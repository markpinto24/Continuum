"""The client against a mock transport that checks each request and answers as
the API does. What is pinned: the endpoints and bodies, the parsing of disputes
and pending items, the chat stream read to its `remembered` event, and that
settle never goes out without the person's words."""

from __future__ import annotations

import json

import httpx
import pytest

from continuum_sdk import AsyncContinuum, Continuum, ContinuumError


def memory(mid: str, content: str, status: str = "active") -> dict:
    return {
        "id": mid, "content": content, "category": "decision", "status": status,
        "confidence": 0.8, "created_at": "2026-10-01T09:00:00Z", "subject": "atlas",
        "source_excerpt": None, "extra_field_the_sdk_ignores": 1,
    }


POSTGRES = memory("m1", "Atlas uses Postgres", "contradicted")
MONGO = memory("m2", "Atlas uses MongoDB", "contradicted")
WRITTEN = {
    "source_id": "s", "extracted": 1, "created": [memory("m3", "Atlas uses MongoDB")],
    "duplicates_skipped": 0, "reinforced": [], "superseded": [], "conflicts_raised": ["m1"],
    "team_conflicts": [], "pending": [], "confirmed": [], "resolutions": [],
}

CHAT_STREAM = (
    "event: context\ndata: {\"query\": \"q\", \"memories\": []}\n\n"
    "event: delta\ndata: {\"text\": \"The record \"}\n\n"
    "event: delta\ndata: {\"text\": \"disagrees.\"}\n\n"
    "event: done\ndata: {\"cited_ids\": [\"m1\"], \"remembering\": true, \"questions\": []}\n\n"
    f"event: remembered\ndata: {json.dumps({'remembered': WRITTEN})}\n\n"
)


def api(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == "Bearer ck_test"
    path, body = request.url.path, json.loads(request.content or b"{}")
    if path == "/api/v1/ingest":
        return httpx.Response(200, json=WRITTEN)
    if path == "/api/v1/chat/context":
        return httpx.Response(200, json={
            "query": body["query"],
            "memories": [{"memory": POSTGRES, "similarity": 0.8, "recency": 1, "score": 0.7}],
            "disagreements": [{"subject": "atlas", "memories": [POSTGRES, MONGO]}],
            "pending": [memory("p1", "The team is considering Kafka", "pending")],
        })
    if path == "/api/v1/conflicts":
        return httpx.Response(200, json={"total": 1, "conflicts": [
            {"memory": POSTGRES, "conflicting": [MONGO], "team": False},
        ]})
    if path == "/api/v1/conflicts/resolve":
        if not body.get("said"):
            return httpx.Response(422, json={"detail": "pass their words in `said`."})
        return httpx.Response(200, json={
            "winner": MONGO | {"status": "active"}, "losers": [POSTGRES], "action": "superseded",
        })
    if path == "/api/v1/chat":
        return httpx.Response(200, text=CHAT_STREAM, headers={"content-type": "text/event-stream"})
    return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture
def sync() -> Continuum:
    return Continuum("ck_test", "http://continuum.test/", transport=httpx.MockTransport(api))


@pytest.fixture
def aio() -> AsyncContinuum:
    return AsyncContinuum("ck_test", "http://continuum.test", transport=httpx.MockTransport(api))


def test_recall_keeps_disputes_and_maybes_apart_from_beliefs(sync):
    found = sync.recall("what database does atlas use?")
    assert [r.memory.content for r in found.memories] == ["Atlas uses Postgres"]
    [dispute] = found.disputes
    assert dispute.subject == "atlas" and [m.id for m in dispute.memories] == ["m1", "m2"]
    assert [m.status for m in found.pending] == ["pending"]


def test_remember_reports_what_changed(sync):
    written = sync.remember("We moved the Atlas event store to MongoDB")
    assert written.extracted == 1 and written.disputes_raised == ["m1"]
    with pytest.raises(ValueError, match="exactly one"):
        sync.remember("a note", messages=[{"role": "user", "content": "and a turn"}])


@pytest.mark.parametrize("said", ["", "   "])
def test_settle_never_goes_out_without_the_persons_words(sync, said):
    with pytest.raises(ValueError, match="own words"):
        sync.settle("m2", said=said)


def test_settle_passes_the_persons_words_on(sync):
    settled = sync.settle("m2", said="Mongo now, we moved last week", loser_ids=["m1"])
    assert settled.action == "superseded" and settled.winner.id == "m2"


def test_chat_reads_the_stream_to_its_remembered_event(sync):
    answer = sync.chat("what database?")
    assert answer.text == "The record disagrees." and answer.cited_ids == ["m1"]
    assert answer.remembered is not None and answer.remembered.disputes_raised == ["m1"]


def test_a_refusal_carries_the_apis_reason():
    def refuses(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "This key is for Work; Personal refused."})

    client = Continuum("ck_test", transport=httpx.MockTransport(refuses))
    with pytest.raises(ContinuumError) as caught:
        client.disputes()
    assert caught.value.status == 403 and "Personal refused" in caught.value.detail


def test_no_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("CONTINUUM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="CONTINUUM_API_KEY"):
        Continuum()


async def test_the_async_client_matches_the_sync_one(aio):
    async with aio:
        found = await aio.recall("db?")
        assert [d.subject for d in found.disputes] == ["atlas"]
        [open_dispute] = await aio.disputes()
        assert open_dispute.conflicting[0].id == "m2" and not open_dispute.team
        answer = await aio.chat([{"role": "user", "content": "db?"}])
        assert answer.remembered is not None and answer.text.endswith("disagrees.")
        with pytest.raises(ValueError):
            await aio.settle("m2", said=" ")
