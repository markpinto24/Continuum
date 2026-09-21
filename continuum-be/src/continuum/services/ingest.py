"""Ingest pipeline: raw input -> extracted facts -> resolved -> belief graph.

Phase 1 triaged on cosine bands alone. Phase 2 hands the ambiguous band to
`ResolutionService` and *applies* its verdict to the graph:

    duplicate    -> reinforce the existing memory, store nothing new
    supersedes   -> store new, mark old SUPERSEDED, write edges both ways
    conflict     -> store new, mark BOTH CONTRADICTED, link them, escalate
    independent  -> store new, no edges
    new          -> store new

Nothing is ever deleted. Every write is reversible from the conflicts API.
"""

from __future__ import annotations

import uuid

import structlog

from continuum.clients.llm import LLMClient
from continuum.config import Settings, get_settings
from continuum.core import logging as clog
from continuum.models.memory import Memory, MemoryStatus
from continuum.models.schemas import IngestRequest, IngestResponse, ResolutionRecord
from continuum.services.extraction import FactExtractor
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import Resolution, ResolutionService, Verdict

log = structlog.get_logger(__name__)


class IngestService:
    def __init__(
        self,
        extractor: FactExtractor,
        memories: MemoryStore,
        resolver: ResolutionService,
        llm: LLMClient,
        settings: Settings | None = None,
    ) -> None:
        self.extractor = extractor
        self.memories = memories
        self.resolver = resolver
        self.llm = llm
        self.settings = settings or get_settings()

    async def ingest(self, request: IngestRequest) -> IngestResponse:
        source_id = request.source_id or str(uuid.uuid4())
        clog.bind(source_id=source_id, user_id=request.user_id)

        transcript = request.as_transcript()
        facts = await self.extractor.extract(transcript)
        if not facts:
            log.info("ingest.no_facts")
            return IngestResponse(source_id=source_id, extracted=0)

        candidates = [
            fact.to_memory(
                user_id=request.user_id,
                source_id=source_id,
                confidence=self.settings.default_confidence,
            )
            for fact in facts
        ]
        # One embedding call for the whole batch; each vector is reused for both
        # the neighbour lookup and the eventual write.
        vectors = await self.llm.embed([MemoryStore.embedding_text(m) for m in candidates])

        created: list[Memory] = []
        reinforced: list[str] = []
        superseded: list[str] = []
        conflicts: list[str] = []
        records: list[ResolutionRecord] = []
        duplicates = 0

        for fact, memory, vector in zip(facts, candidates, vectors, strict=True):
            neighbours = await self.memories.search_by_vector(
                user_id=request.user_id,
                vector=vector,
                limit=self.settings.resolution_candidate_limit,
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
                score_threshold=self.settings.conflict_similarity_threshold,
            )

            resolution = await self.resolver.resolve(fact, neighbours)
            records.append(_record(memory, resolution))

            log.info(
                "ingest.resolved",
                verdict=resolution.verdict,
                judge_confidence=resolution.judge_confidence,
                target_id=resolution.target.id if resolution.target else None,
                escalated=resolution.escalated_from is not None,
            )

            if resolution.verdict is Verdict.DUPLICATE and resolution.target:
                duplicates += 1
                await self.memories.reinforce(
                    resolution.target, self.settings.reinforcement_step
                )
                reinforced.append(resolution.target.id)
                continue

            await self._apply(memory, vector, resolution)
            created.append(memory)

            if resolution.verdict is Verdict.SUPERSEDES and resolution.target:
                superseded.append(resolution.target.id)
            elif resolution.verdict is Verdict.CONFLICT and resolution.target:
                conflicts.append(resolution.target.id)

        log.info(
            "ingest.completed",
            extracted=len(facts),
            created=len(created),
            duplicates=duplicates,
            superseded=len(superseded),
            conflicts=len(conflicts),
        )

        return IngestResponse(
            source_id=source_id,
            extracted=len(facts),
            created=created,
            duplicates_skipped=duplicates,
            reinforced=reinforced,
            superseded=superseded,
            conflicts_raised=conflicts,
            resolutions=records,
        )

    # --- Applying a verdict to the graph -----------------------------------

    async def _apply(
        self, memory: Memory, vector: list[float], resolution: Resolution
    ) -> None:
        target = resolution.target

        if resolution.verdict is Verdict.SUPERSEDES and target is not None:
            # The winner inherits elevated confidence: it survived a comparison.
            memory.confidence = max(
                memory.confidence, self.settings.superseded_winner_confidence
            )
            memory.mark_supersedes(target.id)
            target.mark_superseded_by(memory.id)
            await self.memories.write_with_vector(memory, vector)
            await self.memories.save(target)
            return

        if resolution.verdict is Verdict.CONFLICT and target is not None:
            # Both sides stay retrievable and both are flagged. The agent should
            # surface the disagreement rather than pick a winner unasked.
            memory.mark_contradicted(target.id)
            target.mark_contradicted(memory.id)
            await self.memories.write_with_vector(memory, vector)
            await self.memories.save(target)
            return

        await self.memories.write_with_vector(memory, vector)


def _record(memory: Memory, resolution: Resolution) -> ResolutionRecord:
    return ResolutionRecord(
        memory_id=memory.id,
        content=memory.content,
        verdict=resolution.verdict.value,
        target_id=resolution.target.id if resolution.target else None,
        judge_confidence=resolution.judge_confidence,
        reason=resolution.reason,
        escalated=resolution.escalated_from is not None,
    )
