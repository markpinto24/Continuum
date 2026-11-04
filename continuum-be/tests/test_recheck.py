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


async def test_a_choice_filed_under_self_meets_its_topic(store, settings):  # noqa: F811
    """Live (FINDINGS §22): the old split sat on `self`, the routine on `workout`."""
    old = make("The user will follow a four-day workout split", MemoryCategory.DECISION,
               "self", 0)
    new = make("The user does a push, pull, legs, upper, lower and arms workout",
               MemoryCategory.ROUTINE, "workout", 9)
    age = make("The user is 22 years old", MemoryCategory.FACT, "self", 1)
    memories, service, _ = await setup(
        store, settings, {"relation": "conflict", "confidence": 0.9, "reason": "split"},
        old, new, age,
    )
    report = await service.run("mark", apply=True)
    assert report.subjects_filled == [(old.id, "workout")]
    assert (await memories.get(old.id)).subject == "workout"
    # Nothing in its words names a thing the graph knows: it is about the user.
    assert (await memories.get(age.id)).subject == "self"


async def test_a_dry_run_reports_what_a_moved_subject_would_raise(store, settings):  # noqa: F811
    """Live, 2026-11-04: the dry run moved the split and reported nothing; apply
    re-embedded it, the pair met, and a dispute was raised nobody had seen."""
    def pair():  # noqa: ANN202
        return (make("The user will follow a four-day workout split", MemoryCategory.DECISION,
                     "self", 0),
                make("The user does a push, pull, legs and arms workout split",
                     MemoryCategory.ROUTINE, "workout", 9))

    judgement = {"relation": "conflict", "confidence": 0.9, "reason": "split"}
    _, dry_service, _ = await setup(store, settings, judgement, *pair())
    dry = await dry_service.run("mark", apply=False)
    await store.client.delete_collection(store.collection)
    await store.ensure_collection()
    _, wet_service, _ = await setup(store, settings, judgement, *pair())
    wet = await wet_service.run("mark", apply=True)

    assert [a.verdict for a in dry.actions] == [a.verdict for a in wet.actions] == ["conflict"]


async def test_self_naming_two_things_stays_self(store, settings):  # noqa: F811
    both = make("The user's workout and diet are planned on Sundays", MemoryCategory.ROUTINE,
                "self", 0)
    memories, service, _ = await setup(
        store, settings, {"relation": "independent", "confidence": 0.9, "reason": "-"},
        both,
        make("Push day on Monday", MemoryCategory.ROUTINE, "workout", 1),
        make("High-protein meals", MemoryCategory.ROUTINE, "diet", 2),
    )
    report = await service.run("mark", apply=False)
    assert report.subjects_filled == []


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
