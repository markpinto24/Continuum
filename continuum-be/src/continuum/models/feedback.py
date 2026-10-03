"""Labels from real use: conflicts people settled, and what the resolver thought.

Pure models, no I/O — the same rule as `memory.py`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Decision(StrEnum):
    """What the person said about an older/newer pair."""

    NEWER_HOLDS = "newer_holds"  # the old belief is out of date
    OLDER_HOLDS = "older_holds"  # the new statement was wrong, or already obsolete
    BOTH_HOLD = "both_hold"  # never a real contradiction


class ExpectedAction(StrEnum):
    """What the resolver SHOULD have done, in the corpus's vocabulary."""

    RETIRE = "retire"
    ESCALATE = "escalate"
    STORE = "store"


# Why each decision maps to its action:
# - newer holds: retiring the old belief was right; escalating cost a click.
# - both hold:   nothing to retire; the pair should have been stored side by side.
# - older holds: the incoming statement was the wrong one. The resolver cannot
#   know that from the text — no automatic action would have been right — so
#   asking was the correct call, and auto-retiring would have destroyed a true
#   belief. That makes it the most valuable label of the three.
EXPECTED_ACTION = {
    Decision.NEWER_HOLDS: ExpectedAction.RETIRE,
    Decision.BOTH_HOLD: ExpectedAction.STORE,
    Decision.OLDER_HOLDS: ExpectedAction.ESCALATE,
}


class ResolutionLabel(BaseModel):
    id: str
    user_id: str
    created_at: datetime

    existing_memory_id: str
    existing_content: str
    existing_category: str
    existing_subject: str | None = None
    existing_confidence: float
    existing_age_days: float
    incoming_memory_id: str
    incoming_content: str
    incoming_category: str
    incoming_subject: str | None = None

    judge_relation: str | None = None
    judge_confidence: float | None = None
    similarity: float | None = None
    gate: float | None = None
    forced: bool = False

    decision: Decision
    expected_action: ExpectedAction
    # The graph the pair lived in (a user id or the shared space); "cross" for a
    # private belief settled against team knowledge.
    graph_owner: str | None = None


class GateBand(BaseModel):
    """Escalated `supersedes` verdicts in one confidence band, and how people ruled."""

    low: float
    high: float
    total: int
    confirmed: int = Field(..., description="The person said the newer one holds.")
    refuted: int = Field(..., description="Retiring would have lost a true belief.")


class GateEvidence(BaseModel):
    """Whether real decisions justify changing auto_supersede_confidence.

    Only escalated `supersedes` verdicts below the gate are evidence about the
    gate: they are exactly what a lower gate would have retired automatically.
    """

    gate: float
    labels: int = Field(..., description="Every decision recorded.")
    with_judgement: int = Field(..., description="Decisions where the judge's view was recorded.")
    bands: list[GateBand]
    # The largest error rate still consistent with zero failures in n trials,
    # at 95% ("rule of three": 3/n). None when there is nothing to bound.
    below_gate_total: int
    below_gate_refuted: int
    error_upper_bound: float | None
    role_rule_total: int = Field(..., description="Escalated by the role rule.")
    role_rule_shared: int = Field(..., description="…of which both people really held the role.")
    conflicts_total: int
    conflicts_both_hold: int = Field(
        ..., description="Judged 'conflict' but both were true: over-escalation."
    )
    recommendation: str


# --- Calibration -----------------------------------------------------------------


class CalibrationPoint(BaseModel):
    """How often the judge was right when it said `supersedes` in one band."""

    low: float
    high: float
    total: int
    confirmed: int
    observed: float | None = Field(..., description="confirmed / total, or None if empty.")
    calibrated: float = Field(
        ..., description="The band's probability after smoothing and forcing it to rise "
        "with the judge's confidence — what the gate compares when calibration is on."
    )


class CalibrationReport(BaseModel):
    """The judge's stated confidence against how often people agreed with it.

    Pooled across everyone on the instance: how well-calibrated the judge is is a
    property of the model, not of one person's graph.
    """

    labels: int
    points: list[CalibrationPoint]
    usable: bool = Field(..., description="Enough decisions to trust the curve.")
    in_use: bool = Field(..., description="CALIBRATED_GATE is on and the curve is usable.")
    min_labels: int


# --- Rules ---------------------------------------------------------------------


class ResolutionRule(BaseModel):
    id: str
    owner: str
    subject: str
    kind: str = "compatible"
    created_by: str
    created_at: datetime
    revoked_at: datetime | None = None


class RuleSuggestion(BaseModel):
    """A rule worth proposing: the person keeps saying both are true about this subject."""

    owner: str
    subject: str
    both_hold: int = Field(..., description="Times you said both statements were true.")


class RuleList(BaseModel):
    rules: list[ResolutionRule]
    suggestions: list[RuleSuggestion]


# --- Extraction and answer feedback ------------------------------------------------


class RejectReason(StrEnum):
    NOT_A_FACT = "not_a_fact"  # a question, a greeting, a guess
    MERGED = "merged"  # two separate facts joined into one memory
    MISREAD = "misread"  # the text says something else
    OTHER = "other"


class ExtractionFeedback(BaseModel):
    id: str
    user_id: str
    created_at: datetime
    memory_id: str
    content: str
    category: str
    source_excerpt: str | None = None
    reason: RejectReason
    note: str | None = None


class AnswerFeedback(BaseModel):
    id: str
    user_id: str
    created_at: datetime
    query: str
    answer: str
    rating: int = Field(..., description="+1, -1, or 0 for a missing-memory report only.")
    note: str | None = None
    used_ids: list[str] = Field(default_factory=list)
    cited_ids: list[str] = Field(default_factory=list)
    missing_ids: list[str] = Field(
        default_factory=list, description="Memories the person says should have come up."
    )


class FeedbackSummary(BaseModel):
    """Everything the system has been taught, in counts."""

    decisions: int
    rejected_facts: int
    answers_up: int
    answers_down: int
    missing_memories: int
