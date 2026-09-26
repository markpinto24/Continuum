"""Table definitions — the single source Alembic compares migrations against.

Rows here are storage shapes, not the domain: `clients/authdb.py` converts them
to the pure models in `models/auth.py` at the boundary, so nothing outside the
adapter ever holds a SQLAlchemy object (or a password hash).

Changing a table means changing this file AND adding a migration:

    uv run alembic revision --autogenerate -m "what changed"

`test_migrations_match_the_models` fails if the two drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, MetaData, String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Deterministic constraint names. Without them Postgres invents names, and a
# later migration that drops or alters a constraint cannot refer to it portably.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware UTC in Python.

    Postgres stores `timestamptz` and hands back aware values; SQLite (the test
    database) has no timezone type and hands back naive ones. Normalising here
    means expiry checks like `expires_at <= now()` behave identically on both,
    instead of raising "can't compare offset-naive and offset-aware" in tests
    only — or, worse, in production only.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given to a UTC column; attach a timezone")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class UserRow(Base):
    __tablename__ = "users"

    # The memory owner id (Qdrant payload `user_id`). Never the email.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SessionRow(Base):
    __tablename__ = "sessions"

    # SHA-256 of the cookie value. The cookie itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    # SHA-256 of the key. Unique, and the lookup path for every agent request.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_api_keys_user_created", "user_id", "created_at"),)
