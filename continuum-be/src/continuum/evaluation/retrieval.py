"""Retrieval recall, from memories people said should have come up.

Same split as the resolution harness: gather once, sweep free. Each case's
candidates are fetched once with generous over-fetch, keeping the raw factors
(similarity, confidence, recency); every weight setting is then re-ranked as
arithmetic with the same `rank_score` the chat uses.

`recall@k` here is "of the memories you named, how many made the top k" — the
thing that matters for an answer, since a memory outside the top k never reaches
the prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import product

from pydantic import BaseModel

from continuum.config import Settings
from continuum.evaluation.types import RetrievalCase
from continuum.models.memory import visible_owners
from continuum.services.memory_store import MemoryStore
from continuum.services.retrieval import rank_score


class Candidate(BaseModel):
    memory_id: str
    similarity: float
    confidence: float
    recency: float
    keyword: float = 0.0


class Gathered(BaseModel):
    case: RetrievalCase
    candidates: list[Candidate]


class WeightResult(BaseModel):
    confidence_weight: float
    recency_weight: float
    keyword_weight: float
    recall: float
    found: int
    wanted: int
    unreachable: int  # never in the candidate pool at all: a similarity problem


def rank(candidates: list[Candidate], *, confidence_weight: float, recency_weight: float,
         keyword_weight: float) -> list[str]:
    scored = sorted(
        candidates,
        key=lambda c: rank_score(
            similarity=c.similarity,
            confidence=c.confidence,
            recency=c.recency,
            confidence_weight=confidence_weight,
            recency_weight=recency_weight,
            keyword=c.keyword,
            keyword_weight=keyword_weight,
        ),
        reverse=True,
    )
    return [c.memory_id for c in scored]


def recall_at_k(gathered: list[Gathered], *, k: int, confidence_weight: float,
                recency_weight: float, keyword_weight: float) -> WeightResult:
    found = wanted = unreachable = 0
    for item in gathered:
        order = rank(item.candidates, confidence_weight=confidence_weight,
                     recency_weight=recency_weight, keyword_weight=keyword_weight)
        top = set(order[:k])
        pool = set(order)
        for expected in item.case.expect:
            wanted += 1
            found += expected.id in top
            unreachable += expected.id not in pool
    return WeightResult(
        confidence_weight=confidence_weight,
        recency_weight=recency_weight,
        keyword_weight=keyword_weight,
        recall=round(found / wanted, 4) if wanted else 0.0,
        found=found,
        wanted=wanted,
        unreachable=unreachable,
    )


def sweep(gathered: list[Gathered], *, k: int) -> list[WeightResult]:
    grid = product((0.0, 0.5, 1.0, 2.0), (0.0, 0.5, 1.0, 2.0), (0.0, 0.5, 1.0))
    results = [
        recall_at_k(gathered, k=k, confidence_weight=c, recency_weight=r, keyword_weight=w)
        for c, r, w in grid
    ]
    return sorted(results, key=lambda r: (-r.recall, r.confidence_weight + r.recency_weight))


async def gather(cases: list[RetrievalCase], memories: MemoryStore, settings: Settings,
                 *, pool: int = 100) -> list[Gathered]:
    """Fetch each case's candidate pool once, against the live graph."""
    now = datetime.now(UTC)
    out = []
    for case in cases:
        owners = visible_owners(case.owner, shared=settings.shared_space_enabled)
        rows = await memories.search(user_id=owners, query=case.query, limit=pool)
        keyword = dict(
            await memories.keyword_search(user_id=owners, query=case.query, limit=pool)
        )
        out.append(Gathered(case=case, candidates=[
            Candidate(
                memory_id=m.id,
                similarity=s,
                confidence=m.confidence,
                recency=m.recency_weight(
                    now=now,
                    half_life_days=settings.retrieval_recency_half_life_days,
                    floor=settings.retrieval_recency_floor,
                ),
                keyword=keyword.get(m.id, 0.0),
            )
            for m, s in rows
        ]))
    return out


def render(results: list[WeightResult], current: WeightResult, *, k: int) -> str:
    lines = [
        f"  recall@{k} with the configured weights: {current.recall:.0%} "
        f"({current.found}/{current.wanted}; {current.unreachable} never in the pool)",
        "",
        "  best settings (confidence^w, recency^w, keyword weight):",
    ]
    for r in results[:8]:
        lines.append(
            f"    conf {r.confidence_weight:<4} rec {r.recency_weight:<4} kw {r.keyword_weight:<4}"
            f"  recall {r.recall:6.1%}"
        )
    if current.unreachable:
        lines.append(
            "\n  'Never in the pool' cannot be fixed by weights: the memory is worded too\n"
            "  far from the question for similarity (or keywords) to find it."
        )
    return "\n".join(lines)
