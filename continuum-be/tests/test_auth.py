"""Authentication and isolation.

Mostly failure modes: the interesting property of an auth layer is what it
refuses. Runs the real router, real AuthService and a real SQLite store
(in memory); only the memory store and ingest are stubbed.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from continuum.api.router import api_router
from continuum.clients.authdb import AuthDB
from continuum.config import Settings, get_settings
from continuum.db.engine import make_engine, migrate
from continuum.models.memory import Memory, MemoryCategory, MemoryStatus
from continuum.models.schemas import IngestResponse
from continuum.services.auth import AlreadyExists, AuthService
from continuum.services.limits import LoginThrottle, SlidingWindow

PASSWORD = "correct horse battery"
BROWSER = {"x-continuum-client": "web"}


class FakeMemories:
    """Just enough of MemoryStore for the ownership checks."""

    def __init__(self) -> None:
        self.items: dict[str, Memory] = {}

    def add(self, user_id: str, content: str = "Atlas uses Postgres") -> Memory:
        memory = Memory(user_id=user_id, content=content, category=MemoryCategory.DECISION)
        self.items[memory.id] = memory
        return memory

    async def get(self, memory_id: str) -> Memory | None:
        return self.items.get(memory_id)

    async def save(self, memory: Memory) -> Memory:
        self.items[memory.id] = memory
        return memory

    async def reinforce(self, memory: Memory, amount: float = 0.05) -> Memory:
        memory.reinforce(amount)
        return await self.save(memory)

    async def list_all(self, *, user_id: str, **_: object) -> list[Memory]:
        return [m for m in self.items.values() if m.user_id == user_id]


class FakeIngest:
    def __init__(self) -> None:
        self.users: list[str] = []

    async def ingest(self, request) -> IngestResponse:  # noqa: ANN001
        self.users.append(request.user_id)
        return IngestResponse(source_id="s", extracted=0)


class App:
    def __init__(self, app: FastAPI, auth: AuthService, settings: Settings) -> None:
        self.app, self.auth, self.settings = app, auth, settings
        self.memories: FakeMemories = app.state.memories
        self.ingest: FakeIngest = app.state.ingest

    def client(self, **kwargs: object) -> httpx.AsyncClient:
        # A crash inside a route comes back as a 500, so an unprotected route
        # fails the guard test with its name rather than with a traceback.
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://testserver", **kwargs)

    async def user(self, email: str, *, user_id: str | None = None, admin: bool = False):
        return await self.auth.create_user(email, PASSWORD, user_id=user_id, is_admin=admin)

    async def signed_in(self, email: str) -> httpx.AsyncClient:
        """A fresh, unopened client carrying a session cookie, like a signed-in tab."""
        async with self.client() as login:
            response = await login.post(
                "/auth/login", json={"email": email, "password": PASSWORD}, headers=BROWSER
            )
            assert response.status_code == 200, response.text
            cookies = dict(login.cookies)
        return self.client(cookies=cookies, headers=BROWSER)


_OPEN: list[AsyncEngine] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    """aiosqlite runs each connection on a worker thread; one left open keeps the
    test process alive after a failure, which looks like a hang, not a failure."""
    yield
    while _OPEN:
        await _OPEN.pop().dispose()


async def migrated_engine(url: str = "sqlite+aiosqlite://") -> AsyncEngine:
    """A fresh database with the REAL migrations applied — so every test run
    also checks that the Alembic scripts build a schema the adapter works with."""
    engine = make_engine(Settings(_env_file=None, database_url=url))
    await migrate(engine)
    _OPEN.append(engine)
    return engine


async def build(**overrides: object) -> App:
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite://", **overrides)
    db = AuthDB(await migrated_engine())
    # Cheapest argon2 parameters: these tests check behaviour, not hash strength.
    auth = AuthService(
        db, settings, hasher=PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.auth = auth
    app.state.llm_limiter = SlidingWindow(settings.llm_requests_per_minute, 60)
    app.state.memories = FakeMemories()
    app.state.ingest = FakeIngest()
    return App(app, auth, settings)


@pytest.fixture
async def env() -> App:
    return await build()


# --- The guard on every route -------------------------------------------------

PUBLIC = {
    ("GET", "/health"),
    ("GET", "/auth/status"),
    ("POST", "/auth/setup"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
}


async def test_every_route_but_the_public_ones_refuses_an_anonymous_request(env):
    """A new endpoint that forgets `CurrentUser` fails here, not in production.

    Walks the OpenAPI schema rather than `app.routes`: it is the public list of
    every endpoint, however the routers happen to be mounted.
    """
    checked = 0
    async with env.client() as client:
        for path, operations in env.app.openapi()["paths"].items():
            for method in operations:
                method = method.upper()
                if (method, path) in PUBLIC:
                    continue
                url = re.sub(r"\{[^}]+\}", "x", path)
                response = await client.request(method, url, json={})
                assert response.status_code == 401, f"{method} {path} is not protected"
                checked += 1
    assert checked >= 15  # the walk really ran over the API


# --- First-run setup ----------------------------------------------------------


async def test_setup_creates_the_first_admin_once(env):
    async with env.client() as client:
        assert (await client.get("/auth/status")).json()["needs_setup"] is True

        body = {"email": "Mark@Example.com", "password": PASSWORD, "user_id": "mark"}
        first = await client.post("/auth/setup", json=body, headers=BROWSER)
        assert first.status_code == 201
        assert first.json() == {
            "user_id": "mark", "email": "mark@example.com", "is_admin": True, "via": "session"
        }
        cookie = first.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie

        again = await client.post(
            "/auth/setup", json={**body, "email": "x@example.com"}, headers=BROWSER
        )
        assert again.status_code == 409
        assert (await client.get("/auth/status")).json()["needs_setup"] is False


async def test_setup_can_be_disabled_for_exposed_deployments():
    env = await build(auth_allow_web_setup=False)
    async with env.client() as client:
        response = await client.post(
            "/auth/setup", json={"email": "a@example.com", "password": PASSWORD}, headers=BROWSER
        )
    assert response.status_code == 409


async def test_env_bootstrap_adopts_existing_memories():
    env = await build(admin_email="mark@example.com", admin_password=PASSWORD, admin_user_id="mark")
    user = await env.auth.bootstrap_from_settings()
    assert user is not None and user.id == "mark" and user.is_admin
    assert await env.auth.bootstrap_from_settings() is None  # idempotent


async def test_a_new_user_never_inherits_a_graph_by_name(env):
    """Ids are random unless asked for, so 'mark@elsewhere' cannot get mark's memories."""
    user = await env.user("mark@elsewhere.com")
    assert user.id != "mark" and len(user.id) == 32


