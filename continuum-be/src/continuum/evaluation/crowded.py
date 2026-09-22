"""Candidate selection in a crowded graph — the check behind "null subjects are harmless".

Most extracted facts come out with no subject, and with no subject the cheap
filter cannot rule out a different entity's memory. The resolver judges only its
single top-scoring comparable candidate, so in a graph holding several clients'
budgets, a subject-less fact must reach the RIGHT client's budget on cosine
alone. This runs each scenario with subjects null and with subjects set, using
the real embedding model and the resolver's own `_pick_candidate`.

It is a property of the embedding model: bge-m3 passes 8/8 either way because it
separates entity names inside the text. A model that collapses names — as
nomic-embed-text did — would not, so re-run this whenever the model changes.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient

from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.evaluation.corpus import CORPUS_DIR
from continuum.evaluation.types import CorpusFact
from continuum.models.memory import ExtractedFact, Memory, MemoryStatus
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService

CROWDED_CORPUS = CORPUS_DIR / "crowded.yaml"


class CrowdedScenario(BaseModel):
    id: str
    graph: list[CorpusFact]
    incoming: CorpusFact
    target: int


class CrowdedResult(BaseModel):
    scenario: str
    with_subjects: bool
    judged: str | None
    right: bool


def load_crowded(path: Path | None = None) -> list[CrowdedScenario]:
    rows = yaml.safe_load((path or CROWDED_CORPUS).read_text(encoding="utf-8"))
    return [CrowdedScenario.model_validate(r) for r in rows]


async def run_crowded(llm: LLMClient, scenarios: list[CrowdedScenario]) -> list[CrowdedResult]:
    results = []
    for scenario in scenarios:
        for with_subjects in (False, True):
            results.append(await _one(llm, scenario, with_subjects))
    return results


async def _one(llm: LLMClient, scenario: CrowdedScenario, with_subjects: bool) -> CrowdedResult:
    settings = llm.settings
    store = QdrantStore.__new__(QdrantStore)
    store.settings = settings
    store.collection = f"crowded_{scenario.id.replace('-', '_')}"
    store.client = AsyncQdrantClient(":memory:")
    try:
        await store.ensure_collection()
        memories = MemoryStore(store, llm, settings)
        stored = []
        for item in scenario.graph:
            memory = Memory(
                user_id="crowded",
                content=item.content,
                category=item.category,
                subject=item.subject if with_subjects else None,
            )
            await memories.add(memory)
            stored.append(memory)

        incoming = scenario.incoming
        fact = ExtractedFact(
            content=incoming.content,
            category=incoming.category,
            subject=incoming.subject if with_subjects else None,
        )
        vector = await llm.embed_one(MemoryStore.embedding_text_for(fact.content, fact.subject))
        neighbours = await memories.search_by_vector(
            user_id="crowded",
            vector=vector,
            limit=settings.resolution_candidate_limit,
            statuses=[MemoryStatus.ACTIVE],
            score_threshold=settings.conflict_similarity_threshold,
        )
        chosen = ResolutionService(memories, llm, settings)._pick_candidate(fact, neighbours)
        return CrowdedResult(
            scenario=scenario.id,
            with_subjects=with_subjects,
            judged=chosen.content if chosen else None,
            right=chosen is not None and chosen.id == stored[scenario.target].id,
        )
    finally:
        await store.client.close()


def render_crowded(results: list[CrowdedResult]) -> str:
    lines = []
    for scenario in dict.fromkeys(r.scenario for r in results):
        null, subj = (next(r for r in results if r.scenario == scenario and r.with_subjects is w)
                      for w in (False, True))
        lines.append(
            f"  {scenario:<34} null: {'ok  ' if null.right else 'MISS'}   "
            f"subjects: {'ok  ' if subj.right else 'MISS'}"
            + ("" if null.right else f"   (null judged: {null.judged!r})")
        )
    n = len(results) // 2
    null_right = sum(r.right for r in results if not r.with_subjects)
    subj_right = sum(r.right for r in results if r.with_subjects)
    lines.append("")
    lines.append(
        f"  right memory judged — null subjects {null_right}/{n}, with subjects {subj_right}/{n}"
    )
    return "\n".join(lines)
