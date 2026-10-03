"""A person correcting the graph directly: "that is not a fact", "forget this".

Two different acts, deliberately kept apart:

* **Reject** — the extractor misread something (a question recorded as a belief,
  two facts merged into one, words that say something else). The memory is
  archived with the reason, and the mistake becomes a labelled extraction case.
  If the misreading had retired a belief, that belief comes back: a belief lost
  to an extraction error is exactly the loss the project exists to prevent.
* **Forget** — the content must go (it was private, wrong to keep, asked to be
  removed). The record, its id, edges and dates stay so the graph still reads;
  the words leave everywhere Continuum copied them: the memory, its vector, the
  labels, rejection records and answer ratings that quoted it.

Forgetting is the one deliberate exception to "nothing is lost", and it is a
person's explicit act, never the system's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from continuum.clients.labels import LabelStore
from continuum.clients.learning import LearningStore
from continuum.core.logger import get_logger
from continuum.models.feedback import ExtractionFeedback, RejectReason
from continuum.models.memory import Memory, MemoryStatus
from continuum.services.memory_store import MemoryStore

log = get_logger(__name__)


class CorrectionError(Exception):
    status = 409


class CorrectionService:
    def __init__(
        self,
        memories: MemoryStore,
        *,
        labels: LabelStore | None = None,
        learning: LearningStore | None = None,
    ) -> None:
        self.memories = memories
        self.labels = labels
        self.learning = learning

    async def _release_counterparts(self, memory: Memory) -> list[str]:
        """Clear every conflict edge pointing at `memory` from the other side."""
        others = await self.memories.resolve_ids(memory.conflicts_with + memory.team_conflicts_with)
        for other in await self.memories.referencing(memory.id):
            others.setdefault(other.id, other)
        released = []
        for other in others.values():
            if other.id == memory.id:
                continue
            other.clear_conflict_with(memory.id)
            await self.memories.save(other)
            released.append(other.id)
        return released

    async def reject(
        self, memory: Memory, *, user_id: str, reason: RejectReason, note: str | None = None
    ) -> tuple[Memory, list[str]]:
        """Archive a misreading; restore anything it had retired. Returns the
        memory and the ids brought back."""
        if memory.redacted_at:
            raise CorrectionError("That memory was forgotten; there is nothing left to reject.")
        if memory.rejected_reason:
            raise CorrectionError("That memory was already rejected.")

        await self._release_counterparts(memory)
        restored = []
        for old in (await self.memories.resolve_ids(memory.supersedes)).values():
            if old.status is MemoryStatus.SUPERSEDED and old.superseded_by == memory.id:
                old.reactivate()
                await self.memories.save(old)
                restored.append(old.id)

        memory.conflicts_with = []
        memory.team_conflicts_with = []
        memory.rejected_reason = reason.value
        memory.archive()
        await self.memories.save(memory)

        if self.learning is not None:
            try:
                await self.learning.add_rejection(
                    ExtractionFeedback(
                        id=uuid.uuid4().hex,
                        user_id=user_id,
                        created_at=datetime.now(UTC),
                        memory_id=memory.id,
                        content=memory.content,
                        category=memory.category.value,
                        source_excerpt=memory.source_excerpt,
                        reason=reason,
                        note=note,
                    )
                )
            except Exception:  # noqa: BLE001 - the correction itself is applied
                log.exception("correction.record_failed", memory_id=memory.id)
        log.info("correction.rejected", memory_id=memory.id, reason=reason.value,
                 restored=len(restored))
        return memory, restored

    async def forget(self, memory: Memory) -> Memory:
        """Remove a memory's words from everywhere Continuum stored them."""
        if memory.redacted_at:
            return memory
        linked = (
            set(memory.supersedes)
            | set(memory.conflicts_with)
            | set(memory.team_conflicts_with)
            | ({memory.superseded_by} - {None})
        )
        await self._release_counterparts(memory)
        memory.redact()
        # Re-embed: a vector of the original words is itself a trace of them.
        await self.memories.add(memory)

        # Escalation reasons on the other side may paraphrase this memory.
        for other in (await self.memories.resolve_ids(sorted(linked))).values():
            if other.escalation_against(memory.id):
                other.escalations = [
                    e.model_copy(update={"reason": ""}) if e.target_id == memory.id else e
                    for e in other.escalations
                ]
                await self.memories.save(other)

        # A summary written from it repeats its words.
        for summary in await self.memories.summaries_of([memory.id]):
            if not summary.redacted_at:
                summary.redact()
                await self.memories.add(summary)

        if self.labels is not None:
            await self.labels.redact_memory(memory.id)
        if self.learning is not None:
            await self.learning.redact_rejections(memory.id)
            await self.learning.redact_answers_mentioning(memory.id)
        log.info("correction.forgotten", memory_id=memory.id)
        return memory
