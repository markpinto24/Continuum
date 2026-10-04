"""Putting a private memory into the shared team space.

Not a copy and not a move. The memory is run through the SHARED graph's
resolver, exactly as if a teammate had just said it — so sharing "Raj leads
mobile" when the team already holds "Sara leads mobile" raises a shared
conflict instead of quietly contradicting team knowledge, and sharing something
the team already knows reinforces it instead of duplicating it.

The private original is then retired by an edge to its shared counterpart
(`superseded_by` + `shared_as`). Commitment 1 holds: nothing is deleted, and the
author's graph still shows where the belief went.
"""

from __future__ import annotations

from continuum.config import Settings
from continuum.core.logger import get_logger
from continuum.models.memory import SHARED_SPACE, ExtractedFact, Memory, MemoryStatus
from continuum.models.schemas import ShareResponse
from continuum.services.ingest import IngestService
from continuum.services.memory_store import MemoryStore

log = get_logger(__name__)

_OUTCOME = {"supersedes": "superseded", "conflict": "conflict"}


class SharingError(Exception):
    status = 409


class SharingDisabled(SharingError):
    status = 404


class SharingService:
    def __init__(self, ingest: IngestService, memories: MemoryStore, settings: Settings) -> None:
        self.ingest = ingest
        self.memories = memories
        self.settings = settings

    async def share(self, memory: Memory, *, author: str, author_email: str) -> ShareResponse:
        if not self.settings.shared_space_enabled:
            raise SharingDisabled("The shared team space is turned off on this server.")
        if memory.is_shared:
            raise SharingError("That memory is already in the shared space.")
        if memory.kind == "summary":
            raise SharingError(
                "A summary cannot be shared. Share the memories it was written from."
            )
        # Disagreeing only with the team is what sharing settles: the team graph's
        # resolver judges it against the team belief (and asks the team if unsure).
        team_only = (
            memory.status is MemoryStatus.CONTRADICTED
            and not memory.conflicts_with
            and bool(memory.team_conflicts_with)
        )
        if team_only:
            for shared_id in list(memory.team_conflicts_with):
                memory.clear_conflict_with(shared_id)
        elif memory.status is not MemoryStatus.ACTIVE:
            raise SharingError(
                "Only an active memory can be shared. Settle its disagreement, or restore "
                "it, first — otherwise a dispute would leak into team knowledge unresolved."
            )

        fact = ExtractedFact(
            content=memory.content,
            category=memory.category,
            subject=memory.subject,
            source_excerpt=memory.source_excerpt,
        )
        result = await self.ingest.ingest_facts(
            [fact],
            user_id=SHARED_SPACE,
            source_id=f"shared:{memory.id}",
            author=author,
            author_email=author_email,
        )

        if result.created:
            shared = result.created[0]
            outcome = _OUTCOME.get(result.resolutions[0].verdict, "created")
        else:
            # The team already knew it: the resolver reinforced the shared memory.
            target = await self.memories.get(result.reinforced[0])
            assert target is not None
            shared, outcome = target, "merged"

        memory.shared_as = shared.id
        memory.mark_superseded_by(shared.id)
        await self.memories.save(memory)

        log.info("sharing.shared", memory_id=memory.id, shared_id=shared.id, outcome=outcome)
        return ShareResponse(outcome=outcome, original=memory, shared=shared)
