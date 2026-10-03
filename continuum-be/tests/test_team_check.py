"""Private beliefs checked against the team's.

A new private fact that disagrees with team knowledge is flagged on the private
side only — even when the judge is sure — and settled by its owner: the team
holds, mine holds (share it, and let the team graph decide), or both hold.
"""

from __future__ import annotations

from continuum.models.memory import SHARED_SPACE, MemoryStatus
from continuum.models.schemas import IngestRequest
from continuum.services.retrieval import RetrievalService
from tests.test_ingest_pipeline import settings, store  # noqa: F401
from tests.test_sharing import app_for, atlas, call, mine, setup, share

CONFLICT = {"relation": "conflict", "confidence": 0.9, "reason": "which database?"}
SURE = {"relation": "supersedes", "confidence": 0.99, "reason": "replaced"}


async def team_and_mine(store, settings, judgement):  # noqa: ANN001, ANN201, F811
    """The team holds "Postgres"; Sara then notes "Mongo" privately."""
    ingest, memories, sharing, llm = await setup(
        store, settings, atlas("Atlas uses Postgres"), atlas("Atlas uses Mongo"),
        judgement=judgement,
    )
    team = (await share(sharing, ingest, "mark")).shared
    result = await ingest.ingest(IngestRequest(user_id="sara", text="b"))
    return ingest, memories, sharing, llm, team, result


async def test_a_private_fact_against_the_team_is_flagged_on_the_private_side(store, settings):  # noqa: F811
    _, memories, _, _, team, result = await team_and_mine(store, settings, CONFLICT)
    private = result.created[0]

    assert result.team_conflicts == [team.id]
    stored = await memories.get(private.id)
    assert stored.status is MemoryStatus.CONTRADICTED
    assert stored.team_conflicts_with == [team.id] and stored.conflicts_with == []
    assert stored.escalation_against(team.id).forced is True  # not gate evidence
    untouched = await memories.get(team.id)
    assert untouched.status is MemoryStatus.ACTIVE and untouched.conflicts_with == []


async def test_even_a_sure_judge_never_retires_team_knowledge_from_a_private_note(
    store, settings  # noqa: F811
):
    _, memories, _, _, team, result = await team_and_mine(store, settings, SURE)
    assert result.team_conflicts == [team.id]
    assert (await memories.get(team.id)).status is MemoryStatus.ACTIVE
    escalation = result.created[0].escalation_against(team.id)
    assert escalation.judge_relation == "supersedes"


async def test_the_check_can_be_switched_off(store, settings):  # noqa: F811
    settings.team_cross_check = False
    _, _, _, _, _, result = await team_and_mine(store, settings, CONFLICT)
    assert result.team_conflicts == []


async def test_the_team_dispute_is_in_its_owners_inbox_only(store, settings):  # noqa: F811
    _, memories, sharing, _, team, result = await team_and_mine(store, settings, CONFLICT)
    sara = (await call(app_for(memories, sharing, settings, "sara"), "GET", "/conflicts")).json()
    [pair] = sara["conflicts"]
    assert pair["team"] is True
    assert pair["memory"]["id"] == result.created[0].id
    assert [m["id"] for m in pair["conflicting"]] == [team.id]
    raj = (await call(app_for(memories, sharing, settings, "raj"), "GET", "/conflicts")).json()
    assert raj["total"] == 0


async def test_team_holds_retires_mine_and_leaves_the_team_alone(store, settings):  # noqa: F811
    _, memories, sharing, _, team, result = await team_and_mine(store, settings, CONFLICT)
    mine_id = result.created[0].id
    response = await call(app_for(memories, sharing, settings, "sara"), "POST",
                          "/conflicts/resolve-team",
                          json={"memory_id": mine_id, "shared_id": team.id,
                                "decision": "team_holds"})
    assert response.status_code == 200
    stored = await memories.get(mine_id)
    assert stored.status is MemoryStatus.SUPERSEDED and stored.superseded_by == team.id
    assert (await memories.get(team.id)).supersedes == []


async def test_both_hold_clears_the_flag(store, settings):  # noqa: F811
    _, memories, sharing, _, team, result = await team_and_mine(store, settings, CONFLICT)
    mine_id = result.created[0].id
    await call(app_for(memories, sharing, settings, "sara"), "POST", "/conflicts/resolve-team",
               json={"memory_id": mine_id, "shared_id": team.id, "decision": "both_hold"})
    stored = await memories.get(mine_id)
    assert stored.status is MemoryStatus.ACTIVE and stored.team_conflicts_with == []


async def test_mine_holds_shares_it_and_the_team_graph_decides(store, settings):  # noqa: F811
    _, memories, sharing, _, team, result = await team_and_mine(store, settings, CONFLICT)
    mine_id = result.created[0].id
    response = await call(app_for(memories, sharing, settings, "sara"), "POST",
                          "/conflicts/resolve-team",
                          json={"memory_id": mine_id, "shared_id": team.id,
                                "decision": "mine_holds"})
    body = response.json()
    assert response.status_code == 200 and body["shared"]["outcome"] == "conflict"
    # Now a team dispute, in every member's inbox.
    team_inbox = await memories.list_conflicts(user_id=SHARED_SPACE)
    assert {m.content for m in team_inbox} == {"Atlas uses Postgres", "Atlas uses Mongo"}


async def test_someone_else_cannot_settle_my_team_dispute(store, settings):  # noqa: F811
    _, memories, sharing, _, team, result = await team_and_mine(store, settings, CONFLICT)
    response = await call(app_for(memories, sharing, settings, "mark"), "POST",
                          "/conflicts/resolve-team",
                          json={"memory_id": result.created[0].id, "shared_id": team.id,
                                "decision": "team_holds"})
    assert response.status_code == 404


async def test_chat_surfaces_the_team_dispute_even_if_only_the_team_side_was_retrieved(
    store, settings  # noqa: F811
):
    _, memories, _, _, team, result = await team_and_mine(store, settings, CONFLICT)
    retrieval = RetrievalService(memories, settings)
    settings.retrieval_keyword_weight = 0.0
    context = await retrieval.retrieve(user_id="sara", query="[atlas] Atlas uses Postgres")
    [dispute] = context.disagreements
    assert {m.id for m in dispute.memories} == {team.id, result.created[0].id}
    # Nobody else's private side ever appears.
    other = await retrieval.retrieve(user_id="raj", query="[atlas] Atlas uses Postgres")
    assert other.disagreements == []


async def test_an_unrelated_private_fact_is_not_flagged(store, settings):  # noqa: F811
    ingest, memories, sharing, _ = await setup(
        store, settings, atlas("Atlas uses Postgres"),
        [{"content": "Billing moved to Stripe", "category": "decision", "subject": "billing"}],
        judgement=CONFLICT,
    )
    await share(sharing, ingest, "mark")
    private = await mine(ingest, user="sara")
    assert (await memories.get(private.id)).team_conflicts_with == []
