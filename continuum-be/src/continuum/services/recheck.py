"""Re-check memories already in a graph, after the resolver learned something.

Ingest only ever compares a new fact with what is already there. When the rules
for *what gets compared* widen — subjects filled from the words, decisions and
facts compared with each other — pairs stored before the change were never
looked at under the new rules. This walks a graph once, oldest first, and puts
each memory through the normal resolver against the older ones, exactly as if it
had just arrived:

  * subjects the extractor left out are filled first (`infer_subject`), and the
    memory re-embedded, since the subject is part of the embedded text; so is a
    memory filed under `self` whose words name one thing the graph knows;
  * SUPERSEDES writes the edge and retires the older belief, like ingest;
  * CONFLICT flags both sides for the inbox, with the escalation recorded;
  * DUPLICATE is reported only — merging two stored memories would discard one,
    and that is a person's call, not a backfill's;
  * nothing is deleted, and a dry run (the default in the script) writes nothing.

Every verdict comes from the same resolver, gate and rules as live ingest, so the
backfill cannot be more aggressive than the system already is.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from continuum.config import Settings
from continuum.core.logger import get_logger
from continuum.models.memory import ExtractedFact, MemoryStatus
from continuum.services.ingest import escalation_record
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService, Verdict
from continuum.services.subjects import (
    SELF_SUBJECTS,
    canonical_subject,
    clean_subject,
    infer_subject,
)

log = get_logger(__name__)


class RecheckAction(BaseModel):
    verdict: str
    memory_id: str
    target_id: str
    memory: str
    target: str
    judge_confidence: float | None = None


class RecheckReport(BaseModel):
    owner: str
    examined: int = 0
    subjects_filled: list[tuple[str, str]] = Field(
        default_factory=list, description="(memory id, subject) filled from the words."
    )
    actions: list[RecheckAction] = Field(default_factory=list)
    skipped: list[str] = Field(
        default_factory=list, description="Memories that could not be embedded; untouched."
    )
    applied: bool = False


class RecheckService:
    def __init__(
        self,
        memories: MemoryStore,
        resolver: ResolutionService,
        settings: Settings,
        *,
        rules: Callable[[str], Awaitable[set[str]]] | None = None,
    ) -> None:
        self.memories = memories
        self.resolver = resolver
        self.settings = settings
        self.rules = rules

    async def run(self, owner: str, *, apply: bool = False) -> RecheckReport:
        report = RecheckReport(owner=owner, applied=apply)
        live = [
            m
            for m in await self.memories.list_all(
                user_id=owner,
                limit=100_000,
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
            )
            if m.kind == "fact"
        ]
        live.sort(key=lambda m: m.created_at)
        stored_subject = {m.id: m.subject for m in live}
        known = {m.subject for m in live if clean_subject(m.subject)}

        # 1. Subjects the extractor left out — or wrote as a placeholder ("null") —
        # and `self`. `self` meets only `self` (subjects.py), so a choice filed
        # there before agreed choices got a real subject (FINDINGS §13) never
        # meets its own topic: "will follow a four-day workout split" sat on
        # `self` while the routine that replaced it was filed as `workout`, and
        # the two were never compared (§22). Moved only when the words name one
        # thing the graph knows; "The user is 22" stays where it is.
        things = {s for s in known if s not in SELF_SUBJECTS}
        # A moved memory's vector, as apply stores it. A dry run writes nothing,
        # so without these it searched with the old vector and reported nothing
        # where apply then raised a dispute (2026-11-04, the live four-day split).
        moved: dict[str, list[float]] = {}
        for memory in live:
            if memory.subject and clean_subject(memory.subject) is None:
                memory.subject = None
            if memory.subject in SELF_SUBJECTS:
                subject = infer_subject(memory.content, things)
            elif memory.subject:
                continue
            else:
                subject = canonical_subject(infer_subject(memory.content, known), known)
            if subject:
                memory.subject = subject
                report.subjects_filled.append((memory.id, subject))
            if memory.subject == stored_subject.get(memory.id):
                continue
            try:
                moved[memory.id] = await self.memories.llm.embed_one(
                    MemoryStore.embedding_text(memory)
                )
                if apply:
                    await self.memories.add(memory)  # re-embed: "[subject] content"
            except Exception:  # noqa: BLE001 - leave it exactly as stored
                log.exception("recheck.embed_failed", memory_id=memory.id)
                memory.subject = stored_subject.get(memory.id)
                moved.pop(memory.id, None)
                report.skipped.append(memory.id)

        compatible = await self.rules(owner) if self.rules else set()
        retired: set[str] = set()

        # 2. Each memory against the older ones, as if it had just arrived.
        for index, memory in enumerate(live):
            if memory.id in retired or memory.id in report.skipped:
                continue
            older = {m.id for m in live[:index] if m.id not in retired}
            if not older:
                continue
            report.examined += 1
            try:
                vector = await self.memories.llm.embed_one(MemoryStore.embedding_text(memory))
            except Exception:  # noqa: BLE001 - skipping changes nothing; stopping helps no one
                log.exception("recheck.embed_failed", memory_id=memory.id)
                report.skipped.append(memory.id)
                continue
            found = {
                m.id: (m, score)
                for m, score in await self.memories.search_by_vector(
                    user_id=owner,
                    vector=vector,
                    limit=self.settings.resolution_candidate_limit + 5,
                    statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
                    score_threshold=self.settings.conflict_similarity_threshold,
                )
            }
            # Older memories that moved are scored as apply would store them.
            by_id = {m.id: m for m in live}
            for moved_id, moved_vector in moved.items():
                score = _cosine(vector, moved_vector)
                found.pop(moved_id, None)
                if score >= self.settings.conflict_similarity_threshold:
                    found[moved_id] = (by_id[moved_id], score)
            neighbours = sorted(
                (
                    (m, score)
                    for m, score in found.values()
                    # Only older memories, not already linked to this one either way.
                    if m.id in older
                    and m.id not in memory.conflicts_with + memory.supersedes
                    and memory.id not in m.conflicts_with + m.supersedes
                ),
                key=lambda pair: pair[1],
                reverse=True,
            )
            if not neighbours:
                continue
            fact = ExtractedFact(
                content=memory.content,
                category=memory.category,
                subject=memory.subject,
                source_excerpt=memory.source_excerpt,
            )
            resolution = await self.resolver.resolve(
                fact, neighbours, compatible_subjects=compatible
            )
            target = resolution.target
            if target is None or resolution.verdict in (Verdict.NEW, Verdict.INDEPENDENT):
                continue
            report.actions.append(
                RecheckAction(
                    verdict=resolution.verdict.value,
                    memory_id=memory.id,
                    target_id=target.id,
                    memory=memory.content,
                    target=target.content,
                    judge_confidence=resolution.judge_confidence,
                )
            )
            if not apply or resolution.verdict is Verdict.DUPLICATE:
                continue
            if resolution.verdict is Verdict.SUPERSEDES:
                memory.mark_supersedes(target.id)
                target.mark_superseded_by(memory.id)
                retired.add(target.id)
            else:  # CONFLICT
                memory.escalations.append(
                    escalation_record(
                        resolution, neighbours, self.settings.auto_supersede_confidence
                    )
                )
                memory.mark_contradicted(target.id)
                target.mark_contradicted(memory.id)
            await self.memories.save(memory)
            await self.memories.save(target)

        log.info(
            "recheck.completed",
            examined=report.examined,
            subjects_filled=len(report.subjects_filled),
            actions=len(report.actions),
            applied=apply,
        )
        return report


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0
