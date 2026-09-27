"""Storage for resolution labels, over SQLAlchemy. A thin adapter: no policy."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from continuum.db.models import ResolutionLabelRow
from continuum.models.feedback import ResolutionLabel

_FIELDS = tuple(ResolutionLabel.model_fields)


class LabelStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def add_many(self, labels: list[ResolutionLabel]) -> None:
        if not labels:
            return
        async with self.sessions.begin() as s:
            s.add_all(ResolutionLabelRow(**label.model_dump()) for label in labels)

    async def list_for_user(self, user_id: str, *, limit: int = 1000) -> list[ResolutionLabel]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ResolutionLabelRow)
                .where(ResolutionLabelRow.user_id == user_id)
                .order_by(ResolutionLabelRow.created_at.desc())
                .limit(limit)
            )
            return [
                ResolutionLabel.model_validate({f: getattr(r, f) for f in _FIELDS}) for r in rows
            ]
