"""Evaluation vocabulary.

The corpus is labelled with **actions**, not resolver verdicts, and that choice
is load-bearing.

`Verdict.NEW` and `Verdict.INDEPENDENT` are different routes to the same
behaviour: store the fact, touch nothing. One means "no comparable candidate was
found", the other "the judge looked and said both can be true". A corpus that
distinguished them would be grading the resolver on its internal path rather than
on what it did to the belief graph — and would punish a correct outcome reached
cheaply, which is the opposite of what this project wants.

So there are four actions, and they map one-to-one onto what happens to the
graph:

    RETIRE     an existing belief is superseded, unasked
    ESCALATE   the pair goes to the contradiction inbox
    REINFORCE  nothing new is stored; the existing belief is confirmed
    STORE      the fact is stored with no edges

Every metric in `metrics.py` is computed over these.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from continuum.models.memory import MemoryCategory
from continuum.services.resolution import Verdict


class Action(StrEnum):
    """What actually happened to the belief graph."""

    RETIRE = "retire"
    ESCALATE = "escalate"
    REINFORCE = "reinforce"
    STORE = "store"


VERDICT_ACTION: dict[Verdict, Action] = {
    Verdict.SUPERSEDES: Action.RETIRE,
    Verdict.CONFLICT: Action.ESCALATE,
    Verdict.DUPLICATE: Action.REINFORCE,
    Verdict.INDEPENDENT: Action.STORE,
    Verdict.NEW: Action.STORE,
}


def action_of(verdict: Verdict) -> Action:
    return VERDICT_ACTION[verdict]


class CorpusMemory(BaseModel):
    """The belief already in the graph when the new fact arrives."""

    content: str
    category: MemoryCategory
    subject: str | None = None
    confidence: float = 0.70
    age_days: float = Field(
        default=30.0,
        description=(
            "How long ago this was recorded. The judge prompt carries the date, "
            "so age is part of the input, not decoration."
        ),
    )


class CorpusFact(BaseModel):
    """The newly extracted fact being resolved against the graph."""

    content: str
    category: MemoryCategory
    subject: str | None = None


class ResolutionCase(BaseModel):
    id: str
    why: str = Field(
        ...,
        description="Why this label is correct. A case without a rationale is a guess.",
    )
    existing: CorpusMemory
    incoming: CorpusFact
    expect: Action
    tags: list[str] = Field(default_factory=list)


class CaseOutcome(BaseModel):
    """One case, run.

    Records the judge's answer *before* the confidence gate was applied, which is
    what makes `metrics.sweep` possible: one expensive pass over the corpus, then
    every gate value evaluated for free.
    """

    case_id: str
    expect: Action
    tags: list[str] = Field(default_factory=list)

    verdict: Verdict
    action: Action

    judge_relation: Verdict | None = Field(
        default=None,
        description="What the judge said before the gate. None means no LLM call was made.",
    )
    judge_confidence: float | None = None
    confidence_source: str | None = Field(
        default=None,
        description="'logprob' or 'self_report' — which number judge_confidence is.",
    )
    self_reported_confidence: float | None = Field(
        default=None,
        description="The confidence the judge wrote, kept even when logprobs were used.",
    )
    forced_escalation: bool = Field(
        default=False,
        description="Escalated by policy regardless of confidence; no gate replays it.",
    )
    similarity: float | None = Field(
        default=None, description="Cosine score of the candidate the resolver picked."
    )
    gate: float = Field(..., description="auto_supersede_confidence this run used.")
    reason: str = ""

    @property
    def used_llm(self) -> bool:
        return self.judge_relation is not None


class ExpectedFact(BaseModel):
    content: str
    category: MemoryCategory
    subject: str | None = None


class ExtractionCase(BaseModel):
    id: str
    why: str
    text: str
    expect: list[ExpectedFact] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
