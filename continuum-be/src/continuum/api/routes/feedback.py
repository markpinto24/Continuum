"""What the system has learned from the conflicts you settled.

Read-only: labels are written by /conflicts/resolve, never here.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from continuum.api.deps import CurrentUser, FeedbackDep
from continuum.models.feedback import GateEvidence
from continuum.models.schemas import LabelListResponse

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("/labels", response_model=LabelListResponse)
async def list_labels(
    principal: CurrentUser, feedback: FeedbackDep, limit: int = Query(200, ge=1, le=1000)
) -> LabelListResponse:
    """Your decisions, newest first, each with what the resolver had thought."""
    labels = await feedback.labels(principal.user_id, limit=limit)
    return LabelListResponse(total=len(labels), labels=labels)


@router.get("/evidence", response_model=GateEvidence)
async def evidence(principal: CurrentUser, feedback: FeedbackDep) -> GateEvidence:
    """Would a lower auto-supersede gate have been safe? Counted from your decisions."""
    return await feedback.evidence(principal.user_id)


@router.get("/export", response_class=PlainTextResponse)
async def export(principal: CurrentUser, feedback: FeedbackDep) -> PlainTextResponse:
    """Your decisions as Phase 5 corpus cases, for `run_eval.py --add-cases`."""
    return PlainTextResponse(
        await feedback.export_yaml(principal.user_id),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="continuum-cases.yaml"'},
    )
