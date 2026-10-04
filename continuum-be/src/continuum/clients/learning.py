"""Storage for what people teach Continuum, beyond conflict decisions.

Thin adapters over SQLAlchemy, like `labels.py`: rejected extractions, answer
ratings, and approved resolution rules. No policy lives here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from continuum.db.models import AnswerFeedbackRow, ExtractionFeedbackRow, ResolutionRuleRow
from continuum.models.feedback import AnswerFeedback, ExtractionFeedback, ResolutionRule


def _model(model, row):  # noqa: ANN001, ANN202
    return model.model_validate({f: getattr(row, f) for f in model.model_fields})


class LearningStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    # --- Rejected extractions --------------------------------------------------

    async def add_rejection(self, item: ExtractionFeedback) -> None:
        async with self.sessions.begin() as s:
            s.add(ExtractionFeedbackRow(**item.model_dump()))

    async def rejections(self, user_id: str, *, limit: int = 1000) -> list[ExtractionFeedback]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ExtractionFeedbackRow)
                .where(ExtractionFeedbackRow.user_id == user_id)
                .order_by(ExtractionFeedbackRow.created_at.desc())
                .limit(limit)
            )
            return [_model(ExtractionFeedback, r) for r in rows]

    async def redact_rejections(self, memory_id: str) -> None:
        async with self.sessions.begin() as s:
            await s.execute(
                update(ExtractionFeedbackRow)
                .where(ExtractionFeedbackRow.memory_id == memory_id)
                .values(content="[forgotten]", source_excerpt=None, note=None)
            )

    # --- Answer ratings ----------------------------------------------------------

    async def add_answer(self, item: AnswerFeedback) -> None:
        async with self.sessions.begin() as s:
            s.add(AnswerFeedbackRow(**item.model_dump()))

    async def answers(self, user_id: str, *, limit: int = 1000) -> list[AnswerFeedback]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(AnswerFeedbackRow)
                .where(AnswerFeedbackRow.user_id == user_id)
                .order_by(AnswerFeedbackRow.created_at.desc())
                .limit(limit)
            )
            return [_model(AnswerFeedback, r) for r in rows]

    async def redact_answers_mentioning(self, memory_id: str) -> int:
        """Blank the question and answer of every rating that involved this memory —
        anyone's, since a shared memory turns up in everyone's answers.

        Checked in Python, not SQL: a portable "JSON array contains" query does not
        exist across Postgres and SQLite, and ratings are few.
        """
        async with self.sessions.begin() as s:
            rows = await s.scalars(select(AnswerFeedbackRow))
            hits = [
                r for r in rows
                if memory_id in (r.used_ids or []) + (r.cited_ids or []) + (r.missing_ids or [])
            ]
            for row in hits:
                row.query = "[forgotten]"
                row.answer = "[forgotten]"
                row.note = None
        return len(hits)

    # --- Rules -------------------------------------------------------------------

    async def add_rule(self, rule: ResolutionRule) -> None:
        async with self.sessions.begin() as s:
            s.add(ResolutionRuleRow(**rule.model_dump()))

    async def rules(self, owners: list[str], *, active_only: bool = True) -> list[ResolutionRule]:
        statement = select(ResolutionRuleRow).where(ResolutionRuleRow.owner.in_(owners))
        if active_only:
            statement = statement.where(ResolutionRuleRow.revoked_at.is_(None))
        async with self.sessions() as s:
            rows = await s.scalars(statement.order_by(ResolutionRuleRow.created_at))
            return [_model(ResolutionRule, r) for r in rows]

    async def get_rule(self, rule_id: str) -> ResolutionRule | None:
        async with self.sessions() as s:
            row = await s.get(ResolutionRuleRow, rule_id)
        return _model(ResolutionRule, row) if row else None

    async def revoke_rule(self, rule_id: str, when: datetime) -> None:
        """Revoked, not deleted: which rules governed past decisions stays auditable."""
        async with self.sessions.begin() as s:
            await s.execute(
                update(ResolutionRuleRow)
                .where(ResolutionRuleRow.id == rule_id)
                .values(revoked_at=when)
            )
