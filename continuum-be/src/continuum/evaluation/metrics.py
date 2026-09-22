"""Scoring, and the gate sweep.

Everything here is a pure function of recorded outcomes. That is deliberate: it
means the expensive part (one LLM pass over the corpus) happens once, and every
subsequent question — what happens at gate 0.7? at 0.9? — is arithmetic.

`CaseOutcome` stores the judge's relation and confidence *before* the gate was
applied, so `replay` can recompute what the system would have done at any other
gate without asking the model again.

The metrics, and what each one is for:

**belief_loss_rate** — the headline safety number. The fraction of the whole
corpus where the system retired a belief that should have survived. This is the
failure the project exists to prevent, and it is measured against the corpus
rather than against predictions so that a system which retires almost nothing
cannot flatter itself with a good precision score.

**stale_belief_rate** — the opposite failure, and a quieter one. A belief that
should have been retired was stored or merged instead, and *nobody was told*. An
escalation is not counted here: asking a human is a safe miss.

**escalation_rate** — what the safety costs in human attention. A gate that
escalates everything loses no beliefs and is also useless.

**free_rate** — the share of cases settled with no LLM call. Category policy and
cosine bands should be carrying most of the load.

**judge_agreement** — of the cases that reached the judge, how often its relation
matched the label, ignoring the gate. Separates "the judge is wrong" from "the
gate is set wrong", which are different problems with different fixes.

**band_miss_rate** — cases where the resolver found no candidate at all and the
label was not `store`. This measures embedding reach, not judgement: the fact
never got close enough to the belief it contradicts for anything to compare them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from continuum.evaluation.types import Action, CaseOutcome, action_of
from continuum.services.resolution import Verdict

# Gate values the sweep walks by default. Spans "escalate almost nothing" to
# "escalate almost everything" so the trade-off curve is visible end to end.
DEFAULT_GATES: tuple[float, ...] = (0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


def replay(outcome: CaseOutcome, gate: float) -> Action:
    """What the system would have done at a different confidence gate.

    Only one decision in the pipeline depends on the gate: a `supersedes` from
    the judge is applied above it and downgraded to an escalation below it.
    Everything else — the free filters, a `duplicate`, a judge that already said
    `conflict` — is gate-independent, so it replays unchanged.
    """
    if outcome.judge_relation is not Verdict.SUPERSEDES:
        return outcome.action
    confidence = outcome.judge_confidence or 0.0
    return Action.RETIRE if confidence >= gate else Action.ESCALATE


class Report(BaseModel):
    """Scored corpus at one gate value."""

    gate: float
    total: int

    # Safety
    belief_loss_rate: float = Field(..., description="Retired a belief that should have lived.")
    merge_loss_rate: float = Field(
        ..., description="Folded a distinct fact into an existing one as a duplicate."
    )
    stale_belief_rate: float = Field(
        ..., description="Should have retired, did not, and did not ask either."
    )

    # Cost
    escalation_rate: float = Field(..., description="Share routed to a human.")
    free_rate: float = Field(..., description="Share settled with no LLM call.")

    # Quality
    accuracy: float
    retire_precision: float
    retire_recall: float
    retire_f1: float
    judge_agreement: float | None = Field(
        default=None, description="None when no case reached the judge."
    )
    band_miss_rate: float

    confusion: dict[str, dict[str, int]] = Field(
        default_factory=dict, description="expected action -> predicted action -> count."
    )
    failures: list[str] = Field(
        default_factory=list, description="Case ids whose action did not match the label."
    )


def score(outcomes: Sequence[CaseOutcome], *, gate: float | None = None) -> Report:
    """Score a set of outcomes, optionally replayed at a different gate."""
    if not outcomes:
        raise ValueError("Cannot score an empty corpus.")

    effective_gate = gate if gate is not None else outcomes[0].gate
    predicted = [replay(o, effective_gate) for o in outcomes]
    total = len(outcomes)

    tally: dict[str, Counter[str]] = {}
    for outcome, action in zip(outcomes, predicted, strict=True):
        tally.setdefault(outcome.expect.value, Counter())[action.value] += 1
    confusion = {expected: dict(counts) for expected, counts in tally.items()}

    correct = sum(o.expect is a for o, a in zip(outcomes, predicted, strict=True))

    # --- Safety -----------------------------------------------------------
    belief_loss = sum(
        a is Action.RETIRE and o.expect is not Action.RETIRE
        for o, a in zip(outcomes, predicted, strict=True)
    )
    merge_loss = sum(
        a is Action.REINFORCE and o.expect is not Action.REINFORCE
        for o, a in zip(outcomes, predicted, strict=True)
    )
    stale = sum(
        o.expect is Action.RETIRE and a in {Action.STORE, Action.REINFORCE}
        for o, a in zip(outcomes, predicted, strict=True)
    )

    # --- Retire precision / recall ---------------------------------------
    true_positive = sum(
        a is Action.RETIRE and o.expect is Action.RETIRE
        for o, a in zip(outcomes, predicted, strict=True)
    )
    predicted_retire = sum(a is Action.RETIRE for a in predicted)
    labelled_retire = sum(o.expect is Action.RETIRE for o in outcomes)

    precision = _ratio(true_positive, predicted_retire)
    recall = _ratio(true_positive, labelled_retire)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    # --- Cost and reach ---------------------------------------------------
    judged = [o for o in outcomes if o.used_llm]
    agreement = (
        _ratio(sum(o.judge_relation is not None and action_of(o.judge_relation) is o.expect
                   for o in judged), len(judged))
        if judged
        else None
    )

    # A resolver that found no candidate returns NEW without consulting anything.
    band_miss = sum(
        o.verdict is Verdict.NEW and o.expect is not Action.STORE for o in outcomes
    )

    return Report(
        gate=effective_gate,
        total=total,
        belief_loss_rate=_ratio(belief_loss, total),
        merge_loss_rate=_ratio(merge_loss, total),
        stale_belief_rate=_ratio(stale, total),
        escalation_rate=_ratio(sum(a is Action.ESCALATE for a in predicted), total),
        free_rate=_ratio(total - len(judged), total),
        accuracy=_ratio(correct, total),
        retire_precision=precision,
        retire_recall=recall,
        retire_f1=round(f1, 4),
        judge_agreement=agreement,
        band_miss_rate=_ratio(band_miss, total),
        confusion=confusion,
        failures=[
            o.case_id for o, a in zip(outcomes, predicted, strict=True) if o.expect is not a
        ],
    )


def sweep(
    outcomes: Sequence[CaseOutcome], gates: Iterable[float] = DEFAULT_GATES
) -> list[Report]:
    """Score the same recorded run at every candidate gate. No LLM calls."""
    return [score(outcomes, gate=g) for g in gates]


def recommend_gate(reports: Sequence[Report], *, max_belief_loss: float = 0.0) -> Report | None:
    """The cheapest gate that keeps belief loss within budget.

    Deliberately not "highest F1". F1 treats a lost belief and an unnecessary
    escalation as equally bad, and they are not: one wastes a minute of someone's
    attention, the other destroys information with no way to notice. So the
    safety constraint is applied first, and only then do we minimise escalation —
    which is the same priority order the resolver itself uses.
    """
    affordable = [r for r in reports if r.belief_loss_rate <= max_belief_loss]
    if not affordable:
        return None
    return min(affordable, key=lambda r: (r.escalation_rate, -r.retire_recall, r.gate))


def by_tag(outcomes: Sequence[CaseOutcome], *, gate: float | None = None) -> dict[str, Report]:
    """Score each tag separately.

    The aggregate number hides which *kind* of case regressed. A drop that is all
    in `narrowing` means something different from one all in `duplicate`.
    """
    tags = sorted({tag for o in outcomes for tag in o.tags})
    grouped: dict[str, Report] = {}
    for tag in tags:
        subset = [o for o in outcomes if tag in o.tags]
        if subset:
            grouped[tag] = score(subset, gate=gate)
    return grouped


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)
