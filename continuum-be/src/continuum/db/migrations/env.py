"""Alembic environment.

Two ways in:

- The API's startup (`db.engine.migrate`) passes an open connection in
  `config.attributes["connection"]`, already inside the migration lock and the
  running event loop. It is used as-is.
- The CLI (`uv run alembic ...` from continuum-be/) has no connection, so one is
  opened here from DATABASE_URL — the same setting the API uses, so the CLI
  can never migrate a different database than the one the app talks to.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from continuum.config import get_settings
from continuum.db.engine import make_engine
from continuum.db.models import Base, UTCDateTime

config = context.config
target_metadata = Base.metadata

# Only the CLI has an ini file. The API configures its own logging (structlog)
# and must not have it replaced mid-startup by alembic.ini's handlers.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Write app-specific column types into migrations as plain SQLAlchemy types.

    A migration is frozen history: it must not import application code that can
    change or disappear later. UTCDateTime's storage is timestamptz, so that is
    what the migration records; the UTC normalisation is Python-side only.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False  # everything else: Alembic's default rendering


def run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
        # SQLite cannot ALTER most things in place; batch mode rebuilds the table
        # instead, so the same migration runs in tests and against Postgres.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_from_settings() -> None:
    engine = make_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run)
            await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    # `alembic upgrade head --sql`: emit the DDL for review instead of running it.
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    shared = config.attributes.get("connection")
    if shared is not None:
        run(shared)
    else:
        asyncio.run(run_from_settings())
