"""Re-checking stored memories: the same resolver as ingest, oldest first, and a
dry run that writes nothing. Real in-memory Qdrant, scripted judge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from continuum.models.memory import Memory, MemoryCategory, MemoryStatus
from continuum.services.memory_store import MemoryStore
from continuum.services.recheck import RecheckService
from continuum.services.resolution import ResolutionService
from tests.test_ingest_pipeline import StubLLM, settings, store  # noqa: F401

T0 = datetime(2026, 9, 21, tzinfo=UTC)


def make(content, category, subject, days):  # noqa: ANN001, ANN201
    return Memory(user_id="mark", content=content, category=category, subject=subject,
                  created_at=T0 + timedelta(days=days))


async def setup(store, settings, judgement, *items):  # noqa: ANN001, ANN201, F811
    llm = StubLLM([[]], judgement=judgement)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    await memories.add_many(list(items))
    resolver = ResolutionService(memories, llm, settings)  # type: ignore[arg-type]
    return memories, RecheckService(memories, resolver, settings), llm


def live_case():  # noqa: ANN201
    """The pair from the screenshot: a decision with a subject, a later fact without."""
    return (
        make("Chose Postgres for Atlas", MemoryCategory.DECISION, "atlas-project", 0),
        make("Atlas is using MongoDB", MemoryCategory.FACT, None, 12),
    )


async def test_a_dry_run_reports_and_writes_nothing(store, settings):  # noqa: F811
    old, new = live_case()
    memories, service, _ = await setup(
        store, settings, {"relation": "conflict", "confidence": 0.9, "reason": "db"}, old, new
    )
    report = await service.run("mark")
    assert report.subjects_filled == [(new.id, "atlas-project")]
    assert [(a.verdict, a.memory_id, a.target_id) for a in report.actions] == [
        ("conflict", new.id, old.id)
    ]
    assert (await memories.get(new.id)).subject is None
    assert (await memories.get(old.id)).status is MemoryStatus.ACTIVE


async def test_apply_fills_the_subject_and_raises_the_dispute(store, settings):  # noqa: F811
    old, new = live_case()
    memories, service, _ = await setup(
        store, settings, {"relation": "conflict", "confidence": 0.9, "reason": "db"}, old, new
    )
    await service.run("mark", apply=True)
    stored_new, stored_old = await memories.get(new.id), await memories.get(old.id)
    assert stored_new.subject == "atlas-project"
    assert stored_new.conflicts_with == [old.id] and stored_old.conflicts_with == [new.id]
    assert stored_new.escalation_against(old.id) is not None  # graded later, like ingest


async def test_a_confident_supersede_retires_the_older_belief(store, settings):  # noqa: F811
    old, new = live_case()
    memories, service, _ = await setup(
        store, settings, {"relation": "supersedes", "confidence": 0.95, "reason": "moved"},
        old, new,
    )
    await service.run("mark", apply=True)
    stored_old = await memories.get(old.id)
    assert stored_old.status is MemoryStatus.SUPERSEDED and stored_old.superseded_by == new.id


async def test_running_twice_changes_nothing_the_second_time(store, settings):  # noqa: F811
    old, new = live_case()
    _, service, llm = await setup(
        store, settings, {"relation": "conflict", "confidence": 0.9, "reason": "db"}, old, new
    )
    await service.run("mark", apply=True)
    calls = llm.judge_calls
    second = await service.run("mark", apply=True)
    assert second.actions == [] and llm.judge_calls == calls


async def test_a_duplicate_pair_is_reported_never_merged(store, settings):  # noqa: F811
    a = make("Atlas uses Postgres", MemoryCategory.DECISION, "atlas", 0)
    b = make("Atlas uses Postgres", MemoryCategory.DECISION, "atlas", 5)
    memories, service, _ = await setup(store, settings, None, a, b)
    report = await service.run("mark", apply=True)
    assert [x.verdict for x in report.actions] == ["duplicate"]
    assert {m.status for m in await memories.list_all(user_id="mark")} == {MemoryStatus.ACTIVE}


async def test_events_and_unrelated_subjects_are_left_alone(store, settings):  # noqa: F811
    memories, service, llm = await setup(
        store, settings, {"relation": "supersedes", "confidence": 0.99, "reason": "x"},
        make("Shipped Atlas v1 to staging", MemoryCategory.EVENT, "atlas", 0),
        make("Shipped Atlas v2 to staging", MemoryCategory.EVENT, "atlas", 5),
        make("Billing uses Stripe", MemoryCategory.DECISION, "billing", 6),
    )
    report = await service.run("mark", apply=True)
    assert report.actions == [] and llm.judge_calls == 0


async def test_a_placeholder_subject_is_treated_as_missing(store, settings):  # noqa: F811
    """Live: the extractor wrote "null" as a subject, making every such memory one entity."""
    old = make("Chose Postgres for Atlas", MemoryCategory.DECISION, "atlas-project", 0)
    new = make("Atlas is using MongoDB", MemoryCategory.FACT, "null", 12)
    memories, service, _ = await setup(
        store, settings, {"relation": "conflict", "confidence": 0.9, "reason": "db"}, old, new
    )
    report = await service.run("mark", apply=True)
    assert report.subjects_filled == [(new.id, "atlas-project")]
    assert (await memories.get(new.id)).subject == "atlas-project"


async def test_a_failed_embedding_skips_the_memory_and_carries_on(store, settings):  # noqa: F811
    old, new = live_case()
    memories, service, llm = await setup(
        store, settings, {"relation": "conflict", "confidence": 0.9, "reason": "db"}, old, new
    )

    async def broken(_text):  # noqa: ANN001, ANN202
        raise RuntimeError("NaN from the embedder")

    llm.embed_one = broken  # type: ignore[method-assign]
    report = await service.run("mark", apply=True)
    assert report.skipped == [new.id] and report.actions == []
    assert (await memories.get(old.id)).status is MemoryStatus.ACTIVE
