"""Render reports as plain text.

No plotting, no HTML. The output has to be readable in a terminal, diffable
between runs, and pasteable into a pull request — that is what makes a number
something you can argue with.
"""

from __future__ import annotations

from collections.abc import Sequence

from continuum.evaluation.extraction import ExtractionReport
from continuum.evaluation.metrics import Report, recommend_gate
from continuum.evaluation.types import Action, CaseOutcome

_ACTIONS = [Action.RETIRE, Action.ESCALATE, Action.REINFORCE, Action.STORE]


def render_summary(report: Report) -> str:
    agreement = "   n/a" if report.judge_agreement is None else _pct(report.judge_agreement)
    lines = [
        f"gate {report.gate:.2f}   {report.total} cases",
        "",
        "  safety",
        f"    belief loss      {_pct(report.belief_loss_rate)}"
        "   retired a belief that should have lived",
        f"    merge loss       {_pct(report.merge_loss_rate)}"
        "   folded a distinct fact into an existing one",
        f"    stale belief     {_pct(report.stale_belief_rate)}"
        "   should have retired, did not, did not ask",
        "",
        "  cost",
        f"    escalation       {_pct(report.escalation_rate)}   sent to a human",
        f"    free decisions   {_pct(report.free_rate)}   settled with no LLM call",
        "",
        "  quality",
        f"    accuracy         {_pct(report.accuracy)}",
        f"    retire P / R     {_pct(report.retire_precision)} / {_pct(report.retire_recall)}"
        f"   (F1 {report.retire_f1:.2f})",
        f"    judge agreement  {agreement}",
        f"    band misses      {_pct(report.band_miss_rate)}"
        "   never found a candidate to compare",
    ]
    if report.failures:
        lines += ["", "  mislabelled cases", *(f"    - {case}" for case in report.failures)]
    return "\n".join(lines)


def render_confusion(report: Report) -> str:
    header = "  expected \\ actual".ljust(22) + "".join(a.value.ljust(11) for a in _ACTIONS)
    rows = [header, "  " + "-" * (20 + 11 * len(_ACTIONS))]
    for expected in _ACTIONS:
        counts = report.confusion.get(expected.value, {})
        if not counts:
            continue
        cells = "".join(str(counts.get(a.value, 0)).ljust(11) for a in _ACTIONS)
        rows.append("  " + expected.value.ljust(20) + cells)
    return "\n".join(rows)


def render_sweep(reports: Sequence[Report]) -> str:
    """The table Phase 5 exists to produce.

    Read it as a trade-off curve, not a leaderboard. Belief loss falls and
    escalation rises together, and the right gate is the cheapest one that keeps
    belief loss at the budget you are willing to accept.
    """
    lines = [
        "  gate    belief-loss   stale   escalation   retire-R   accuracy",
        "  " + "-" * 62,
    ]
    for report in reports:
        lines.append(
            f"  {report.gate:.2f}    "
            f"{_pct(report.belief_loss_rate)}       "
            f"{_pct(report.stale_belief_rate)}   "
            f"{_pct(report.escalation_rate)}        "
            f"{_pct(report.retire_recall)}      "
            f"{_pct(report.accuracy)}"
        )

    best = recommend_gate(reports)
    lines.append("")
    if best is None:
        lines.append(
            "  No gate holds belief loss at zero on this corpus. Either the judge is\n"
            "  wrong on a case it is confident about, or a label is wrong. Read the\n"
            "  failures before turning the dial."
        )
    else:
        lines.append(
            f"  Recommended gate: {best.gate:.2f} — the cheapest setting with zero belief\n"
            f"  loss, escalating {_pct(best.escalation_rate).strip()} of cases."
        )
    return "\n".join(lines)


def render_by_tag(grouped: dict[str, Report]) -> str:
    lines = ["  tag".ljust(30) + "n     accuracy   belief-loss   escalation", "  " + "-" * 68]
    for tag, report in sorted(grouped.items()):
        lines.append(
            "  "
            + tag.ljust(28)
            + str(report.total).ljust(6)
            + _pct(report.accuracy)
            + "     "
            + _pct(report.belief_loss_rate)
            + "         "
            + _pct(report.escalation_rate)
        )
    return "\n".join(lines)


def render_cases(outcomes: Sequence[CaseOutcome]) -> str:
    lines = []
    for outcome in outcomes:
        hit = "ok  " if outcome.action is outcome.expect else "MISS"
        judge = (
            f"{outcome.judge_relation.value}@{outcome.judge_confidence:.2f}"
            if outcome.judge_relation and outcome.judge_confidence is not None
            else "free"
        )
        similarity = f"{outcome.similarity:.3f}" if outcome.similarity is not None else "  -  "
        lines.append(
            f"  {hit}  {outcome.case_id.ljust(38)} "
            f"want {outcome.expect.value.ljust(10)} got {outcome.action.value.ljust(10)} "
            f"sim {similarity}  judge {judge}"
        )
    return "\n".join(lines)


def render_extraction(report: ExtractionReport, *, label: str) -> str:
    lines = [
        f"  {label}",
        f"    facts        {report.extracted_facts} extracted vs {report.labelled_facts} labelled",
        f"    precision    {_pct(report.fact_precision)}",
        f"    recall       {_pct(report.fact_recall)}   (F1 {report.fact_f1:.2f})",
        f"    category     {_pct(report.category_accuracy)}   of matched facts",
        f"    subject      {_pct(report.subject_accuracy)}   of matched facts",
        f"    provenance   {_pct(report.excerpt_rate)}   carry a source excerpt",
    ]
    if report.missed:
        lines += ["    missed:", *(f"      - {m}" for m in report.missed)]
    if report.spurious:
        lines += ["    invented:", *(f"      - {s}" for s in report.spurious)]
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"
