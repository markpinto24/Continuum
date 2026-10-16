"""Pending memories: said, not decided (requirements.md Part 2).

A pending item is the honest record of "maybe" — kept, never believed. The
person settles it: yes makes it a belief (judged against the graph only then),
no declines it (kept for audit, never raised again), "still unsure" leaves it
pending. Every answer is a person's button or a spoken word to the question just
asked — never a model reading intent into free text (commitment 2).
"""

from __future__ import annotations

from continuum.core.logger import get_logger
from continuum.models.memory import Memory
from continuum.models.schemas import IngestResponse
from continuum.services.ingest import IngestService
from continuum.services.memory_store import MemoryStore

log = get_logger(__name__)


class PendingError(ValueError):
    """The memory is not pending, so there is nothing to answer."""


class PendingService:
    def __init__(self, ingest: IngestService, memories: MemoryStore) -> None:
        self.ingest = ingest
        self.memories = memories

    async def confirm(self, memory: Memory) -> IngestResponse:
        _require_pending(memory)
        return await self.ingest.confirm_pending(memory)

    async def decline(self, memory: Memory) -> Memory:
        _require_pending(memory)
        memory.decline()
        log.info("pending.declined", memory_id=memory.id)
        return await self.memories.save(memory)

    async def keep(self, memory: Memory) -> Memory:
        """Still unsure: it stays pending, and this answer counts as one reminder,
        so an item nobody decides on still fades in the end."""
        _require_pending(memory)
        memory.remind()
        log.info("pending.kept", memory_id=memory.id, reminders=memory.reminders)
        return await self.memories.save(memory)

    async def reminded(self, memories: list[Memory]) -> None:
        """These were just put to the person as a question."""
        for memory in memories:
            if memory.is_pending:
                memory.remind()
                await self.memories.save(memory)


def _require_pending(memory: Memory) -> None:
    if not memory.is_pending:
        raise PendingError(f"This memory is {memory.status.value}, not pending.")
