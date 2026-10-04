"""The graph draws each relation once, and only relations that hold.

Found live: the header said "2 edges" with one visible. Settling a dispute had
left the loser holding its conflict edge, drawn underneath the new supersede
arrow — and a memory shared to the team had no edge to its shared copy at all.
"""

from __future__ import annotations

from continuum.models.memory import SHARED_SPACE, Memory, MemoryCategory, MemoryStatus
from tests.test_ingest_pipeline import settings, store  # noqa: F401
from tests.test_sharing import app_for, atlas, call, mine, setup


async def graph(memories, sharing, settings, user="mark"):  # noqa: ANN001, ANN201, F811
    return (await call(app_for(memories, sharing, settings, user), "GET", "/memories/graph")).json()


async def test_settling_a_dispute_leaves_one_arrow_and_no_stale_dispute(store, settings):  # noqa: F811
    settings.team_cross_check = False
    ingest, memories, sharing, _ = await setup(
        store, settings, atlas("Atlas uses Postgres"), atlas("Atlas uses Mongo"),
        judgement={"relation": "conflict", "confidence": 0.9, "reason": "which?"},
    )
    old = await mine(ingest, text="a")
    new = await mine(ingest, text="b")
    app = app_for(memories, sharing, settings, "mark")
    response = await call(app, "POST", "/conflicts/resolve",
                          json={"winner_id": old.id, "loser_ids": [new.id]})
    assert response.status_code == 200

    assert (await memories.get(new.id)).conflicts_with == []  # the loser's side too
    body = await graph(memories, sharing, settings)
    assert [(e["source"], e["target"], e["kind"]) for e in body["edges"]] == [
        (old.id, new.id, "supersedes")
    ]


async def test_a_stale_dispute_on_a_superseded_memory_is_not_drawn(store, settings):  # noqa: F811
    _, memories, sharing, _ = await setup(store, settings)
    winner = Memory(user_id="mark", content="Atlas uses Postgres",
                    category=MemoryCategory.DECISION, subject="atlas")
    loser = Memory(user_id="mark", content="Atlas uses Mongo",
                   category=MemoryCategory.DECISION, subject="atlas")
    # The shape the old resolve route left behind.
    winner.mark_supersedes(loser.id)
    loser.mark_contradicted(winner.id)
    loser.mark_superseded_by(winner.id)
    await memories.add_many([winner, loser])
    body = await graph(memories, sharing, settings)
    assert [e["kind"] for e in body["edges"]] == ["supersedes"]


async def test_a_memory_shared_to_the_team_is_linked_to_its_shared_copy(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(store, settings, atlas("Atlas uses Postgres"))
    private = await mine(ingest)
    shared = (await sharing.share(private, author="mark", author_email="m@x.io")).shared
    body = await graph(memories, sharing, settings)
    assert (await memories.get(private.id)).status is MemoryStatus.SUPERSEDED
    assert shared.user_id == SHARED_SPACE
    assert [(e["source"], e["target"], e["kind"]) for e in body["edges"]] == [
        (shared.id, private.id, "supersedes")
    ]
