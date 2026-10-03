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
from collections.abc import Awaitable, Callable

from continuum.clients.llm import LLMClient
from continuum.config import Settings, get_settings
from continuum.core import logger as clog
from continuum.core.logger import get_logger
from continuum.models.memory import (
    SHARED_SPACE,
    Escalation,
    ExtractedFact,
    Memory,
    MemoryStatus,
)
from continuum.models.schemas import IngestRequest, IngestResponse, ResolutionRecord
from continuum.services.extraction import FactExtractor
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import Resolution, ResolutionService, Verdict
from continuum.services.subjects import canonical_subject

log = get_logger(__name__)


class IngestService:
    def __init__(
        self,
        extractor: FactExtractor,
        memories: MemoryStore,
        resolver: ResolutionService,
        llm: LLMClient,
        settings: Settings | None = None,
        *,
        rules: Callable[[str], Awaitable[set[str]]] | None = None,
    ) -> None:
        # Approved "these can all hold" subjects for a graph (FeedbackService).
        self.rules = rules
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
        return await self.ingest_facts(
            facts,
            user_id=request.user_id,
            source_id=source_id,
            author=request.author,
            author_email=request.author_email,
        )

    async def ingest_facts(
        self,
        facts: list[ExtractedFact],
        *,
        user_id: str,
        source_id: str,
        author: str | None = None,
        author_email: str | None = None,
    ) -> IngestResponse:
        """Resolve already-extracted facts into `user_id`'s graph.

        The second half of `ingest`, on its own so that sharing a memory can run
        it through the shared graph's resolver without extracting it again.
        """

        # Snap each subject onto the slug the graph already uses for that
        # entity, before embedding: the subject filter compares slugs exactly,
        # and the `[subject]` prefix is part of the embedded text. Facts earlier
        # in this batch count as known too, so one note cannot introduce two
        # spellings of the same thing.
        known = await self.memories.distinct_subjects(user_id=user_id)
        renamed = 0
        for fact in facts:
            canonical = canonical_subject(fact.subject, known)
            if canonical != fact.subject:
                renamed += 1
                fact.subject = canonical
            if fact.subject:
                known.add(fact.subject)
        if renamed:
            # A count only: subject slugs are user data.
            log.info("ingest.subjects_canonicalised", count=renamed)

        candidates = [
            fact.to_memory(
                user_id=user_id,
                source_id=source_id,
                confidence=self.settings.default_confidence,
            )
            for fact in facts
        ]
        if user_id == SHARED_SPACE:
            for candidate in candidates:
                candidate.shared_by = author
                candidate.shared_by_email = author_email
        compatible = await self.rules(user_id) if self.rules else set()
        check_team = (
            self.settings.team_cross_check
            and self.settings.shared_space_enabled
            and user_id != SHARED_SPACE
        )
        team_rules: set[str] | None = None
        team_conflicts: list[str] = []

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
                user_id=user_id,
                vector=vector,
                limit=self.settings.resolution_candidate_limit,
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
                score_threshold=self.settings.conflict_similarity_threshold,
            )

            resolution = await self.resolver.resolve(
                fact, neighbours, compatible_subjects=compatible
            )
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

            if resolution.verdict is Verdict.CONFLICT and resolution.target:
                memory.escalations.append(
                    _escalation(resolution, neighbours, self.settings.auto_supersede_confidence)
                )
            await self._apply(memory, vector, resolution)
            created.append(memory)

            if resolution.verdict is Verdict.SUPERSEDES and resolution.target:
                superseded.append(resolution.target.id)
            elif resolution.verdict is Verdict.CONFLICT and resolution.target:
                conflicts.append(resolution.target.id)

            if check_team:
                if team_rules is None:
                    team_rules = await self.rules(SHARED_SPACE) if self.rules else set()
                team_target = await self._check_against_team(fact, memory, vector, team_rules)
                if team_target:
                    team_conflicts.append(team_target)

        log.info(
            "ingest.completed",
            extracted=len(facts),
            created=len(created),
            duplicates=duplicates,
            superseded=len(superseded),
            conflicts=len(conflicts),
            team_conflicts=len(team_conflicts),
        )

        return IngestResponse(
            source_id=source_id,
            extracted=len(facts),
            created=created,
            duplicates_skipped=duplicates,
            reinforced=reinforced,
            superseded=superseded,
            conflicts_raised=conflicts,
            team_conflicts=team_conflicts,
            resolutions=records,
        )

    async def _check_against_team(
        self, fact: ExtractedFact, memory: Memory, vector: list[float], rules: set[str]
    ) -> str | None:
        """Does this new private belief disagree with what the team holds?

        Judged like any other pair, but never applied to the team graph: even a
        confident "supersedes" only flags the private memory. Whether the team is
        out of date is for a person to say — by sharing their memory.
        """
        neighbours = await self.memories.search_by_vector(
            user_id=SHARED_SPACE,
            vector=vector,
            limit=self.settings.resolution_candidate_limit,
            statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
            score_threshold=self.settings.conflict_similarity_threshold,
        )
        if not neighbours:
            return None
        resolution = await self.resolver.resolve(fact, neighbours, compatible_subjects=rules)
        target = resolution.target
        if resolution.verdict not in (Verdict.SUPERSEDES, Verdict.CONFLICT) or target is None:
            return None
        escalation = _escalation(resolution, neighbours, self.settings.auto_supersede_confidence)
        # A policy escalation, like the role rule: across graphs nothing is ever
        # auto-applied, so no gate decided it and it is no evidence about the gate.
        escalation = escalation.model_copy(update={"forced": True})
        if resolution.verdict is Verdict.SUPERSEDES:
            escalation = escalation.model_copy(
                update={"judge_relation": Verdict.SUPERSEDES.value}
            )
        memory.escalations.append(escalation)
        memory.mark_team_conflict(target.id)
        await self.memories.save(memory)
        log.info("ingest.team_conflict", memory_id=memory.id, target_id=target.id,
                 verdict=resolution.verdict)
        return target.id

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


def _escalation(
    resolution: Resolution, neighbours: list[tuple[Memory, float]], gate: float
) -> Escalation:
    """What the resolver thought, kept so a person's later answer can grade it."""
    assert resolution.target is not None
    similarity = next((score for m, score in neighbours if m.id == resolution.target.id), None)
    relation = (
        resolution.escalated_from.value
        if resolution.escalated_from is not None
        else (Verdict.CONFLICT.value if resolution.judge_confidence is not None else None)
    )
    return Escalation(
        target_id=resolution.target.id,
        judge_relation=relation,
        judge_confidence=resolution.judge_confidence,
        similarity=round(similarity, 4) if similarity is not None else None,
        gate=gate,
        forced=resolution.forced_escalation,
        reason=resolution.reason[:500],
    )


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
