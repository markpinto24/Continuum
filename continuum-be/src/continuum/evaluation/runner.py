"""Run the corpus through the real resolution pipeline.

Not a mock. Each case seeds its existing belief into an in-memory Qdrant, embeds
the incoming fact with the configured embedding model, searches for neighbours
the same way ingest does, and hands the result to the real `ResolutionService`.
So a run exercises the whole chain — embedding geometry, the cosine bands, the
category and subject filters, the judge, and the gate — not just the prompt.

Each case gets its own collection. Cases must not see each other's memories, or
the corpus stops being a set of independent measurements.

The expensive part is the judge. It runs at most once per case, and the outcome
records the judge's answer before the gate, so `metrics.sweep` can evaluate every
other gate value for free afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from qdrant_client import AsyncQdrantClient

from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import Settings, get_settings
from continuum.evaluation.types import CaseOutcome, ResolutionCase, action_of
from continuum.models.memory import ExtractedFact, Memory, MemoryStatus
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import Resolution, ResolutionService, Verdict

log = structlog.get_logger(__name__)

EVAL_USER = "eval"


class ResolutionRunner:
    def __init__(self, llm: LLMClient, settings: Settings | None = None) -> None:
        self.llm = llm
        self.settings = settings or get_settings()

    async def run(self, cases: list[ResolutionCase]) -> list[CaseOutcome]:
        outcomes: list[CaseOutcome] = []
        for index, case in enumerate(cases, start=1):
            outcome = await self.run_case(case)
            outcomes.append(outcome)
            log.info(
                "eval.case_done",
                case_id=case.id,
                expected=case.expect.value,
                actual=outcome.action.value,
                hit=outcome.action is case.expect,
                progress=f"{index}/{len(cases)}",
            )
        return outcomes

    async def run_case(self, case: ResolutionCase) -> CaseOutcome:
        store = await self._isolated_store(case.id)
        try:
            memories = MemoryStore(store, self.llm, self.settings)
            resolver = ResolutionService(memories, self.llm, self.settings)

            existing = _seed_memory(case)
            await memories.add(existing)

            fact = ExtractedFact(
                content=case.incoming.content,
                category=case.incoming.category,
                subject=case.incoming.subject,
            )
            # Exactly what IngestService does: embed once, reuse the vector for
            # the neighbour lookup.
            vector = await self.llm.embed_one(
                MemoryStore.embedding_text(
                    Memory(
                        user_id=EVAL_USER,
                        content=fact.content,
                        category=fact.category,
                        subject=fact.subject,
                    )
                )
            )
            neighbours = await memories.search_by_vector(
                user_id=EVAL_USER,
                vector=vector,
                limit=self.settings.resolution_candidate_limit,
                statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
                score_threshold=self.settings.conflict_similarity_threshold,
            )

            resolution = await resolver.resolve(fact, neighbours)
            top_score = neighbours[0][1] if neighbours else None
            return _outcome(case, resolution, top_score, self.settings.auto_supersede_confidence)
        finally:
            await store.client.close()

    async def _isolated_store(self, case_id: str) -> QdrantStore:
        """A fresh in-memory collection per case, so cases cannot contaminate each other."""
        store = QdrantStore.__new__(QdrantStore)
        store.settings = self.settings
        store.collection = f"eval_{case_id.replace('-', '_')}"
        store.client = AsyncQdrantClient(":memory:")
        await store.ensure_collection()
        return store


def _seed_memory(case: ResolutionCase) -> Memory:
    """Build the existing belief, backdated so the judge sees a real recorded date."""
    recorded = datetime.now(UTC) - timedelta(days=case.existing.age_days)
    return Memory(
        user_id=EVAL_USER,
        content=case.existing.content,
        category=case.existing.category,
        subject=case.existing.subject,
        confidence=case.existing.confidence,
        created_at=recorded,
        updated_at=recorded,
        last_reinforced_at=recorded,
    )


def _outcome(
    case: ResolutionCase,
    resolution: Resolution,
    similarity: float | None,
    gate: float,
) -> CaseOutcome:
    return CaseOutcome(
        case_id=case.id,
        expect=case.expect,
        tags=case.tags,
        verdict=resolution.verdict,
        action=action_of(resolution.verdict),
        judge_relation=_judge_relation(resolution),
        judge_confidence=resolution.judge_confidence,
        similarity=similarity,
        gate=gate,
        reason=resolution.reason,
    )


def _judge_relation(resolution: Resolution) -> Verdict | None:
    """Recover what the judge said, before the gate downgraded it.

    `escalated_from` is set only when a `supersedes` was knocked down to a
    conflict, so it is the pre-gate answer whenever it is present. A verdict with
    no `judge_confidence` never reached the judge at all — it was settled by the
    duplicate band or a category/subject filter.
    """
    if resolution.escalated_from is not None:
        return resolution.escalated_from
    if resolution.judge_confidence is None:
        return None
    return resolution.verdict
