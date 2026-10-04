"""Re-check memories already in a graph, after the resolver learned something.

Ingest only ever compares a new fact with what is already there. When the rules
for *what gets compared* widen — subjects filled from the words, decisions and
facts compared with each other — pairs stored before the change were never
looked at under the new rules. This walks a graph once, oldest first, and puts
each memory through the normal resolver against the older ones, exactly as if it
had just arrived:

  * subjects the extractor left out are filled first (`infer_subject`), and the
    memory re-embedded, since the subject is part of the embedded text;
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
from continuum.services.subjects import canonical_subject, clean_subject, infer_subject

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

        # 1. Subjects the extractor left out — or wrote as a placeholder ("null").
        for memory in live:
            if memory.subject and clean_subject(memory.subject) is None:
                memory.subject = None
            if memory.subject:
                continue
            subject = canonical_subject(infer_subject(memory.content, known), known)
            if subject:
                memory.subject = subject
                report.subjects_filled.append((memory.id, subject))
            if apply and memory.subject != stored_subject.get(memory.id):
                try:
                    await self.memories.add(memory)  # re-embed: "[subject] content"
                except Exception:  # noqa: BLE001 - leave it exactly as stored
                    log.exception("recheck.embed_failed", memory_id=memory.id)
                    memory.subject = stored_subject.get(memory.id)
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
            neighbours = [
                (m, score)
                for m, score in await self.memories.search_by_vector(
                    user_id=owner,
                    vector=vector,
                    limit=self.settings.resolution_candidate_limit + 5,
                    statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
                    score_threshold=self.settings.conflict_similarity_threshold,
                )
                # Only older memories, not already linked to this one either way.
                if m.id in older
                and m.id not in memory.conflicts_with + memory.supersedes
                and memory.id not in m.conflicts_with + m.supersedes
            ]
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
