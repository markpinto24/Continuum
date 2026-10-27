"""Pending memories: said, not decided (requirements.md Part 2, Phase H).

A "maybe" is kept but never believed. These tests pin the lifecycle from every
side — above all the failure modes: a pending item retiring a belief, being
retired by one, being declined by a model reading free text, nagging forever,
or leaking into the team space — against the real ingest service, resolver and
in-memory Qdrant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from continuum.api.deps import get_principal
from continuum.api.routes import memories as memories_route
from continuum.models.auth import Principal
from continuum.models.memory import (
    SHARED_SPACE,
    Memory,
    MemoryCategory,
    MemoryStatus,
    Mode,
)
from continuum.models.schemas import ChatContext, IngestRequest
from continuum.services.chat import build_system_prompt
from continuum.services.decay import DecayService
from continuum.services.limits import MemoryWindow
from continuum.services.memory_store import MemoryStore
from continuum.services.pending import PendingError, PendingService
from continuum.services.retrieval import RetrievalService
from tests.test_ingest_pipeline import StubLLM, build_service, settings, store  # noqa: F401

SPLIT = "The user follows a push/pull/legs split six days a week"


def gym(content: str, *, tentative: bool = False, category: str = "routine") -> list[dict]:
    return [{"content": content, "category": category, "subject": "gym",
             "tentative": tentative}]


async def setup(store, settings, *batches, judgement=None):  # noqa: ANN001, ANN201, F811
    llm = StubLLM(list(batches) or [[]], judgement=judgement)
    ingest = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    return ingest, memories, PendingService(ingest, memories), llm


async def say(ingest, user: str = "mark:personal"):  # noqa: ANN001, ANN201
    return await ingest.ingest(IngestRequest(user_id=user, text="a"))


# --- The model ------------------------------------------------------------------------


def test_a_pending_item_is_never_a_belief_then_or_now():
    item = Memory(user_id="m", content=SPLIT, status=MemoryStatus.PENDING)
    assert not item.believed_at(datetime.now(UTC) + timedelta(days=1))
    item.decline()
    assert item.status is MemoryStatus.DECLINED and item.decided_at is not None
    assert not item.believed_at(datetime.now(UTC) + timedelta(days=1))


def test_confirming_starts_its_life_as_a_belief_now():
    item = Memory(user_id="m", content=SPLIT, status=MemoryStatus.PENDING)
    item.last_reinforced_at = datetime.now(UTC) - timedelta(days=20)
    item.confirm()
    assert item.status is MemoryStatus.ACTIVE
    # Decay counts from the decision, not from when it was first floated.
    assert (datetime.now(UTC) - item.last_reinforced_at).total_seconds() < 5


@pytest.mark.parametrize(("reminders", "age_days", "expired"),
                         [(2, 29, False), (3, 1, True), (0, 30, True)])
def test_an_unanswered_item_fades_after_three_reminders_or_thirty_days(
    reminders, age_days, expired
):
    now = datetime.now(UTC)
    item = Memory(user_id="m", content=SPLIT, status=MemoryStatus.PENDING,
                  created_at=now - timedelta(days=age_days), reminders=reminders)
    assert item.pending_expired(now=now, max_reminders=3, max_age_days=30) is expired


# --- Ingest: a maybe is held, never judged -----------------------------------------------


async def test_a_maybe_is_held_as_pending_and_never_judged(store, settings):  # noqa: F811
    ingest, memories, _, llm = await setup(store, settings, gym(SPLIT, tentative=True))
    result = await say(ingest)

    assert result.pending == [result.created[0].id]
    assert result.created[0].status is MemoryStatus.PENDING
    assert llm.judge_calls == 0


async def test_a_maybe_never_retires_what_the_person_already_does(store, settings):  # noqa: F811
    ingest, memories, _, llm = await setup(
        store, settings,
        gym("The user follows an upper/lower split"),
        gym("The user might switch to a push/pull/legs split", tentative=True),
        judgement={"relation": "supersedes", "confidence": 0.99, "reason": "switch"},
    )
    current = (await say(ingest)).created[0]
    await say(ingest)

    assert (await memories.get(current.id)).status is MemoryStatus.ACTIVE
    assert llm.judge_calls == 0


async def test_a_new_belief_is_never_judged_against_a_pending_item(store, settings):  # noqa: F811
    ingest, memories, _, llm = await setup(
        store, settings,
        gym("The user might switch to a push/pull/legs split", tentative=True),
        gym("The user follows an upper/lower split"),
        judgement={"relation": "supersedes", "confidence": 0.99, "reason": "switch"},
    )
    pending = (await say(ingest)).created[0]
    second = await say(ingest)

    assert llm.judge_calls == 0  # nothing to compare: the pending item is not a neighbour
    assert second.superseded == [] and second.conflicts_raised == []
    assert (await memories.get(pending.id)).status is MemoryStatus.PENDING


async def test_a_free_text_contradiction_never_declines_a_pending_item(store, settings):  # noqa: F811
    ingest, memories, _, _ = await setup(
        store, settings,
        gym(SPLIT, tentative=True),
        gym("The user decided against a push/pull/legs split"),
        judgement={"relation": "conflict", "confidence": 0.99, "reason": "no"},
    )
    pending = (await say(ingest)).created[0]
    await say(ingest)
    # Only a button, or a spoken no to the question just asked, declines.
    assert (await memories.get(pending.id)).status is MemoryStatus.PENDING


async def test_saying_it_again_as_a_maybe_does_not_ask_twice(store, settings):  # noqa: F811
    ingest, memories, _, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    await say(ingest)
    second = await say(ingest)
    assert second.pending == [] and second.duplicates_skipped == 1
    assert len(await memories.list_all(user_id="mark:personal")) == 1


async def test_a_maybe_about_what_is_already_believed_is_not_a_new_question(store, settings):  # noqa: F811
    ingest, memories, _, _ = await setup(store, settings, gym(SPLIT), gym(SPLIT, tentative=True))
    await say(ingest)
    second = await say(ingest)
    assert second.pending == [] and second.created == []


async def test_the_team_space_keeps_no_maybes(store, settings):  # noqa: F811
    ingest, memories, _, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    result = await say(ingest, user=SHARED_SPACE)
    assert result.created == [] and await memories.list_all(user_id=SHARED_SPACE) == []


# --- Confirming ----------------------------------------------------------------------------


async def test_saying_it_in_your_own_words_confirms_it(store, settings):  # noqa: F811
    ingest, memories, _, _ = await setup(store, settings, gym(SPLIT, tentative=True), gym(SPLIT))
    pending = (await say(ingest)).created[0]
    second = await say(ingest)

    assert second.confirmed == [pending.id]
    assert (await memories.get(pending.id)).status is MemoryStatus.ACTIVE
    assert len(await memories.list_all(user_id="mark:personal")) == 1  # no second copy


async def test_confirming_is_judged_like_something_just_said(store, settings):  # noqa: F811
    ingest, memories, pending, llm = await setup(
        store, settings,
        gym("The user follows an upper/lower split"),
        gym("The user switched to a push/pull/legs split instead", tentative=True),
        judgement={"relation": "supersedes", "confidence": 0.99, "reason": "switched"},
    )
    old = (await say(ingest)).created[0]
    maybe = (await say(ingest)).created[0]

    result = await pending.confirm(maybe)

    assert llm.judge_calls == 1  # judged only now
    assert result.superseded == [old.id]
    assert (await memories.get(old.id)).superseded_by == maybe.id
    assert (await memories.get(maybe.id)).status is MemoryStatus.ACTIVE


async def test_confirming_a_change_the_judge_is_unsure_of_raises_a_dispute(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(
        store, settings,
        gym("The user follows an upper/lower split"),
        gym("The user switched to a push/pull/legs split instead", tentative=True),
        judgement={"relation": "conflict", "confidence": 0.9, "reason": "both?"},
    )
    old = (await say(ingest)).created[0]
    maybe = (await say(ingest)).created[0]
    result = await pending.confirm(maybe)
    assert result.conflicts_raised == [old.id]
    assert (await memories.get(maybe.id)).status is MemoryStatus.CONTRADICTED


async def test_confirming_what_was_already_believed_reinforces_it(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    maybe = (await say(ingest)).created[0]
    # Meanwhile the same belief arrived another way (e.g. shared back, or an import).
    believed = Memory(user_id="mark:personal", content=SPLIT,
                      category=MemoryCategory.ROUTINE, subject="gym")
    await memories.add(believed)

    result = await pending.confirm(maybe)

    assert result.reinforced == [believed.id]
    stored = await memories.get(maybe.id)
    # Carried by the existing belief from now on: retired by the edge, never deleted.
    assert stored.status is MemoryStatus.SUPERSEDED and stored.superseded_by == believed.id


# --- Declining, and still unsure ----------------------------------------------------------


async def test_declining_hides_it_for_good_and_keeps_the_record(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    maybe = (await say(ingest)).created[0]
    await pending.decline(maybe)

    stored = await memories.get(maybe.id)
    assert stored.status is MemoryStatus.DECLINED and stored.content == SPLIT
    with pytest.raises(PendingError):
        await pending.confirm(stored)  # answered once is answered


async def test_still_unsure_keeps_it_pending_and_counts_as_a_reminder(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    maybe = (await say(ingest)).created[0]
    kept = await pending.keep(maybe)
    assert kept.status is MemoryStatus.PENDING and kept.reminders == 1


async def test_the_sweep_fades_an_unanswered_item_but_never_a_fresh_one(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(
        store, settings,
        gym(SPLIT, tentative=True),
        gym("The user might add a cardio day", tentative=True),
    )
    asked = (await say(ingest)).created[0]
    fresh = (await say(ingest)).created[0]
    for _ in range(settings.pending_max_reminders):
        await pending.keep(await memories.get(asked.id))

    report = await DecayService(memories, settings).sweep(user_id="mark:personal")

    assert report.pending_faded == 1
    assert (await memories.get(asked.id)).status is MemoryStatus.ARCHIVED
    assert (await memories.get(fresh.id)).status is MemoryStatus.PENDING


# --- Retrieval and the prompt -------------------------------------------------------------


async def test_a_relevant_pending_item_is_offered_to_ask_about_not_as_memory(store, settings):  # noqa: F811
    ingest, memories, _, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    await say(ingest)
    context = await RetrievalService(memories, settings).retrieve(
        user_id="mark:personal", query="[gym] The user follows a push/pull/legs split"
    )
    assert [m.content for m in context.pending] == [SPLIT]
    assert context.memories == []  # never ranked among beliefs


async def test_pending_items_stay_in_their_own_graph_and_out_of_the_past(store, settings):  # noqa: F811
    ingest, memories, _, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    await say(ingest, user="sara:personal")
    service = RetrievalService(memories, settings)
    query = "[gym] The user follows a push/pull/legs split"
    assert (await service.retrieve(user_id="mark:personal", query=query)).pending == []
    past = await service.retrieve(user_id="sara:personal", query=query,
                                  as_of=datetime.now(UTC) - timedelta(days=1))
    assert past.pending == []


def test_the_prompt_lists_pending_items_apart_and_never_numbers_them():
    item = Memory(user_id="m:personal", content=SPLIT, status=MemoryStatus.PENDING)
    prompt = build_system_prompt(ChatContext(query="q", pending=[item]), mode=Mode.PERSONAL)
    assert "PENDING — said, not decided:" in prompt
    assert f'- "{SPLIT}" (said' in prompt and f'] "{SPLIT}"' not in prompt
    assert "never state one as true" in prompt


# --- Routes: only the person who said it answers it --------------------------------------


async def _client(memories, pending, principal):  # noqa: ANN001, ANN202
    app = FastAPI()
    app.include_router(memories_route.router)
    app.state.memories = memories
    app.state.pending = pending
    app.state.llm_limiter = MemoryWindow(1000, 60)
    app.dependency_overrides[get_principal] = lambda: principal
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def me(mode: Mode, user: str = "mark") -> Principal:
    return Principal(user_id=user, email=f"{user}@x.io", via="session", mode=mode)


async def test_the_answer_routes_settle_your_own_pending_items(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(
        store, settings, gym(SPLIT, tentative=True),
        gym("The user might add a cardio day", tentative=True),
    )
    first = (await say(ingest)).created[0]
    second = (await say(ingest)).created[0]
    async with await _client(memories, pending, me(Mode.PERSONAL)) as client:
        confirmed = await client.post(f"/memories/{first.id}/confirm")
        declined = await client.post(f"/memories/{second.id}/decline")
        again = await client.post(f"/memories/{second.id}/unsure")
        graph = (await client.get("/memories/graph")).json()
        everything = (await client.get("/memories/graph?include_archived=true")).json()
    assert confirmed.status_code == 200 and confirmed.json()["confirmed"] == [first.id]
    assert declined.status_code == 200 and declined.json()["status"] == "declined"
    assert again.status_code == 409  # already answered
    assert second.id not in {n["id"] for n in graph["nodes"]}  # declined: out of the way
    assert second.id in {n["id"] for n in everything["nodes"]}  # ...but never deleted


async def test_nobody_else_and_no_other_mode_can_answer(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(store, settings, gym(SPLIT, tentative=True))
    maybe = (await say(ingest)).created[0]
    for principal in (me(Mode.WORKSPACE), me(Mode.PERSONAL, user="sara")):
        async with await _client(memories, pending, principal) as client:
            assert (await client.post(f"/memories/{maybe.id}/decline")).status_code == 404
    assert (await memories.get(maybe.id)).status is MemoryStatus.PENDING


async def test_a_belief_is_not_answered_like_a_pending_item(store, settings):  # noqa: F811
    ingest, memories, pending, _ = await setup(store, settings, gym(SPLIT))
    belief = (await say(ingest)).created[0]
    async with await _client(memories, pending, me(Mode.PERSONAL)) as client:
        assert (await client.post(f"/memories/{belief.id}/decline")).status_code == 409
    assert (await memories.get(belief.id)).status is MemoryStatus.ACTIVE


# --- Decided wording (FINDINGS §14) ------------------------------------------------------

MAYBE = "The user is considering a push/pull/legs split six days a week"
DECIDED = "The user will do a push/pull/legs split six days a week"


async def test_a_confirmed_maybe_is_worded_as_decided(store, settings):  # noqa: F811
    llm = StubLLM([gym(MAYBE, tentative=True)], wording=DECIDED)
    ingest = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    maybe = (await say(ingest)).created[0]

    await PendingService(ingest, memories).confirm(maybe)

    stored = await memories.get(maybe.id)
    assert stored.content == DECIDED and stored.tentative_wording == MAYBE
    # The vector moved with the words: the decided wording is what is found.
    [vector] = await llm.embed([MemoryStore.embedding_text(stored)])
    [(hit, score)] = await memories.search_by_vector(
        user_id="mark:personal", vector=vector, limit=1
    )
    assert hit.id == maybe.id and score > 0.999


async def test_a_refused_rewrite_keeps_the_words_and_still_confirms(store, settings):  # noqa: F811
    # The rewrite changed the number: the guard keeps the original.
    llm = StubLLM([gym(MAYBE, tentative=True)], wording=DECIDED.replace("six", "5"))
    ingest = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    maybe = (await say(ingest)).created[0]

    await PendingService(ingest, memories).confirm(maybe)

    stored = await memories.get(maybe.id)
    assert stored.status is MemoryStatus.ACTIVE
    assert stored.content == MAYBE and stored.tentative_wording is None


async def test_restating_a_maybe_as_decided_takes_the_new_words(store):  # noqa: F811
    from continuum.config import Settings

    # The stub puts two statements about one subject at ~0.90: a duplicate band
    # below that makes the restatement the same statement, as a real embedder does.
    near = Settings(
        embedding_dim=len((await StubLLM([[]]).embed(["x"]))[0]),
        qdrant_collection="test_memories",
        duplicate_similarity_threshold=0.85,
        conflict_similarity_threshold=0.70,
    )
    ingest, memories, _, _ = await setup(
        store, near, gym(MAYBE, tentative=True), gym(DECIDED)
    )
    maybe = (await say(ingest)).created[0]
    await say(ingest)

    stored = await memories.get(maybe.id)
    assert stored.status is MemoryStatus.ACTIVE
    assert stored.content == DECIDED and stored.tentative_wording == MAYBE
