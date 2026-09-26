"""Accounts, sign-in, sessions and API keys.

Two credentials, one outcome. A person signs in to the web UI and gets a session
cookie; an agent is given an API key. Both resolve to the same `Principal`, and
`Principal.user_id` is the only source of a memory owner id anywhere in the API —
nothing a request body says about who it is for is ever believed.

Secrets are never stored. Passwords are argon2id hashes (slow on purpose: they
are low-entropy and guessable). Session tokens and API keys are 256-bit random
values stored as SHA-256 — already unguessable, so a fast hash loses nothing and
lets a request be authenticated with one indexed lookup.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from continuum.clients.authdb import AuthDB, DuplicateRecord
from continuum.config import Settings
from continuum.core.logger import get_logger
from continuum.models.auth import (
    USER_ID_PATTERN,
    ApiKey,
    Principal,
    User,
    is_valid_email,
    normalise_email,
)
from continuum.services.limits import LoginThrottle

log = get_logger(__name__)

API_KEY_PREFIX = "ck_"
# Shown in key lists so a person can tell their keys apart. 8 random characters
# after the marker: enough to recognise, far too few to help a guess.
_DISPLAY_PREFIX_LEN = len(API_KEY_PREFIX) + 8
# argon2 cost grows with input length; cap it so a megabyte "password" cannot be
# used to burn CPU on the sign-in endpoint.
_MAX_PASSWORD_LENGTH = 1024
# last_used_at is informational. Writing it on every request would turn every
# authenticated read into a disk write.
_TOUCH_INTERVAL = timedelta(minutes=1)


class AuthError(Exception):
    """Base for every refusal. `status` is the HTTP code the route should send."""

    status = 400


class InvalidCredentials(AuthError):
    status = 401


class LockedOut(AuthError):
    status = 429

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Too many failed sign-ins. Try again in {retry_after}s.")
        self.retry_after = retry_after


class SetupClosed(AuthError):
    status = 409


class InvalidInput(AuthError):
    status = 422


class AlreadyExists(AuthError):
    status = 409


class NotFound(AuthError):
    status = 404


def _now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(
        self,
        db: AuthDB,
        settings: Settings,
        *,
        hasher: PasswordHasher | None = None,
        throttle: LoginThrottle | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.hasher = hasher or PasswordHasher()
        self.throttle = throttle or LoginThrottle(
            settings.login_max_failures, settings.login_lockout_minutes
        )
        # Verified against when the email is unknown, so a miss costs the same
        # time as a wrong password and response timing does not reveal which
        # emails have accounts.
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(16))

    # --- Password hashing, off the event loop ------------------------------

    async def _hash(self, password: str) -> str:
        return await asyncio.to_thread(self.hasher.hash, password)

    async def _verify(self, password_hash: str, password: str) -> bool:
        def verify() -> bool:
            try:
                return self.hasher.verify(password_hash, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False

        return await asyncio.to_thread(verify)

    def _check_password_rules(self, password: str) -> None:
        if len(password) < self.settings.password_min_length:
            raise InvalidInput(
                f"Password must be at least {self.settings.password_min_length} characters."
            )
        if len(password) > _MAX_PASSWORD_LENGTH:
            raise InvalidInput("Password is too long.")

    # --- Accounts ----------------------------------------------------------

    async def needs_setup(self) -> bool:
        return await self.db.count_users() == 0

    async def create_user(
        self,
        email: str,
        password: str,
        *,
        user_id: str | None = None,
        is_admin: bool = False,
        only_if_first: bool = False,
    ) -> User:
        email = normalise_email(email)
        if not is_valid_email(email):
            raise InvalidInput("That does not look like an email address.")
        self._check_password_rules(password)
        # A fresh random id unless one is asked for. Asking for one is how an
        # admin adopts memories stored before authentication existed — which is
        # also why it is never derived from the email: a new account must not
        # silently inherit a graph that happens to share its name.
        user_id = (user_id or uuid.uuid4().hex).strip().lower()
        if not USER_ID_PATTERN.match(user_id):
            raise InvalidInput(
                "User id must be 1-64 characters: lowercase letters, digits, '.', '_' or '-'."
            )

        user = User(id=user_id, email=email, is_admin=is_admin, created_at=_now())
        try:
            created = await self.db.insert_user(
                user, await self._hash(password), only_if_first=only_if_first
            )
        except DuplicateRecord as exc:
            raise AlreadyExists("An account with that email or user id already exists.") from exc
        if not created:
            raise SetupClosed("Setup is already complete. Sign in instead.")
        log.info("auth.user_created", user_id=user.id, is_admin=is_admin)
        return user

    async def setup_first_admin(
        self, email: str, password: str, user_id: str | None = None
    ) -> User:
        """Create the first account from the web UI. Refused once any account exists."""
        if not self.settings.auth_allow_web_setup:
            raise SetupClosed(
                "Web setup is disabled. Set ADMIN_EMAIL and ADMIN_PASSWORD and restart."
            )
        return await self.create_user(
            email, password, user_id=user_id, is_admin=True, only_if_first=True
        )

    async def bootstrap_from_settings(self) -> User | None:
        """Create the first admin from ADMIN_EMAIL / ADMIN_PASSWORD, if set and needed."""
        s = self.settings
        if not (s.admin_email and s.admin_password):
            if await self.needs_setup():
                log.warning(
                    "auth.no_accounts",
                    hint="open the web UI to create the first admin, or set "
                    "ADMIN_EMAIL and ADMIN_PASSWORD",
                    web_setup=s.auth_allow_web_setup,
                )
            return None
        if not await self.needs_setup():
            return None
        try:
            return await self.create_user(
                s.admin_email,
                s.admin_password,
                user_id=s.admin_user_id,
                is_admin=True,
                only_if_first=True,
            )
        except SetupClosed:
            return None  # another process won the race; nothing to do

    async def list_users(self) -> list[User]:
        return await self.db.list_users()

    async def set_user_state(
        self, user_id: str, *, disabled: bool | None = None, is_admin: bool | None = None
    ) -> User:
        user = await self.db.get_user(user_id)
        if not user:
            raise NotFound("No such user.")
        losing_admin = user.is_admin and not user.disabled and (
            disabled is True or is_admin is False
        )
        if losing_admin and await self.db.count_active_admins() <= 1:
            # Otherwise the last admin can lock everyone out of user management,
            # with no way back short of editing the database by hand.
            raise InvalidInput("That would leave no active admin.")
        await self.db.update_user(user_id, disabled=disabled, is_admin=is_admin)
        if disabled:
            await self.db.delete_sessions_for_user(user_id)
        log.info("auth.user_updated", user_id=user_id, disabled=disabled, is_admin=is_admin)
        updated = await self.db.get_user(user_id)
        assert updated is not None
        return updated

    # --- Sign-in and sessions ----------------------------------------------

    async def authenticate_password(
        self, email: str, password: str, client: str | None
    ) -> User:
        email = normalise_email(email)
        keys = self.throttle.keys(email, client)
        wait = self.throttle.retry_after(keys)
        if wait is not None:
            log.warning("auth.login_locked_out", retry_after=wait)
            raise LockedOut(wait)

        found = await self.db.get_user_with_hash(email) if password else None
        if found is None:
            await self._verify(self._dummy_hash, password or "x")  # equalise timing
            ok, user, stored = False, None, None
        else:
            user, stored = found
            ok = len(password) <= _MAX_PASSWORD_LENGTH and await self._verify(stored, password)

        # A disabled account fails exactly like a wrong password: the sign-in
        # form should not confirm which accounts exist.
        if not ok or user is None or user.disabled:
            self.throttle.record_failure(keys)
            log.info("auth.login_failed")
            raise InvalidCredentials("Wrong email or password.")

        self.throttle.record_success(email)
        if stored and self.hasher.check_needs_rehash(stored):
            await self.db.set_password_hash(user.id, await self._hash(password))
        log.info("auth.login_succeeded", user_id=user.id)
        return user

    async def create_session(self, user: User) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(hours=self.settings.session_ttl_hours)
        await self.db.insert_session(hash_token(token), user.id, now, expires)
        # Opportunistic cleanup; there is no scheduler job for something this small.
        await self.db.purge_expired_sessions(now)
        return token, expires

    async def end_session(self, token: str) -> None:
        await self.db.delete_session(hash_token(token))

    async def principal_from_session(self, token: str) -> Principal | None:
        found = await self.db.get_session(hash_token(token))
        if not found:
            return None
        user_id, expires_at = found
        if expires_at <= _now():
            await self.db.delete_session(hash_token(token))
            return None
        user = await self.db.get_user(user_id)
        if not user or user.disabled:
            return None
        return Principal(user_id=user.id, email=user.email, is_admin=user.is_admin, via="session")

    async def change_password(
        self, user_id: str, current: str, new: str, *, current_session: str | None
    ) -> None:
        stored = await self.db.get_password_hash(user_id)
        if not stored or not await self._verify(stored, current):
            raise InvalidCredentials("Current password is wrong.")
        self._check_password_rules(new)
        await self.db.set_password_hash(user_id, await self._hash(new))
        # Every other sign-in ends: a password change is often a response to a
        # suspected compromise, and a stolen cookie must not outlive it.
        keep = hash_token(current_session) if current_session else None
        await self.db.delete_sessions_for_user(user_id, keep=keep)
        log.info("auth.password_changed", user_id=user_id)

    # --- API keys ----------------------------------------------------------

    async def create_api_key(self, user_id: str, name: str) -> tuple[ApiKey, str]:
        """Mint a key. The plaintext is returned exactly once and never stored."""
        name = name.strip()
        if not name or len(name) > 100:
            raise InvalidInput("Give the key a name of 1-100 characters.")
        secret = API_KEY_PREFIX + secrets.token_urlsafe(32)
        key = ApiKey(
            id=uuid.uuid4().hex,
            user_id=user_id,
            name=name,
            prefix=secret[:_DISPLAY_PREFIX_LEN],
            created_at=_now(),
        )
        await self.db.insert_api_key(key, hash_token(secret))
        log.info("auth.api_key_created", user_id=user_id, api_key_id=key.id)
        return key, secret

    async def list_api_keys(self, user_id: str) -> list[ApiKey]:
        return await self.db.list_api_keys(user_id)

    async def revoke_api_key(self, user_id: str, key_id: str) -> None:
        if not await self.db.revoke_api_key(key_id, user_id, _now()):
            raise NotFound("No such active key.")
        log.info("auth.api_key_revoked", user_id=user_id, api_key_id=key_id)

    async def principal_from_api_key(self, secret: str) -> Principal | None:
        if not secret.startswith(API_KEY_PREFIX):
            return None
        key = await self.db.get_api_key_by_hash(hash_token(secret))
        if not key or key.revoked_at is not None:
            return None
        user = await self.db.get_user(key.user_id)
        if not user or user.disabled:
            return None
        now = _now()
        if key.last_used_at is None or now - key.last_used_at > _TOUCH_INTERVAL:
            await self.db.touch_api_key(key.id, now)
        return Principal(
            user_id=user.id,
            email=user.email,
            is_admin=user.is_admin,
            via="api_key",
            api_key_id=key.id,
        )
