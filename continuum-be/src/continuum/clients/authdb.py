"""Storage for users, sessions and API keys, over SQLAlchemy.

A thin adapter, like `qdrant.py`: it stores and fetches rows and makes no policy
decision. Hashing, expiry and lockout live in `services/auth.py`. Rows are
converted to the pure models in `models/auth.py` here, at the boundary, so no
SQLAlchemy object — and no password hash, except through the one method that
exists to return it — leaves this module.

Postgres in every deployment; SQLite (in memory) in the tests. The schema is
Alembic's (`db/migrations`), never created from here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from continuum.db.models import ApiKeyRow, SessionRow, UserRow
from continuum.models.auth import ApiKey, User


class DuplicateRecord(Exception):
    """A unique email, user id or key hash already exists."""


def _user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        is_admin=row.is_admin,
        disabled=row.disabled,
        created_at=row.created_at,
    )


def _key(row: ApiKeyRow) -> ApiKey:
    return ApiKey(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class AuthDB:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        # expire_on_commit=False: rows are converted to domain models after the
        # transaction commits, and must not trigger a lazy reload to do so.
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - health reports down, never raises
            return False

    # --- Users -------------------------------------------------------------

    async def count_users(self) -> int:
        async with self.sessions() as s:
            return int(await s.scalar(select(func.count()).select_from(UserRow)) or 0)

    async def insert_user(self, user: User, password_hash: str, *, only_if_first: bool) -> bool:
        """Insert a user. With `only_if_first`, succeed only while no user exists.

        Returns False when `only_if_first` found an existing user. A duplicate
        email or id raises `DuplicateRecord`.

        "Only if first" must hold when two setups race. SQLite gets that from its
        single writer. Postgres at READ COMMITTED does not: both transactions
        would see an empty table and both insert, leaving two admins. The table
        lock makes the second wait for the first to commit, then see its row.
        """
        try:
            async with self.sessions.begin() as s:
                if only_if_first:
                    if self.engine.dialect.name == "postgresql":
                        await s.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
                    if await s.scalar(select(UserRow.id).limit(1)) is not None:
                        return False
                s.add(
                    UserRow(
                        id=user.id,
                        email=user.email,
                        password_hash=password_hash,
                        is_admin=user.is_admin,
                        disabled=user.disabled,
                        created_at=user.created_at,
                    )
                )
        except IntegrityError as exc:
            raise DuplicateRecord(str(exc.orig)) from exc
        return True

    async def get_user(self, user_id: str) -> User | None:
        async with self.sessions() as s:
            row = await s.get(UserRow, user_id)
        return _user(row) if row else None

    async def get_user_with_hash(self, email: str) -> tuple[User, str] | None:
        async with self.sessions() as s:
            row = await s.scalar(select(UserRow).where(UserRow.email == email))
        return (_user(row), row.password_hash) if row else None

    async def get_password_hash(self, user_id: str) -> str | None:
        async with self.sessions() as s:
            return await s.scalar(select(UserRow.password_hash).where(UserRow.id == user_id))

    async def list_users(self) -> list[User]:
        async with self.sessions() as s:
            rows = await s.scalars(select(UserRow).order_by(UserRow.created_at))
            return [_user(r) for r in rows]

    async def update_user(
        self, user_id: str, *, disabled: bool | None = None, is_admin: bool | None = None
    ) -> None:
        values: dict[str, bool] = {}
        if disabled is not None:
            values["disabled"] = disabled
        if is_admin is not None:
            values["is_admin"] = is_admin
        if not values:
            return
        async with self.sessions.begin() as s:
            await s.execute(update(UserRow).where(UserRow.id == user_id).values(**values))

    async def set_password_hash(self, user_id: str, password_hash: str) -> None:
        async with self.sessions.begin() as s:
            await s.execute(
                update(UserRow).where(UserRow.id == user_id).values(password_hash=password_hash)
            )

    async def count_active_admins(self) -> int:
        async with self.sessions() as s:
            count = await s.scalar(
                select(func.count())
                .select_from(UserRow)
                .where(UserRow.is_admin.is_(True), UserRow.disabled.is_(False))
            )
        return int(count or 0)

    # --- Sessions ----------------------------------------------------------

    async def insert_session(
        self, token_hash: str, user_id: str, created_at: datetime, expires_at: datetime
    ) -> None:
        async with self.sessions.begin() as s:
            s.add(
                SessionRow(
                    token_hash=token_hash,
                    user_id=user_id,
                    created_at=created_at,
                    expires_at=expires_at,
                )
            )

    async def get_session(self, token_hash: str) -> tuple[str, datetime] | None:
        async with self.sessions() as s:
            row = await s.get(SessionRow, token_hash)
        return (row.user_id, row.expires_at) if row else None

    async def delete_session(self, token_hash: str) -> None:
        async with self.sessions.begin() as s:
            await s.execute(delete(SessionRow).where(SessionRow.token_hash == token_hash))

    async def delete_sessions_for_user(self, user_id: str, *, keep: str | None = None) -> None:
        statement = delete(SessionRow).where(SessionRow.user_id == user_id)
        if keep is not None:
            statement = statement.where(SessionRow.token_hash != keep)
        async with self.sessions.begin() as s:
            await s.execute(statement)

    async def purge_expired_sessions(self, now: datetime) -> int:
        async with self.sessions.begin() as s:
            result = await s.execute(delete(SessionRow).where(SessionRow.expires_at < now))
        return result.rowcount

    # --- API keys ----------------------------------------------------------

    async def insert_api_key(self, key: ApiKey, key_hash: str) -> None:
        try:
            async with self.sessions.begin() as s:
                s.add(
                    ApiKeyRow(
                        id=key.id,
                        user_id=key.user_id,
                        name=key.name,
                        prefix=key.prefix,
                        key_hash=key_hash,
                        created_at=key.created_at,
                    )
                )
        except IntegrityError as exc:
            raise DuplicateRecord(str(exc.orig)) from exc

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        async with self.sessions() as s:
            row = await s.scalar(select(ApiKeyRow).where(ApiKeyRow.key_hash == key_hash))
        return _key(row) if row else None

    async def touch_api_key(self, key_id: str, when: datetime) -> None:
        async with self.sessions.begin() as s:
            await s.execute(
                update(ApiKeyRow).where(ApiKeyRow.id == key_id).values(last_used_at=when)
            )

    async def list_api_keys(self, user_id: str) -> list[ApiKey]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ApiKeyRow)
                .where(ApiKeyRow.user_id == user_id)
                .order_by(ApiKeyRow.created_at.desc())
            )
            return [_key(r) for r in rows]

    async def revoke_api_key(self, key_id: str, user_id: str, when: datetime) -> bool:
        """Mark a key revoked. Kept, not deleted, so "last used" stays auditable."""
        async with self.sessions.begin() as s:
            result = await s.execute(
                update(ApiKeyRow)
                .where(
                    ApiKeyRow.id == key_id,
                    ApiKeyRow.user_id == user_id,
                    ApiKeyRow.revoked_at.is_(None),
                )
                .values(revoked_at=when)
            )
        return result.rowcount == 1