async def test_weak_passwords_and_bad_ids_are_refused(env):
    async with env.client() as client:
        short = await client.post(
            "/auth/setup", json={"email": "a@example.com", "password": "short"}, headers=BROWSER
        )
        bad_id = await client.post(
            "/auth/setup",
            json={"email": "a@example.com", "password": PASSWORD, "user_id": "../etc"},
            headers=BROWSER,
        )
    assert short.status_code == 422
    assert bad_id.status_code == 422


# --- Sign-in ------------------------------------------------------------------


async def test_wrong_password_and_unknown_email_look_identical(env):
    await env.user("mark@example.com")
    async with env.client() as client:
        wrong = await client.post(
            "/auth/login", json={"email": "mark@example.com", "password": "nope-nope-nope"},
            headers=BROWSER,
        )
        unknown = await client.post(
            "/auth/login", json={"email": "who@example.com", "password": PASSWORD},
            headers=BROWSER,
        )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_repeated_failures_lock_the_account_even_for_the_right_password():
    env = await build(login_max_failures=3)
    await env.user("mark@example.com")
    async with env.client() as client:
        for _ in range(3):
            await client.post(
                "/auth/login", json={"email": "mark@example.com", "password": "wrong-wrong"},
                headers=BROWSER,
            )
        locked = await client.post(
            "/auth/login", json={"email": "mark@example.com", "password": PASSWORD},
            headers=BROWSER,
        )
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


async def test_a_disabled_account_cannot_sign_in(env):
    await env.user("admin@example.com", admin=True)
    user = await env.user("raj@example.com")
    await env.auth.set_user_state(user.id, disabled=True)
    async with env.client() as client:
        response = await client.post(
            "/auth/login", json={"email": "raj@example.com", "password": PASSWORD},
            headers=BROWSER,
        )
    assert response.status_code == 401


async def test_sign_in_without_the_browser_header_is_refused(env):
    """Login CSRF: a hostile page must not sign a visitor into the attacker's account."""
    await env.user("mark@example.com")
    async with env.client() as client:
        response = await client.post(
            "/auth/login", json={"email": "mark@example.com", "password": PASSWORD}
        )
    assert response.status_code == 403


# --- Sessions -----------------------------------------------------------------


async def test_session_round_trip_and_logout(env):
    await env.user("mark@example.com", user_id="mark")
    client = await env.signed_in("mark@example.com")
    async with client:
        assert (await client.get("/auth/me")).json()["user_id"] == "mark"
        assert (await client.post("/auth/logout")).status_code == 204
        assert (await client.get("/auth/me")).status_code == 401


