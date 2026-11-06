"""The contradiction inbox.

Phase 2's answer to ambiguity is a human, so there has to be somewhere for the
ambiguity to land. This is it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from continuum.api.deps import (
    CurrentUser,
    FeedbackDep,
    MemoryStoreDep,
    SettingsDep,
    SharingDep,
    limit_llm_requests,
)
from continuum.models.auth import Principal
from continuum.models.memory import SHARED_SPACE
from continuum.models.schemas import (
    ConflictListResponse,
    ConflictPair,
    ConflictResolutionRequest,
    ConflictResolutionResponse,
    TeamResolutionRequest,
    TeamResolutionResponse,
)
from continuum.services.sharing import SharingError

router = APIRouter(prefix="/conflicts", tags=["conflicts"])


@router.get("", response_model=ConflictListResponse)
async def list_conflicts(
    principal: CurrentUser,
    memories: MemoryStoreDep,
    settings: SettingsDep,
    limit: int = Query(100, ge=1, le=500),
) -> ConflictListResponse:
    """Unresolved disagreements awaiting a human decision."""
    # Yours, and the shared space's: any member may settle team knowledge.
    owners = principal.owners(shared=settings.shared_space_enabled)
    flagged = await memories.list_conflicts(user_id=owners, limit=limit)

    referenced = {cid for m in flagged for cid in m.conflicts_with + m.team_conflicts_with}
    # Ids come from stored edges, which never cross users today — but an id is
    # just a string, and this is the one place a lookup by id feeds a response.
    lookup = {
        mid: m
        for mid, m in (await memories.resolve_ids(sorted(referenced))).items()
        if m.user_id in owners
    }

    seen: set[str] = set()
    pairs: list[ConflictPair] = []
    for memory in flagged:
        # Your belief against the team's: its own entry, settled differently.
        team = [lookup[cid] for cid in memory.team_conflicts_with if cid in lookup]
        if team and memory.user_id == principal.owner:
            pairs.append(ConflictPair(memory=memory, conflicting=team, team=True))
        # Conflicts are symmetric; show each disagreement once.
        if memory.id in seen:
            continue
        others = [lookup[cid] for cid in memory.conflicts_with if cid in lookup]
        if not others:
            continue
        seen.add(memory.id)
        seen.update(o.id for o in others)
        pairs.append(ConflictPair(memory=memory, conflicting=others))

    return ConflictListResponse(total=len(pairs), conflicts=pairs)


@router.post("/resolve", response_model=ConflictResolutionResponse)
async def resolve_conflict(
    request: ConflictResolutionRequest,
    principal: CurrentUser,
    memories: MemoryStoreDep,
    feedback: FeedbackDep,
    settings: SettingsDep,
) -> ConflictResolutionResponse:
    """Apply a human verdict.

    `keep_both` clears the flags and leaves both beliefs active. Otherwise the
    losers are marked superseded by the winner — an edge, never a delete, so the
    decision stays auditable and reversible.
    """
    said = _person_decided(principal, request.said)
    via = _settled_via(principal, request.spoken, said)
    owners = principal.owners(shared=settings.shared_space_enabled)
    winner = await memories.get(request.winner_id)
    if not winner or winner.user_id not in owners:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Winner memory not found")

    loser_ids = request.loser_ids or [c for c in winner.conflicts_with]
    lookup = await memories.resolve_ids(loser_ids)
    # Losers come from the winner's own graph: a decision never reaches across
    # from the shared space into anyone's private memories, or back.
    losers = [m for m in lookup.values() if m.user_id == winner.user_id]
    # Only a pair actually in dispute can be settled. Accepting any ids let any
    # member retire team beliefs nobody had disputed — or an event, which can
    # never be superseded — by naming them as "losers".
    if len(losers) != len(set(loser_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Memory not found")
    if not losers or any(
        loser.id not in winner.conflicts_with and winner.id not in loser.conflicts_with
        for loser in losers
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only memories in dispute with it can be settled against it.",
        )
    # The label describes the pair as the resolver saw it, so take it from a
    # snapshot — applying the decision below reinforces the winner — but write it
    # only once the decision has actually been applied.
    seen_winner = winner.model_copy(deep=True)
    seen_losers = [loser.model_copy(deep=True) for loser in losers]

    if request.keep_both:
        for loser in losers:
            loser.clear_conflict_with(winner.id)
            await memories.save(loser)
            winner.clear_conflict_with(loser.id)
        await memories.save(winner)
        await feedback.record_resolution(
            principal.user_id, seen_winner, seen_losers, keep_both=True,
            said=said, settled_via=via,
        )
        return ConflictResolutionResponse(winner=winner, losers=losers, action="kept_both")

    for loser in losers:
        # Both sides of the dispute are settled, not only the winner's: a loser
        # left holding the edge drew a stale dispute over its supersede arrow.
        loser.clear_conflict_with(winner.id)
        loser.mark_superseded_by(winner.id)
        await memories.save(loser)
        winner.mark_supersedes(loser.id)
        winner.clear_conflict_with(loser.id)

    winner.reinforce()
    await memories.save(winner)
    await feedback.record_resolution(
        principal.user_id, seen_winner, seen_losers, keep_both=False,
        said=said, settled_via=via,
    )

    return ConflictResolutionResponse(winner=winner, losers=losers, action="superseded")


# "Mine holds" shares it, which runs the shared graph's resolver: a judge call.
@router.post(
    "/resolve-team",
    response_model=TeamResolutionResponse,
    dependencies=[Depends(limit_llm_requests)],
)
async def resolve_team_conflict(
    request: TeamResolutionRequest,
    principal: CurrentUser,
    memories: MemoryStoreDep,
    feedback: FeedbackDep,
    sharing: SharingDep,
) -> TeamResolutionResponse:
    """Settle your private belief against the team's.

    Only your memory changes. The team's is never retired from here: if you think
    the team is out of date, `mine_holds` shares yours, and the team graph's own
    resolver — or the team, if it is unsure — decides.
    """
    said = _person_decided(principal, request.said)
    via = _settled_via(principal, request.spoken, said)
    memory = await memories.get(request.memory_id)
    if not memory or memory.user_id != principal.owner:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Memory not found")
    team = await memories.get(request.shared_id)
    if (
        not team
        or team.user_id != SHARED_SPACE
        or request.shared_id not in memory.team_conflicts_with
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such disagreement")

    seen_memory, seen_team = memory.model_copy(deep=True), team.model_copy(deep=True)

    if request.decision == "mine_holds":
        try:
            shared = await sharing.share(
                memory, author=principal.user_id, author_email=principal.email
            )
        except SharingError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc
        await feedback.record_resolution(
            principal.user_id, seen_memory, [seen_team], keep_both=False,
            said=said, settled_via=via,
        )
        return TeamResolutionResponse(memory=shared.original, action="mine_holds", shared=shared)

    memory.clear_conflict_with(team.id)
    if request.decision == "team_holds":
        memory.mark_superseded_by(team.id)
        winner, losers = seen_team, [seen_memory]
    else:
        winner, losers = seen_memory, [seen_team]
    await memories.save(memory)
    await feedback.record_resolution(
        principal.user_id, winner, losers, keep_both=request.decision == "both_hold",
        said=said, settled_via=via,
    )
    return TeamResolutionResponse(memory=memory, action=request.decision)


def _person_decided(principal: Principal, said: str | None) -> str | None:
    """The person's words, required when an agent settles (commitment 2).

    A button press in the web UI is the person deciding. An API key is an agent,
    and an agent deciding a real contradiction on its own judgement is the
    second-LLM-call arbitration the project exists to prevent — so it may only
    pass on a decision the person stated, in their words, which are kept with it.
    """
    words = (said or "").strip() or None
    if principal.via == "api_key" and words is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An agent settles a dispute only on the person's own decision: "
            "pass their words in `said`.",
        )
    return words


def _settled_via(principal: Principal, spoken: bool, said: str | None) -> str:
    """How the decision was made: a button, an agent, or aloud through Lumen.

    Voice is the person deciding, as a button is — but only once Lumen has read
    the decision back and they agreed, which the web app does before it sends
    `spoken`. So it needs their words, kept as the audit trail, and it is the web
    app's to claim: an agent relaying speech is still an agent.
    """
    if not spoken:
        return principal.via
    if principal.via != "session":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only the web app's voice mode settles by voice; an agent passes the "
            "person's words in `said`.",
        )
    if said is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A decision made aloud keeps the words that made it: pass them in `said`.",
        )
    return "voice"
