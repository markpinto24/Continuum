"""What the system has learned from the conflicts you settled.

Labels are written by /conflicts/resolve, never here. What is written here is
a person's explicit approval of a rule — nothing becomes a rule on its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import Field

from continuum.api.deps import (
    CurrentUser,
    FeedbackDep,
    MemoryStoreDep,
    SessionUser,
    SettingsDep,
)
from continuum.models.feedback import (
    AnswerFeedback,
    CalibrationReport,
    FeedbackSummary,
    GateEvidence,
    ResolutionRule,
    RuleList,
)
from continuum.models.memory import SHARED_SPACE, visible_owners
from continuum.models.schemas import ClientBody, LabelListResponse

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("/labels", response_model=LabelListResponse)
async def list_labels(
    principal: CurrentUser, feedback: FeedbackDep, limit: int = Query(200, ge=1, le=1000)
) -> LabelListResponse:
    """Your decisions, newest first, each with what the resolver had thought."""
    labels = await feedback.labels(principal.user_id, limit=limit)
    return LabelListResponse(total=len(labels), labels=labels)


@router.get("/evidence", response_model=GateEvidence)
async def evidence(
    principal: CurrentUser,
    feedback: FeedbackDep,
    scope: Literal["mine", "team"] = "mine",
) -> GateEvidence:
    """Would a lower auto-supersede gate have been safe? Counted from your
    decisions, or from everyone's (`scope=team`) — the gate is one setting for
    the whole instance, so pooled evidence reaches a verdict sooner. Only counts
    leave the labels; nobody's content does."""
    return await feedback.evidence(principal.user_id, team=scope == "team")


@router.get("/calibration", response_model=CalibrationReport)
async def calibration(principal: CurrentUser, feedback: FeedbackDep) -> CalibrationReport:
    """How often people agreed with the judge at each confidence, pooled."""
    return await feedback.calibration_report()


# --- Rules -------------------------------------------------------------------------


class RuleBody(ClientBody):
    subject: str = Field(..., min_length=1, max_length=200)
    scope: Literal["mine", "team"] = "mine"


def _rule_owners(principal: CurrentUser, settings: SettingsDep) -> list[str]:
    return visible_owners(principal.user_id, shared=settings.shared_space_enabled)


@router.get("/rules", response_model=RuleList)
async def list_rules(
    principal: CurrentUser, feedback: FeedbackDep, settings: SettingsDep
) -> RuleList:
    """Approved rules for your graph and the team's, plus suggestions: subjects
    where you have said "both are true" repeatedly."""
    return await feedback.rules(_rule_owners(principal, settings))


@router.post("/rules", response_model=ResolutionRule, status_code=status.HTTP_201_CREATED)
async def approve_rule(
    body: RuleBody, principal: SessionUser, feedback: FeedbackDep, settings: SettingsDep
) -> ResolutionRule:
    """Approve "statements about this subject can all hold": future conflicts
    about it are stored side by side instead of escalated.

    A person only (not an API key). A rule for the team graph changes what every
    member's resolver does, so it takes an admin.
    """
    if body.scope == "team":
        if not settings.shared_space_enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no team space.")
        if not principal.is_admin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Only an admin can add a rule for the team graph."
            )
    owner = SHARED_SPACE if body.scope == "team" else principal.user_id
    return await feedback.approve_rule(
        owner=owner, subject=body.subject.strip(), approved_by=principal.user_id
    )


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_rule(
    rule_id: str, principal: SessionUser, feedback: FeedbackDep, settings: SettingsDep
) -> Response:
    """Revoke a rule. Kept in the table with its revocation time, not deleted."""
    owners = [principal.user_id]
    if principal.is_admin and settings.shared_space_enabled:
        owners.append(SHARED_SPACE)
    try:
        await feedback.revoke_rule(rule_id, owners=owners)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such rule.") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/export", response_class=PlainTextResponse)
async def export(
    principal: CurrentUser,
    feedback: FeedbackDep,
    memories: MemoryStoreDep,
    settings: SettingsDep,
    kind: Literal["resolution", "extraction", "retrieval"] = "resolution",
) -> PlainTextResponse:
    """What you taught Continuum, as evaluation cases.

    resolution: settled conflicts, for `run_eval.py --add-cases`.
    extraction: rejected memories, for `--extraction --add-extraction`.
    retrieval: memories you said should have come up, for `--retrieval`.
    """
    if kind == "extraction":
        body, name = await feedback.extraction_yaml(principal.user_id), "extraction"
    elif kind == "retrieval":
        owners = set(_rule_owners(principal, settings))
        answers = await feedback.learning.answers(principal.user_id) if feedback.learning else []
        ids = sorted({mid for a in answers for mid in a.missing_ids})
        lookup = {
            mid: m for mid, m in (await memories.resolve_ids(ids)).items() if m.user_id in owners
        }
        body, name = await feedback.retrieval_yaml(principal.user_id, lookup), "retrieval"
    else:
        body, name = await feedback.export_yaml(principal.user_id), "cases"
    return PlainTextResponse(
        body,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="continuum-{name}.yaml"'},
    )


# --- Answers ------------------------------------------------------------------------


class AnswerBody(ClientBody):
    query: str = Field(..., min_length=1, max_length=4000)
    answer: str = Field(..., max_length=20_000)
    rating: Literal[-1, 0, 1] = Field(
        ..., description="+1 helpful, -1 not; 0 when only reporting a missing memory."
    )
    note: str | None = Field(default=None, max_length=1000)
    used_ids: list[str] = Field(default_factory=list, max_length=100)
    cited_ids: list[str] = Field(default_factory=list, max_length=100)
    missing_ids: list[str] = Field(default_factory=list, max_length=20)


@router.post("/answer", status_code=status.HTTP_201_CREATED)
async def rate_answer(
    body: AnswerBody,
    principal: CurrentUser,
    feedback: FeedbackDep,
    memories: MemoryStoreDep,
    settings: SettingsDep,
) -> dict[str, int]:
    """Rate an answer, and name memories that should have come up.

    Ids you cannot see are dropped silently rather than refused: answering
    "not found" for some and not others would confirm which ids exist.
    """
    owners = set(_rule_owners(principal, settings))
    wanted = sorted(set(body.used_ids + body.cited_ids + body.missing_ids))
    visible = {mid for mid, m in (await memories.resolve_ids(wanted)).items()
               if m.user_id in owners}
    item = AnswerFeedback(
        id=uuid.uuid4().hex,
        user_id=principal.user_id,
        created_at=datetime.now(UTC),
        query=body.query,
        answer=body.answer,
        rating=body.rating,
        note=body.note,
        used_ids=[i for i in body.used_ids if i in visible],
        cited_ids=[i for i in body.cited_ids if i in visible],
        missing_ids=[i for i in body.missing_ids if i in visible],
    )
    await feedback.record_answer(item)
    return {"missing": len(item.missing_ids)}


@router.get("/summary", response_model=FeedbackSummary)
async def summary(principal: CurrentUser, feedback: FeedbackDep) -> FeedbackSummary:
    """Everything you have taught Continuum, in counts."""
    return await feedback.summary(principal.user_id)