async def test_a_cookie_request_without_the_browser_header_is_refused(env):
    await env.user("mark@example.com")
    client = await env.signed_in("mark@example.com")
    async with client:
        del client.headers["x-continuum-client"]
        assert (await client.get("/auth/keys")).status_code == 200  # reads are fine
        forged = await client.post("/auth/keys", json={"name": "evil"})
    assert forged.status_code == 403


async def test_an_expired_session_is_refused():
    env = await build(session_ttl_hours=0)
    await env.user("mark@example.com")
    client = await env.signed_in("mark@example.com")
    async with client:
        assert (await client.get("/auth/me")).status_code == 401


async def test_changing_password_signs_out_other_browsers(env):
    await env.user("mark@example.com")
    laptop = await env.signed_in("mark@example.com")
    phone = await env.signed_in("mark@example.com")
    async with laptop, phone:
        changed = await laptop.post(
            "/auth/password",
            json={"current_password": PASSWORD, "new_password": "a brand new passphrase"},
        )
        assert changed.status_code == 204
        assert (await laptop.get("/auth/me")).status_code == 200
        assert (await phone.get("/auth/me")).status_code == 401


async def test_disabling_a_user_ends_their_sessions(env):
    await env.user("admin@example.com", admin=True)
    raj = await env.user("raj@example.com")
    client = await env.signed_in("raj@example.com")
    async with client:
        await env.auth.set_user_state(raj.id, disabled=True)
        assert (await client.get("/auth/me")).status_code == 401


# --- API keys -----------------------------------------------------------------


async def test_api_key_lifecycle(env):
    await env.user("mark@example.com", user_id="mark")
    browser = await env.signed_in("mark@example.com")
    async with browser, env.client() as agent:
        created = (await browser.post("/auth/keys", json={"name": "laptop agent"})).json()
        secret = created["secret"]
        assert secret.startswith("ck_") and created["key"]["prefix"] == secret[:11]

        auth = {"Authorization": f"Bearer {secret}"}
        me = (await agent.get("/auth/me", headers=auth)).json()
        assert me == {
            "user_id": "mark", "email": "mark@example.com", "is_admin": False, "via": "api_key"
        }

        assert (await browser.delete(f"/auth/keys/{created['key']['id']}")).status_code == 204
        assert (await agent.get("/auth/me", headers=auth)).status_code == 401

        listed = (await browser.get("/auth/keys")).json()["keys"]
        assert listed[0]["revoked_at"] is not None  # kept for the record
        assert "secret" not in listed[0]


async def test_an_api_key_cannot_manage_credentials(env):
    """A leaked agent key must not be able to mint more keys or change the password."""
    user = await env.user("mark@example.com")
    _, secret = await env.auth.create_api_key(user.id, "agent")
    auth = {"Authorization": f"Bearer {secret}"}
    async with env.client() as agent:
        more = await agent.post("/auth/keys", json={"name": "more"}, headers=auth)
        assert more.status_code == 403
        assert (await agent.get("/auth/keys", headers=auth)).status_code == 403
        password = await agent.post(
            "/auth/password",
            json={"current_password": PASSWORD, "new_password": "attacker chosen pw"},
            headers=auth,
        )
        assert password.status_code == 403


async def test_an_admins_api_key_cannot_use_admin_endpoints(env):
    admin = await env.user("admin@example.com", admin=True)
    _, secret = await env.auth.create_api_key(admin.id, "agent")
    async with env.client() as agent:
        response = await agent.get("/admin/users", headers={"Authorization": f"Bearer {secret}"})
    assert response.status_code == 403


async def test_a_disabled_users_keys_stop_working(env):
    await env.user("admin@example.com", admin=True)
    raj = await env.user("raj@example.com")
    _, secret = await env.auth.create_api_key(raj.id, "agent")
    await env.auth.set_user_state(raj.id, disabled=True)
    async with env.client() as agent:
        response = await agent.get("/auth/me", headers={"Authorization": f"Bearer {secret}"})
    assert response.status_code == 401


@pytest.mark.parametrize("header", ["Basic abc", "Bearer ", "Bearer ck_not-a-real-key", "ck_x"])
async def test_malformed_or_unknown_keys_are_refused(env, header):
    async with env.client() as client:
        response = await client.get("/auth/me", headers={"Authorization": header})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_the_header_wins_over_a_cookie(env):
    """A browser's stale cookie must never change whose graph a key writes to."""
    await env.user("mark@example.com", user_id="mark")
    raj = await env.user("raj@example.com", user_id="raj")
    _, raj_key = await env.auth.create_api_key(raj.id, "agent")
    browser = await env.signed_in("mark@example.com")
    async with browser:
        me = await browser.get("/auth/me", headers={"Authorization": f"Bearer {raj_key}"})
    assert me.json()["user_id"] == "raj"


