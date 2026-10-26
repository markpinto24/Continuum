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
from dataclasses import dataclass, field

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
    Mode,
    mode_of,
)
from continuum.models.schemas import IngestRequest, IngestResponse, ResolutionRecord
from continuum.services.agreement import extract_turn
from continuum.services.extraction import FactExtractor
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import (
    Resolution,
    ResolutionService,
    Verdict,
    material_difference,
)
from continuum.services.subjects import canonical_subject, infer_subject
from continuum.services.wording import decided_wording

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

        mode = mode_of(request.user_id)
        # The graph's mode picks the prompt — life facts in Personal, work in
        # Workspace — and whether a lone chat turn keeps its "user:" label. A
        # reply to an answer reads that answer as context (services/agreement.py).
        reply = _lone_user_turn(request)
        if reply is not None:
            # Every lone turn, first or not: a question makes no choice either way.
            # With no answer before it and no question in it, this is exactly the
            # transcript it always was.
            facts = await extract_turn(
                self.extractor, reply=reply, proposal=request.proposal, mode=mode
            )
        else:
            transcript = request.as_transcript(label_lone_turn=mode is Mode.WORKSPACE)
            facts = await self.extractor.extract(transcript, mode=mode)
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
        renamed = inferred = 0
        for fact in facts:
            if fact.subject is None:
                fact.subject = infer_subject(fact.content, known)
                inferred += fact.subject is not None
            canonical = canonical_subject(fact.subject, known)
            if canonical != fact.subject:
                renamed += 1
                fact.subject = canonical
            if fact.subject:
                known.add(fact.subject)
        if renamed or inferred:
            # Counts only: subject slugs are user data.
            log.info("ingest.subjects_canonicalised", count=renamed, inferred=inferred)

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
        check_team = self._checks_team(user_id)

        # One embedding call for the whole batch; each vector is reused for both
        # the neighbour lookup and the eventual write.
        vectors = await self.llm.embed([MemoryStore.embedding_text(m) for m in candidates])

        batch = _Batch(check_team=check_team, compatible=compatible)
        for fact, memory, vector in zip(facts, candidates, vectors, strict=True):
            if fact.tentative:
                await self._hold_pending(memory, vector, batch, user_id=user_id)
                continue
            # Said in their own words, a pending item is confirmed: the stored one
            # is settled in place of a fresh copy, keeping its first provenance.
            pending = await self._matching_pending(fact, vector, user_id=user_id)
            if pending is not None:
                # Said again as decided, those are now its words ("will consider
                # adding a long run" -> "will add a long run"), and the vector
                # this turn embedded is theirs.
                if fact.content != pending.content:
                    pending.tentative_wording = pending.content
                    pending.content = fact.content
                    await self.memories.write_with_vector(pending, vector)
                pending.confirm()
                batch.confirmed.append(pending.id)
                log.info("ingest.pending_confirmed", memory_id=pending.id, by="restatement")
                memory = pending
            await self._settle(fact, memory, vector, batch)

        log.info(
            "ingest.completed",
            extracted=len(facts),
            created=len(batch.created),
            duplicates=batch.duplicates,
            superseded=len(batch.superseded),
            conflicts=len(batch.conflicts),
            team_conflicts=len(batch.team_conflicts),
            pending=len(batch.pending),
            confirmed=len(batch.confirmed),
        )
        return batch.response(source_id=source_id, extracted=len(facts))

    async def confirm_pending(self, memory: Memory) -> IngestResponse:
        """The person said yes. Only now is it a belief, so only now is it judged:
        against the graph as it stands, exactly as if it had just been said —
        and so in decided words, not the maybe's (`wording.decided_wording`)."""
        decided = await decided_wording(self.llm, memory.content)
        if decided:
            memory.tentative_wording = memory.content
            memory.content = decided
        memory.confirm()
        fact = ExtractedFact(
            content=memory.content, category=memory.category, subject=memory.subject,
            source_excerpt=memory.source_excerpt,
        )
        [vector] = await self.llm.embed([MemoryStore.embedding_text(memory)])
        if decided:
            # Every verdict but one rewrites the vector; a duplicate only saves.
            await self.memories.write_with_vector(memory, vector)
        compatible = await self.rules(memory.user_id) if self.rules else set()
        batch = _Batch(
            check_team=self._checks_team(memory.user_id), compatible=compatible,
            confirmed=[memory.id],
        )
        await self._settle(fact, memory, vector, batch)
        log.info("ingest.pending_confirmed", memory_id=memory.id, by="answer")
        return batch.response(source_id=memory.source_id or memory.id, extracted=1)

    def _checks_team(self, user_id: str) -> bool:
        return (
            self.settings.team_cross_check
            and self.settings.shared_space_enabled
            and user_id != SHARED_SPACE
            # Personal graphs have no team to disagree with.
            and mode_of(user_id) is Mode.WORKSPACE
        )

    async def _settle(
        self, fact: ExtractedFact, memory: Memory, vector: list[float], batch: _Batch
    ) -> None:
        """Resolve one belief against its graph and apply the verdict."""
        user_id = memory.user_id
        neighbours = await self.memories.search_by_vector(
            user_id=user_id,
            vector=vector,
            limit=self.settings.resolution_candidate_limit,
            statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
            score_threshold=self.settings.conflict_similarity_threshold,
        )
        # A confirmed pending item is already stored; it must not meet itself.
        neighbours = [(m, score) for m, score in neighbours if m.id != memory.id]

        resolution = await self.resolver.resolve(
            fact, neighbours, compatible_subjects=batch.compatible
        )
        batch.records.append(_record(memory, resolution))

        log.info(
            "ingest.resolved",
            verdict=resolution.verdict,
            judge_confidence=resolution.judge_confidence,
            target_id=resolution.target.id if resolution.target else None,
            escalated=resolution.escalated_from is not None,
        )

        if resolution.verdict is Verdict.DUPLICATE and resolution.target:
            batch.duplicates += 1
            if fact.adopted:
                # Agreeing to what the answer said back is not a new observation:
                # the answer was made from memory. Nothing stored, nothing raised.
                return
            await self.memories.reinforce(resolution.target, self.settings.reinforcement_step)
            batch.reinforced.append(resolution.target.id)
            if memory.id in batch.confirmed:
                # A confirmed pending item that was already believed is carried
                # by that belief from now on — retired by the edge, not deleted.
                memory.mark_superseded_by(resolution.target.id)
                await self.memories.save(memory)
            return

        if resolution.verdict is Verdict.CONFLICT and resolution.target:
            memory.escalations.append(
                escalation_record(resolution, neighbours, self.settings.auto_supersede_confidence)
            )
        await self._apply(memory, vector, resolution)
        batch.created.append(memory)

        if resolution.verdict is Verdict.SUPERSEDES and resolution.target:
            batch.superseded.append(resolution.target.id)
        elif resolution.verdict is Verdict.CONFLICT and resolution.target:
            batch.conflicts.append(resolution.target.id)

        if batch.check_team:
            if batch.team_rules is None:
                batch.team_rules = await self.rules(SHARED_SPACE) if self.rules else set()
            team_target = await self._check_against_team(fact, memory, vector, batch.team_rules)
            if team_target:
                batch.team_conflicts.append(team_target)

    async def _hold_pending(
        self, memory: Memory, vector: list[float], batch: _Batch, *, user_id: str
    ) -> None:
        """Store a "maybe" as pending: never judged, never retiring anything.

        Skipped when it is already pending (asking twice is not new) or already
        believed (a maybe about what you already do is not a new question). The
        team space keeps no maybes: team knowledge is what someone decided.
        """
        if user_id == SHARED_SPACE:
            return
        for statuses in ([MemoryStatus.PENDING], [MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED]):
            if await self._near_identical(memory, vector, user_id=user_id, statuses=statuses):
                batch.duplicates += 1
                return
        await self.memories.write_with_vector(memory, vector)
        batch.pending.append(memory.id)
        batch.created.append(memory)
        log.info("ingest.pending_held", memory_id=memory.id, category=memory.category)

    async def _matching_pending(
        self, fact: ExtractedFact, vector: list[float], *, user_id: str
    ) -> Memory | None:
        probe = fact.to_memory(user_id=user_id, source_id=None, confidence=0.0)
        return await self._near_identical(
            probe, vector, user_id=user_id, statuses=[MemoryStatus.PENDING]
        )

    async def _near_identical(
        self, memory: Memory, vector: list[float], *, user_id: str, statuses: list[MemoryStatus]
    ) -> Memory | None:
        """The same statement, by the duplicate guard's standard: above the
        duplicate band, and no number, date, name or negation changed. Cheap —
        no judge call — and it only ever matches, never retires."""
        hits = await self.memories.search_by_vector(
            user_id=user_id,
            vector=vector,
            limit=1,
            statuses=statuses,
            score_threshold=self.settings.duplicate_similarity_threshold,
        )
        for candidate, _ in hits:
            if not material_difference(memory.content, candidate.content):
                return candidate
        return None

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
        escalation = escalation_record(
            resolution, neighbours, self.settings.auto_supersede_confidence
        )
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


def escalation_record(
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


def _lone_user_turn(request: IngestRequest) -> str | None:
    """The text of a single user message — the shape of a chat write-back."""
    messages = request.messages or []
    if len(messages) == 1 and messages[0].role == "user":
        return messages[0].content
    return None


@dataclass
class _Batch:
    """What one ingest (or one confirmation) did to the graph, as it happens."""

    check_team: bool
    compatible: set[str]
    team_rules: set[str] | None = None
    created: list[Memory] = field(default_factory=list)
    reinforced: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    team_conflicts: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    records: list[ResolutionRecord] = field(default_factory=list)
    duplicates: int = 0

    def response(self, *, source_id: str, extracted: int) -> IngestResponse:
        return IngestResponse(
            source_id=source_id,
            extracted=extracted,
            created=self.created,
            duplicates_skipped=self.duplicates,
            reinforced=self.reinforced,
            superseded=self.superseded,
            conflicts_raised=self.conflicts,
            team_conflicts=self.team_conflicts,
            pending=self.pending,
            confirmed=self.confirmed,
            resolutions=self.records,
        )
