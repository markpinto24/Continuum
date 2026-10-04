"""Engine construction and startup migration.

`migrate()` is what keeps `docker-compose -f local.yml up` the whole contract: the
API brings its own schema up to date before it accepts a request, so there is no
separate "run alembic" step for anyone to forget.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from continuum.config import Settings
from continuum.core.logger import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary, fixed. Every API process takes this Postgres advisory lock before
# migrating, so replicas starting together run each migration exactly once
# instead of racing to create the same table.
_MIGRATION_LOCK = 7_265_831_904


def make_engine(settings: Settings) -> AsyncEngine:
    url = settings.database_url
    if url.startswith("sqlite"):
        # Tests. One shared connection, or an in-memory database would be a
        # different empty database on every checkout from the pool.
        engine = create_async_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _foreign_keys(dbapi_connection, _record) -> None:  # noqa: ANN001
            # SQLite ignores ON DELETE CASCADE unless asked, per connection.
            dbapi_connection.execute("PRAGMA foreign_keys = ON")

        return engine

    return create_async_engine(
        url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        # A connection dropped by a Postgres restart is replaced on checkout
        # instead of failing the first request after it.
        pool_pre_ping=True,
    )


def alembic_config() -> Config:
    """Config for programmatic use. The CLI reads alembic.ini instead; both point
    at the same scripts, which ship inside the package so the image has them."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


async def migrate(engine: AsyncEngine) -> None:
    """Upgrade the schema to the latest revision. Safe to call on every start."""

    def upgrade(connection: Connection) -> None:
        if connection.dialect.name == "postgresql":
            # Transaction-scoped: released when the upgrade commits or fails.
            connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _MIGRATION_LOCK})
        config = alembic_config()
        # Hand env.py this connection rather than letting it open its own: the
        # upgrade runs inside the lock, and inside the running event loop.
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    async with engine.begin() as connection:
        await connection.run_sync(upgrade)
    log.info("db.migrated", database=engine.url.render_as_string(hide_password=True))