# --- Isolation: the point of all of it ------------------------------------------


async def test_a_memory_id_does_not_open_someone_elses_memory(env):
    await env.user("mark@example.com", user_id="mark")
    await env.user("raj@example.com", user_id="raj")
    marks = env.memories.add("mark")
    raj = await env.signed_in("raj@example.com")
    mark = await env.signed_in("mark@example.com")
    async with raj, mark:
        for method, path in [
            ("GET", f"/memories/{marks.id}"),
            ("POST", f"/memories/{marks.id}/reinforce"),
            ("POST", f"/memories/{marks.id}/reactivate"),
        ]:
            # 404, not 403: a different status would confirm the id exists.
            assert (await raj.request(method, path)).status_code == 404, path
        assert (await mark.get(f"/memories/{marks.id}")).status_code == 200

    marks.status = MemoryStatus.ACTIVE
    assert env.memories.items[marks.id].confidence == marks.confidence  # raj changed nothing


async def test_writes_go_to_the_signed_in_user_whatever_the_body_says(env):
    await env.user("raj@example.com", user_id="raj")
    raj = await env.signed_in("raj@example.com")
    async with raj:
        spoofed = await raj.post("/ingest", json={"user_id": "mark", "text": "hello"})
        honest = await raj.post("/ingest", json={"text": "hello"})
    assert spoofed.status_code == 422
    assert "user_id is not accepted" in spoofed.text
    assert honest.status_code == 201
    assert env.ingest.users == ["raj"]


async def test_oversized_input_is_refused_before_any_llm_call():
    env = await build(max_input_chars=10)
    await env.user("mark@example.com")
    client = await env.signed_in("mark@example.com")
    async with client:
        response = await client.post("/ingest", json={"text": "x" * 11})
    assert response.status_code == 413
    assert env.ingest.users == []


async def test_llm_endpoints_are_rate_limited_per_user():
    env = await build(llm_requests_per_minute=2)
    await env.user("mark@example.com", user_id="mark")
    await env.user("raj@example.com", user_id="raj")
    mark = await env.signed_in("mark@example.com")
    raj = await env.signed_in("raj@example.com")
    async with mark, raj:
        codes = [(await mark.post("/ingest", json={"text": "a"})).status_code for _ in range(3)]
        other = await raj.post("/ingest", json={"text": "a"})
    assert codes == [201, 201, 429]
    assert other.status_code == 201  # one user's limit is not another's


# --- Admin --------------------------------------------------------------------


async def test_only_admins_manage_users(env):
    await env.user("admin@example.com", admin=True)
    await env.user("raj@example.com")
    admin = await env.signed_in("admin@example.com")
    raj = await env.signed_in("raj@example.com")
    async with admin, raj:
        assert (await raj.get("/admin/users")).status_code == 403
        created = await admin.post(
            "/admin/users", json={"email": "new@example.com", "password": PASSWORD}
        )
        assert created.status_code == 201
        duplicate = await admin.post(
            "/admin/users", json={"email": "NEW@example.com", "password": PASSWORD}
        )
        assert duplicate.status_code == 409
        assert len((await admin.get("/admin/users")).json()["users"]) == 3


async def test_the_last_admin_cannot_be_demoted_or_disable_themselves(env):
    admin = await env.user("admin@example.com", admin=True)
    client = await env.signed_in("admin@example.com")
    async with client:
        demote = await client.patch(f"/admin/users/{admin.id}", json={"is_admin": False})
        disable = await client.patch(f"/admin/users/{admin.id}", json={"disabled": True})
    assert demote.status_code == 422
    assert disable.status_code == 422


# --- The limiters, with a fake clock -------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_sliding_window_refuses_then_recovers():
    clock = Clock()
    window = SlidingWindow(2, 60, clock)
    assert window.hit("k") is None and window.hit("k") is None
    assert window.hit("k") == 60
    clock.now += 30
    assert window.hit("k") == 30
    clock.now += 31
    assert window.hit("k") is None


def test_a_successful_sign_in_does_not_reset_the_address_counter():
    """Else one valid login would refill an attacker's budget for every other account."""
    clock = Clock()
    throttle = LoginThrottle(2, 15, clock)
    keys = throttle.keys("victim@example.com", "10.0.0.9")
    throttle.record_failure(keys)
    throttle.record_failure(keys)
    throttle.record_success("attacker@example.com")
    assert throttle.retry_after(throttle.keys("other@example.com", "10.0.0.9")) is not None


