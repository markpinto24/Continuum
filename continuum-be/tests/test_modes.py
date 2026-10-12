"""Personal and Workspace: two graphs per person that never mix (requirements.md §3).

The mode picks which of the caller's OWN graphs a request uses. These tests pin
the separation from every side: the owner-id rule, the mode header, API-key
scoping, lookups by id, the team space, and the resolver and retrieval running
against real in-memory Qdrant.
"""

from __future__ import annotations

import pytest

from continuum.models.auth import Principal
from continuum.models.memory import (
    SHARED_SPACE,
    Memory,
    MemoryCategory,
    Mode,
    graph_owner,
    mode_of,
    visible_owners,
)
from continuum.models.schemas import IngestRequest
from continuum.services.retrieval import RetrievalService
from tests.test_auth import BROWSER, build
from tests.test_ingest_pipeline import settings, store  # noqa: F401
from tests.test_sharing import atlas, setup, share

PERSONAL = {**BROWSER, "x-continuum-mode": "personal"}
WORK = {**BROWSER, "x-continuum-mode": "workspace"}


# --- The owner-id rule ---------------------------------------------------------------


def test_each_mode_is_its_own_graph():
    assert graph_owner("mark", Mode.WORKSPACE) == "mark"  # unchanged: old memories stay put
    assert graph_owner("mark", Mode.PERSONAL) == "mark:personal"
    assert mode_of("mark:personal") is Mode.PERSONAL and mode_of("mark") is Mode.WORKSPACE


def test_a_personal_graph_never_sees_the_team_space():
    assert visible_owners("mark") == ["mark", SHARED_SPACE]
    assert visible_owners("mark:personal") == ["mark:personal"]
    principal = Principal(user_id="mark", email="m@x.io", via="session", mode=Mode.PERSONAL)
    assert principal.owner == "mark:personal" and principal.owners() == ["mark:personal"]


# --- The mode header, keys and accounts -------------------------------------------------


async def test_the_header_picks_the_graph_and_never_another_person():
    env = await build()
    await env.user("mark@example.com", user_id="mark")
    async with await env.signed_in("mark@example.com") as browser:
        for headers in (PERSONAL, WORK, BROWSER):
            # The last names no mode: an adopted pre-auth id opens in Workspace.
            response = await browser.post("/ingest", json={"text": "a"}, headers=headers)
            assert response.status_code == 201
    assert env.ingest.users == ["mark:personal", "mark", "mark"]


async def test_an_unknown_mode_is_refused_not_defaulted():
    env = await build()
    await env.user("mark@example.com", user_id="mark")
    async with await env.signed_in("mark@example.com") as browser:
        response = await browser.post(
            "/ingest", json={"text": "a"}, headers={**BROWSER, "x-continuum-mode": "persnal"}
        )
    assert response.status_code == 400 and env.ingest.users == []


async def test_a_new_account_opens_in_personal():
    env = await build()
    user = await env.user("new@example.com")
    assert user.default_mode is Mode.PERSONAL
    async with await env.signed_in("new@example.com") as browser:
        me = (await browser.get("/auth/me")).json()
        await browser.post("/ingest", json={"text": "a"})
    assert me["mode"] == "personal" and me["default_mode"] == "personal"
    assert env.ingest.users == [f"{user.id}:personal"]


async def test_the_default_mode_is_a_persons_choice_and_not_a_keys():
    env = await build()
    await env.user("mark@example.com", user_id="mark")
    async with await env.signed_in("mark@example.com") as browser:
        changed = await browser.patch("/auth/mode", json={"default_mode": "personal"})
        assert changed.status_code == 200 and changed.json()["default_mode"] == "personal"
        key = (await browser.post("/auth/keys", json={"name": "agent"})).json()["secret"]
    async with env.client() as agent:
        refused = await agent.patch(
            "/auth/mode", json={"default_mode": "workspace"},
            headers={"Authorization": f"Bearer {key}"},
        )
    assert refused.status_code == 403


