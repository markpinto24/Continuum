"""Retrieval ranking tests.

Similarity alone would rank a stale, never-confirmed memory above one the person
reaffirmed last week. These pin the formula that stops that, and the rule that
a contradicted memory is ranked rather than filtered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from continuum.config import Settings
from continuum.models.memory import Memory, MemoryCategory, MemoryStatus
from continuum.services.retrieval import RetrievalService, rank_score


@pytest.fixture
def settings() -> Settings:
    return Settings(
        chat_memory_limit=3,
        chat_candidate_multiplier=4,
        retrieval_recency_half_life_days=45.0,
        retrieval_recency_floor=0.30,
    )


def memory(
    *,
    content: str = "Atlas runs on Postgres",
    confidence: float = 0.7,
    age_days: float = 0.0,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    conflicts_with: list[str] | None = None,
    subject: str | None = "atlas",
    user_id: str = "mark",
) -> Memory:
    stamp = datetime.now(UTC) - timedelta(days=age_days)
    return Memory(
        user_id=user_id,
        content=content,
        category=MemoryCategory.FACT,
        subject=subject,
        confidence=confidence,
        status=status,
        conflicts_with=conflicts_with or [],
        last_reinforced_at=stamp,
    )


class StubStore:
    """Stands in for MemoryStore: scripted search hits, real id lookup."""

    def __init__(self, hits: list[tuple[Memory, float]], extra: list[Memory] | None = None):
        self.hits = hits
        self.requested_limit: int | None = None
        self._by_id = {m.id: m for m, _ in hits}
        for m in extra or []:
            self._by_id[m.id] = m

    async def search(self, *, user_id, query, limit=None, score_threshold=None, **kwargs):  # noqa: ANN001, ARG002
        self.requested_limit = limit
        return [(m, s) for m, s in self.hits if m.user_id == user_id]

    async def resolve_ids(self, memory_ids: list[str]) -> dict[str, Memory]:
        return {mid: self._by_id[mid] for mid in memory_ids if mid in self._by_id}


def service(store: StubStore, settings: Settings) -> RetrievalService:
    return RetrievalService(store, settings)  # type: ignore[arg-type]


# --- The formula ------------------------------------------------------------


def test_rank_is_the_product_of_all_three_factors():
    assert rank_score(
        similarity=0.8,
        confidence=0.5,
        recency=0.5,
        confidence_weight=1.0,
        recency_weight=1.0,
    ) == pytest.approx(0.2)


def test_zero_weight_disables_a_factor_exactly():
    """Phase 5 needs to A/B the weights; 0 must mean 'ignore', not 'zero out'."""
    assert rank_score(
        similarity=0.8,
        confidence=0.1,
        recency=0.1,
        confidence_weight=0.0,
        recency_weight=0.0,
    ) == pytest.approx(0.8)


def test_negative_similarity_cannot_produce_a_negative_rank():
    assert rank_score(
        similarity=-0.4,
        confidence=0.9,
        recency=1.0,
        confidence_weight=1.0,
        recency_weight=1.0,
    ) == 0.0


def test_recency_weight_never_falls_below_the_floor():
    """Age should down-rank a memory, never make it unreachable."""
    old = memory(age_days=3650)
    weight = old.recency_weight(now=datetime.now(UTC), half_life_days=45.0, floor=0.3)
    assert weight == 0.3


def test_recency_weight_is_one_for_a_just_reinforced_memory():
    fresh = memory(age_days=0)
    assert fresh.recency_weight(
        now=datetime.now(UTC), half_life_days=45.0, floor=0.3
    ) == 1.0


# --- Ranking end to end -----------------------------------------------------


async def test_confidence_outranks_a_better_worded_stale_memory(settings):
    stale = memory(content="Atlas runs on Mongo", confidence=0.2, age_days=200)
    trusted = memory(content="Atlas runs on Postgres", confidence=0.95, age_days=1)
    store = StubStore([(stale, 0.90), (trusted, 0.70)])

    context = await service(store, settings).retrieve(user_id="mark", query="db?")

    assert [item.memory.id for item in context.memories] == [trusted.id, stale.id]


async def test_recency_breaks_a_tie_on_similarity_and_confidence(settings):
    old = memory(content="old", confidence=0.8, age_days=180)
    recent = memory(content="recent", confidence=0.8, age_days=1)
    store = StubStore([(old, 0.85), (recent, 0.85)])

    context = await service(store, settings).retrieve(user_id="mark", query="q")

    assert context.memories[0].memory.id == recent.id
    assert context.memories[0].recency > context.memories[1].recency


async def test_candidates_are_over_fetched_before_re_ranking(settings):
    """Fetching only top-k would cap what confidence and recency can promote."""
    store = StubStore([(memory(), 0.9)])
    await service(store, settings).retrieve(user_id="mark", query="q")

    assert store.requested_limit == (
        settings.chat_memory_limit * settings.chat_candidate_multiplier
    )


async def test_result_is_capped_at_the_limit(settings):
    hits = [(memory(content=f"m{i}", confidence=0.9), 0.9 - i / 100) for i in range(10)]
    context = await service(StubStore(hits), settings).retrieve(user_id="mark", query="q")

    assert len(context.memories) == settings.chat_memory_limit


async def test_ranking_factors_are_reported_for_explainability(settings):
    store = StubStore([(memory(confidence=0.5, age_days=45), 0.8)])
    context = await service(store, settings).retrieve(user_id="mark", query="q")

    item = context.memories[0]
    assert item.similarity == pytest.approx(0.8)
    assert item.recency == pytest.approx(0.5, abs=0.01)
    assert item.score == pytest.approx(0.8 * 0.5 * item.recency, abs=1e-4)


# --- Disagreement -----------------------------------------------------------


async def test_contradicted_memories_are_ranked_not_filtered(settings):
    disputed = memory(content="Atlas runs on Mongo", status=MemoryStatus.CONTRADICTED)
    store = StubStore([(disputed, 0.9)])

    context = await service(store, settings).retrieve(user_id="mark", query="q")

    assert [item.memory.id for item in context.memories] == [disputed.id]


async def test_the_other_side_of_a_dispute_is_fetched_even_if_the_query_missed_it(settings):
    """The counterpart is usually worded differently — that is why they disagree."""
    other = memory(content="Atlas runs on Postgres", status=MemoryStatus.CONTRADICTED)
    disputed = memory(
        content="Atlas runs on Mongo",
        status=MemoryStatus.CONTRADICTED,
        conflicts_with=[other.id],
    )
    other.conflicts_with = [disputed.id]
    store = StubStore([(disputed, 0.9)], extra=[other])

    context = await service(store, settings).retrieve(user_id="mark", query="q")

    assert len(context.disagreements) == 1
    ids = {m.id for m in context.disagreements[0].memories}
    assert ids == {disputed.id, other.id}


async def test_a_dispute_is_reported_once_not_once_per_side(settings):
    a = memory(content="Mongo", status=MemoryStatus.CONTRADICTED)
    b = memory(content="Postgres", status=MemoryStatus.CONTRADICTED)
    a.conflicts_with = [b.id]
    b.conflicts_with = [a.id]
    store = StubStore([(a, 0.9), (b, 0.88)])

    context = await service(store, settings).retrieve(user_id="mark", query="q")

    assert len(context.disagreements) == 1


async def test_another_users_memory_is_never_pulled_into_a_dispute(settings):
    theirs = memory(content="Postgres", status=MemoryStatus.CONTRADICTED, user_id="other")
    mine = memory(
        content="Mongo", status=MemoryStatus.CONTRADICTED, conflicts_with=[theirs.id]
    )
    store = StubStore([(mine, 0.9)], extra=[theirs])

    context = await service(store, settings).retrieve(user_id="mark", query="q")

    assert context.disagreements == []


async def test_active_memories_produce_no_disagreements(settings):
    store = StubStore([(memory(), 0.9)])
    context = await service(store, settings).retrieve(user_id="mark", query="q")
    assert context.disagreements == []