async def test_timestamps_come_back_timezone_aware():
    """SQLite has no timezone type. Without UTCDateTime, `expires_at <= now()`
    would raise in the tests while working in Postgres — or the reverse."""
    env = await build()
    user = await env.user("mark@example.com")
    stored = await env.auth.db.get_user(user.id)
    assert stored is not None and stored.created_at.tzinfo is not None
    assert stored.created_at == user.created_at


def test_a_naive_datetime_is_refused_rather_than_guessed():
    from continuum.db.models import UTCDateTime

    with pytest.raises(ValueError, match="naive"):
        UTCDateTime().process_bind_param(datetime(2026, 9, 26, 12, 0), None)  # type: ignore[arg-type]


# --- Migrations ---------------------------------------------------------------


async def test_migrations_match_the_models():
    """A model change without a migration fails here, not on the next deploy."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from continuum.db.models import Base

    engine = await migrated_engine()
    async with engine.connect() as connection:
        diff = await connection.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
        )
    assert diff == [], f"db/models.py and the migrations disagree: {diff}"


async def test_migrating_twice_is_a_no_op():
    """Every API start calls migrate(); the second call must change nothing."""
    engine = await migrated_engine()
    await migrate(engine)
    db = AuthDB(engine)
    assert await db.count_users() == 0


async def test_every_migration_downgrades_cleanly():
    from alembic import command

    from continuum.db.engine import alembic_config

    engine = await migrated_engine()

    def down_and_up(connection) -> None:  # noqa: ANN001
        config = alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        command.upgrade(config, "head")

    async with engine.begin() as connection:
        await connection.run_sync(down_and_up)
    assert await AuthDB(engine).count_users() == 0


# --- Postgres-only behaviour --------------------------------------------------
# SQLite cannot show these: it has a single writer and no timestamptz. Run with
#   TEST_DATABASE_URL=postgresql+asyncpg://continuum:continuum@localhost:5432/continuum_test
# against a database that may be wiped.

PG_URL = os.getenv("TEST_DATABASE_URL")
postgres_only = pytest.mark.skipif(not PG_URL, reason="set TEST_DATABASE_URL to a Postgres test DB")


async def _fresh_postgres() -> AsyncEngine:
    from sqlalchemy import text

    engine = make_engine(Settings(_env_file=None, database_url=PG_URL))
    async with engine.begin() as connection:
        # asyncpg sends each statement as its own prepared statement: one each.
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await migrate(engine)
    _OPEN.append(engine)
    return engine


@postgres_only
async def test_two_racing_setups_create_exactly_one_admin_on_postgres():
    """READ COMMITTED would let both see an empty table and both insert; the
    table lock in insert_user is what makes 'only the first' true."""
    import asyncio

    from continuum.services.auth import SetupClosed

    engine = await _fresh_postgres()
    settings = Settings(_env_file=None, database_url=PG_URL)
    hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    services = [AuthService(AuthDB(engine), settings, hasher=hasher) for _ in range(8)]

    results = await asyncio.gather(
        *(s.setup_first_admin(f"admin{i}@example.com", PASSWORD) for i, s in enumerate(services)),
        return_exceptions=True,
    )

    created = [r for r in results if not isinstance(r, Exception)]
    assert len(created) == 1
    assert all(isinstance(r, SetupClosed) for r in results if isinstance(r, Exception))
    assert await AuthDB(engine).count_users() == 1


@postgres_only
async def test_the_whole_auth_flow_on_postgres():
    engine = await _fresh_postgres()
    settings = Settings(_env_file=None, database_url=PG_URL)
    auth = AuthService(
        AuthDB(engine), settings, hasher=PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    user = await auth.create_user("mark@example.com", PASSWORD, user_id="mark", is_admin=True)
    with pytest.raises(AlreadyExists):
        await auth.create_user("MARK@example.com", PASSWORD)

    token, _ = await auth.create_session(await auth.authenticate_password(
        "mark@example.com", PASSWORD, "10.0.0.1"
    ))
    assert (await auth.principal_from_session(token)).user_id == "mark"

    key, secret = await auth.create_api_key(user.id, "agent")
    assert (await auth.principal_from_api_key(secret)).api_key_id == key.id
    await auth.revoke_api_key(user.id, key.id)
    assert await auth.principal_from_api_key(secret) is None
    assert (await auth.list_api_keys(user.id))[0].revoked_at.tzinfo is not None

    await auth.change_password(user.id, PASSWORD, "a new long passphrase", current_session=token)
    assert await auth.principal_from_session(token) is not None