async def test_a_key_is_scoped_to_one_mode_and_never_switches():
    env = await build()
    await env.user("mark@example.com", user_id="mark")
    async with await env.signed_in("mark@example.com") as browser:
        work_key = (await browser.post("/auth/keys", json={"name": "work agent"})).json()
        life_key = (
            await browser.post("/auth/keys", json={"name": "life agent", "mode": "personal"})
        ).json()
        listed = (await browser.get("/auth/keys")).json()["keys"]
    assert {k["name"]: k["mode"] for k in listed} == {
        "work agent": "workspace", "life agent": "personal",
    }
    work = {"Authorization": f"Bearer {work_key['secret']}"}
    life = {"Authorization": f"Bearer {life_key['secret']}"}
    async with env.client() as agent:
        assert (await agent.post("/ingest", json={"text": "a"}, headers=work)).status_code == 201
        assert (await agent.post("/ingest", json={"text": "b"}, headers=life)).status_code == 201
        switch = await agent.post(
            "/ingest", json={"text": "c"}, headers={**work, "x-continuum-mode": "personal"}
        )
        assert (await agent.get("/auth/me", headers=life)).json()["mode"] == "personal"
    assert switch.status_code == 403
    assert env.ingest.users == ["mark", "mark:personal"]


async def test_a_memory_from_the_other_mode_does_not_exist_by_id():
    env = await build()
    await env.user("mark@example.com", user_id="mark")
    work = env.memories.add("mark", "Atlas uses Postgres")
    life = env.memories.add("mark:personal", "Mark is allergic to penicillin")
    async with await env.signed_in("mark@example.com") as browser:
        assert (await browser.get(f"/memories/{work.id}", headers=PERSONAL)).status_code == 404
        assert (await browser.get(f"/memories/{life.id}", headers=WORK)).status_code == 404
        assert (await browser.get(f"/memories/{life.id}", headers=PERSONAL)).status_code == 200


async def test_personal_memory_is_never_shared():
    env = await build()
    await env.user("mark@example.com", user_id="mark")
    async with await env.signed_in("mark@example.com") as browser:
        ingest = await browser.post("/ingest", json={"text": "a", "share": True}, headers=PERSONAL)
    assert ingest.status_code == 409 and env.ingest.users == []


# --- The resolver and retrieval, on real in-memory Qdrant -----------------------------


async def test_a_personal_fact_never_meets_a_work_one_in_the_resolver(store, settings):  # noqa: F811
    ingest, memories, _, llm = await setup(
        store, settings, atlas("Atlas uses Postgres"), atlas("Atlas uses Mongo"),
        judgement={"relation": "conflict", "confidence": 0.95, "reason": "which?"},
    )
    await ingest.ingest(IngestRequest(user_id="mark", text="a"))
    second = await ingest.ingest(IngestRequest(user_id="mark:personal", text="b"))
    assert llm.judge_calls == 0  # never compared
    assert second.conflicts_raised == [] and second.team_conflicts == []
    assert [m.user_id for m in await memories.list_all(user_id="mark:personal")] == [
        "mark:personal"
    ]


async def test_retrieval_in_personal_mode_sees_only_the_personal_graph(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(
        store, settings, atlas("Atlas uses Postgres"), atlas("Atlas has a weekly release"),
        atlas("Atlas is my side project"),
    )
    await share(sharing, ingest, "sara")  # team knowledge
    await ingest.ingest(IngestRequest(user_id="mark", text="b"))  # Mark's work
    await ingest.ingest(IngestRequest(user_id="mark:personal", text="c"))  # Mark's life
    context = await RetrievalService(memories, settings).retrieve(
        user_id="mark:personal", query="[atlas] Atlas"
    )
    assert {item.memory.user_id for item in context.memories} == {"mark:personal"}


@pytest.mark.parametrize("owner", ["mark:personal"])
async def test_a_personal_memory_cannot_be_shared_by_the_service(store, settings, owner):  # noqa: F811
    from continuum.services.sharing import SharingError

    _, memories, sharing, _ = await setup(store, settings)
    memory = Memory(user_id=owner, content="Mark is allergic to penicillin",
                    category=MemoryCategory.FACT, subject="mark")
    await memories.add(memory)
    with pytest.raises(SharingError):
        await sharing.share(memory, author="mark", author_email="m@x.io")


async def test_a_download_link_names_the_mode_in_its_query_but_a_write_cannot():
    env = await build()
    await env.user("mark@example.com", user_id="mark")
    life = env.memories.add("mark:personal", "Mark is allergic to penicillin")
    async with await env.signed_in("mark@example.com") as browser:
        found = await browser.get(f"/memories/{life.id}?mode=personal")
        clash = await browser.get(f"/memories/{life.id}?mode=personal", headers=WORK)
        write = await browser.post("/ingest?mode=personal", json={"text": "a"})
    assert found.status_code == 200 and clash.status_code == 400
    assert write.status_code == 201 and env.ingest.users == ["mark"]  # the query is ignored
