"""The contradiction inbox.

Phase 2's answer to ambiguity is a human, so there has to be somewhere for the
ambiguity to land. This is it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from continuum.api.deps import CurrentUser, FeedbackDep, MemoryStoreDep
from continuum.models.schemas import (
    ConflictListResponse,
    ConflictPair,
    ConflictResolutionRequest,
    ConflictResolutionResponse,
)

router = APIRouter(prefix="/conflicts", tags=["conflicts"])


@router.get("", response_model=ConflictListResponse)
async def list_conflicts(
    principal: CurrentUser,
    memories: MemoryStoreDep,
    limit: int = Query(100, ge=1, le=500),
) -> ConflictListResponse:
    """Unresolved disagreements awaiting a human decision."""
    flagged = await memories.list_conflicts(user_id=principal.user_id, limit=limit)

    referenced = {cid for m in flagged for cid in m.conflicts_with}
    # Ids come from stored edges, which never cross users today — but an id is
    # just a string, and this is the one place a lookup by id feeds a response.
    lookup = {
        mid: m
        for mid, m in (await memories.resolve_ids(sorted(referenced))).items()
        if m.user_id == principal.user_id
    }

    seen: set[str] = set()
    pairs: list[ConflictPair] = []
    for memory in flagged:
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
) -> ConflictResolutionResponse:
    """Apply a human verdict.

    `keep_both` clears the flags and leaves both beliefs active. Otherwise the
    losers are marked superseded by the winner — an edge, never a delete, so the
    decision stays auditable and reversible.
    """
    winner = await memories.get(request.winner_id)
    if not winner or winner.user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Winner memory not found")

    loser_ids = request.loser_ids or [c for c in winner.conflicts_with]
    lookup = await memories.resolve_ids(loser_ids)
    losers = [m for m in lookup.values() if m.user_id == principal.user_id]
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
            principal.user_id, seen_winner, seen_losers, keep_both=True
        )
        return ConflictResolutionResponse(winner=winner, losers=losers, action="kept_both")

    for loser in losers:
        loser.mark_superseded_by(winner.id)
        await memories.save(loser)
        winner.mark_supersedes(loser.id)
        winner.clear_conflict_with(loser.id)

    winner.reinforce()
    await memories.save(winner)
    await feedback.record_resolution(principal.user_id, seen_winner, seen_losers, keep_both=False)

    return ConflictResolutionResponse(winner=winner, losers=losers, action="superseded")
