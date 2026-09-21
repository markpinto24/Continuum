"""Phase 2 — confidence decay.

The failure mode this prevents: a memory recorded once, eighteen months ago,
never contradicted and never mentioned again, still ranking at full confidence
against something you said last week.

Decay is exponential with a per-category half-life (see
`CATEGORY_HALF_LIFE_DAYS`). Constraints go stale fastest — budgets and deadlines
have a shelf life. People change slowest. Events never decay at all, because
history is not a belief that can weaken.

The sweep is **idempotent**: `decayed_confidence` is a pure function of
`confidence` and `last_reinforced_at`, and the sweep only ever lowers the stored
value toward the same target. Running it twice in a day is harmless.

Archiving is not deletion. An archived memory drops out of retrieval, keeps its
edges, and can be reactivated from the API.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from pydantic import BaseModel

from continuum.config import Settings, get_settings
from continuum.models.memory import MemoryStatus
from continuum.services.memory_store import MemoryStore

log = structlog.get_logger(__name__)


class DecayReport(BaseModel):
    scanned: int = 0
    decayed: int = 0
    archived: int = 0
    unchanged: int = 0


class DecayService:
    def __init__(self, memories: MemoryStore, settings: Settings | None = None) -> None:
        self.memories = memories
        self.settings = settings or get_settings()

    async def sweep(self, *, user_id: str, dry_run: bool = False) -> DecayReport:
        now = datetime.now(UTC)
        report = DecayReport()

        candidates = await self.memories.list_all(
            user_id=user_id,
            limit=self.settings.decay_batch_limit,
            statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
        )

        for memory in candidates:
            report.scanned += 1

            new_confidence = memory.decayed_confidence(
                now=now, floor=self.settings.decay_floor
            )
            if new_confidence >= memory.confidence:
                report.unchanged += 1
                continue

            memory.confidence = new_confidence
            report.decayed += 1

            if new_confidence < self.settings.decay_archive_threshold:
                memory.archive()
                report.archived += 1
                log.info(
                    "decay.archived",
                    memory_id=memory.id,
                    category=memory.category,
                    confidence=new_confidence,
                )

            if not dry_run:
                await self.memories.save(memory)

        log.info(
            "decay.sweep_completed",
            user_id=user_id,
            dry_run=dry_run,
            **report.model_dump(),
        )
        return report

    async def sweep_all(self) -> DecayReport:
        """Sweep every user that has memories.

        Deliberately naive: it scrolls distinct user ids. At the scale where that
        stops being acceptable, this job belongs in a worker keyed off a user
        table rather than the vector store.
        """
        totals = DecayReport()
        for user_id in await self.memories.distinct_user_ids():
            report = await self.sweep(user_id=user_id)
            totals.scanned += report.scanned
            totals.decayed += report.decayed
            totals.archived += report.archived
            totals.unchanged += report.unchanged
        return totals
