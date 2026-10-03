"""The shared team space: sharing goes through the resolver, and visibility holds.

Runs the real IngestService, resolver and in-memory Qdrant from the ingest
pipeline tests — sharing is only interesting where it meets the resolver.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from continuum.api.routes import conflicts as conflicts_route
from continuum.api.routes import memories as memories_route
from continuum.config import get_settings
from continuum.models.memory import SHARED_SPACE, Memory, MemoryCategory, MemoryStatus
from continuum.models.schemas import IngestRequest
from continuum.services.feedback import FeedbackService
from continuum.services.memory_store import MemoryStore
from continuum.services.sharing import SharingDisabled, SharingError, SharingService
from tests.auth_helpers import sign_in_as
from tests.test_ingest_pipeline import StubLLM, build_service, settings, store  # noqa: F401


def atlas(content: str) -> list[dict]:
    return [{"content": content, "category": "decision", "subject": "atlas"}]


async def setup(store, settings, *batches, judgement=None):  # noqa: ANN001, ANN201, F811
    llm = StubLLM(list(batches) or [[]], judgement=judgement)
    ingest = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    return ingest, memories, SharingService(ingest, memories, settings), llm


async def mine(ingest, text: str = "a", user: str = "mark") -> Memory:  # noqa: ANN001
    return (await ingest.ingest(IngestRequest(user_id=user, text=text))).created[0]


async def share(sharing, ingest, user: str):  # noqa: ANN001, ANN201
    """`user` records a fact privately, then shares it."""
    memory = await mine(ingest, user=user)
    return await sharing.share(memory, author=user, author_email=f"{user}@x.io")


# --- Sharing a memory -------------------------------------------------------------


async def test_sharing_creates_team_knowledge_and_retires_the_private_copy(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(store, settings, atlas("Atlas uses Postgres"))
    private = await mine(ingest)

    result = await sharing.share(private, author="mark", author_email="mark@example.com")

    assert result.outcome == "created"
    assert result.shared.user_id == SHARED_SPACE
    assert result.shared.shared_by == "mark" and result.shared.shared_by_email == "mark@example.com"
    assert result.shared.content == "Atlas uses Postgres"
    original = await memories.get(private.id)
    # Retired by an edge, never deleted (commitment 1).
    assert original.status is MemoryStatus.SUPERSEDED
    assert original.superseded_by == result.shared.id and original.shared_as == result.shared.id


async def test_sharing_what_the_team_already_knows_merges_instead_of_duplicating(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(store, settings, atlas("Atlas uses Postgres"))
    first = await share(sharing, ingest, "mark")
    second = await share(sharing, ingest, "sara")

    assert second.outcome == "merged"
    assert second.shared.id == first.shared.id
    shared = await memories.list_all(user_id=SHARED_SPACE)
    assert len(shared) == 1 and shared[0].reinforcement_count == 1


async def test_sharing_a_contradiction_raises_a_shared_conflict(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(
        store, settings, atlas("Atlas uses Postgres"), atlas("Atlas uses Mongo"),
        judgement={"relation": "conflict", "confidence": 0.9, "reason": "partial"},
    )
    await share(sharing, ingest, "mark")
    result = await share(sharing, ingest, "sara")

    assert result.outcome == "conflict"
    team = await memories.list_conflicts(user_id=SHARED_SPACE)
    assert {m.content for m in team} == {"Atlas uses Postgres", "Atlas uses Mongo"}


@pytest.mark.parametrize("state", ["contradicted", "superseded", "shared"])
async def test_only_an_active_private_memory_can_be_shared(store, settings, state):  # noqa: F811
    ingest, memories, sharing, _ = await setup(store, settings, atlas("Atlas uses Postgres"))
    memory = await mine(ingest)
    if state == "contradicted":
        memory.mark_contradicted("other")
    elif state == "superseded":
        memory.mark_superseded_by("other")
    else:
        memory.user_id = SHARED_SPACE

    with pytest.raises(SharingError):
        await sharing.share(memory, author="mark", author_email="m@x.io")


async def test_sharing_can_be_switched_off(store, settings):  # noqa: F811
    settings.shared_space_enabled = False
    ingest, _, sharing, _ = await setup(store, settings, atlas("Atlas uses Postgres"))
    with pytest.raises(SharingDisabled):
        await sharing.share(await mine(ingest), author="mark", author_email="m@x.io")


async def test_a_shared_ingest_is_owned_by_the_space_and_signed_by_its_author(store, settings):  # noqa: F811
    ingest, _, _, _ = await setup(store, settings, atlas("Raj leads mobile"))
    result = await ingest.ingest(
        IngestRequest(user_id=SHARED_SPACE, text="x", author="mark", author_email="m@x.io")
    )
    assert result.created[0].user_id == SHARED_SPACE
    assert result.created[0].shared_by == "mark"


# --- Visibility through the API ----------------------------------------------------


def app_for(memories: MemoryStore, sharing: SharingService, settings, user: str) -> FastAPI:  # noqa: ANN001, F811
    app = FastAPI()
    app.include_router(memories_route.router)
    app.include_router(conflicts_route.router)
    app.state.memories = memories
    app.state.sharing = sharing
    app.state.feedback = FeedbackService(_NullLabels(), settings)  # type: ignore[arg-type]
    app.dependency_overrides[get_settings] = lambda: settings
    sign_in_as(app, user)
    return app


class _NullLabels:
    async def add_many(self, _labels):  # noqa: ANN001, ANN202
        return None

    async def list_all(self, limit: int = 10_000):  # noqa: ANN201, ARG002
        return []


async def call(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:  # noqa: ANN003
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


async def test_everyone_sees_shared_knowledge_and_nobody_sees_private(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(
        store, settings, atlas("Atlas uses Postgres"), atlas("Mark's private note")
    )
    shared = (await sharing.share(await mine(ingest), author="mark", author_email="m@x.io")).shared
    private = await mine(ingest, user="mark")
    sara = app_for(memories, sharing, settings, "sara")

    graph = (await call(sara, "GET", "/memories/graph")).json()
    assert [n["label"] for n in graph["nodes"]] == ["Atlas uses Postgres"]
    assert graph["nodes"][0]["shared"] is True
    assert graph["nodes"][0]["shared_by_email"] == "m@x.io"
    assert (await call(sara, "GET", f"/memories/{shared.id}")).status_code == 200
    assert (await call(sara, "GET", f"/memories/{private.id}")).status_code == 404


async def test_sharing_through_the_api_and_refusing_to_share_twice(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(store, settings, atlas("Atlas uses Postgres"))
    private = await mine(ingest)
    mark = app_for(memories, sharing, settings, "mark")

    first = await call(mark, "POST", f"/memories/{private.id}/share")
    assert first.status_code == 200 and first.json()["outcome"] == "created"
    shared_id = first.json()["shared"]["id"]
    assert (await call(mark, "POST", f"/memories/{shared_id}/share")).status_code == 409
    sara = app_for(memories, sharing, settings, "sara")
    # Someone else's memory cannot be shared on their behalf: it does not exist for Sara.
    other = await mine(ingest, user="mark")
    assert (await call(sara, "POST", f"/memories/{other.id}/share")).status_code == 404


async def test_a_shared_conflict_is_settled_by_anyone_but_never_reaches_a_private_graph(
    store, settings  # noqa: F811
):
    # About the decision crossing graphs, not the ingest-time team check (which
    # would flag the private note against the team, since the stub judge says
    # "conflict" to everything).
    settings.team_cross_check = False
    ingest, memories, sharing, _ = await setup(
        store, settings, atlas("Atlas uses Postgres"), atlas("Atlas uses Mongo"),
        atlas("Mark's private Atlas note"),
        judgement={"relation": "conflict", "confidence": 0.9, "reason": "partial"},
    )
    await share(sharing, ingest, "mark")
    newer = (await share(sharing, ingest, "sara")).shared
    private = await mine(ingest, user="mark")
    raj = app_for(memories, sharing, settings, "raj")

    inbox = (await call(raj, "GET", "/conflicts")).json()
    assert inbox["total"] == 1  # shared disputes reach every member's inbox

    older = next(m for m in await memories.list_all(user_id=SHARED_SPACE) if m.id != newer.id)
    response = await call(
        raj, "POST", "/conflicts/resolve",
        json={"winner_id": newer.id, "loser_ids": [older.id, private.id]},
    )
    assert response.status_code == 200
    # The private memory named as a loser is left alone.
    assert [m["id"] for m in response.json()["losers"]] == [older.id]
    assert (await memories.get(private.id)).status is MemoryStatus.ACTIVE
    assert private.category is MemoryCategory.DECISION
