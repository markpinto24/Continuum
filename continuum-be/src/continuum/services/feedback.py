"""Learning from the conflicts people settle.

Every inbox or chat decision becomes a labelled example: the two statements, what
the resolver thought when it escalated them, and what the person said. From
those labels:

- `gate_evidence` answers the question the project could only answer from three
  hand-written cases: would a lower `auto_supersede_confidence` have been safe?
  It counts exactly the verdicts a lower gate would have auto-applied, and how
  many of them people refuted.
- `to_corpus_cases` exports the labels in the Phase 5 corpus format, so
  `run_eval.py --add-cases` scores the resolver against real decisions.

Nothing here retunes the resolver by itself. The gate is the most consequential
setting in the project (CLAUDE.md); this turns a decision about it from a guess
into arithmetic, and leaves the decision to a person.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import yaml

from continuum.clients.labels import LabelStore
from continuum.config import Settings
from continuum.core.logger import get_logger
from continuum.evaluation.types import ResolutionCase
from continuum.models.feedback import (
    EXPECTED_ACTION,
    Decision,
    GateBand,
    GateEvidence,
    ResolutionLabel,
)
from continuum.models.memory import Memory

log = get_logger(__name__)

BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01),
)

_PHRASE = {
    Decision.NEWER_HOLDS: "the newer statement holds and the older one is out of date",
    Decision.OLDER_HOLDS: (
        "the older statement holds; the newer one was wrong or already obsolete, "
        "so asking was right and auto-retiring would have lost a true belief"
    ),
    Decision.BOTH_HOLD: "both statements are true at once, so nothing should be retired",
}


def build_labels(
    user_id: str,
    winner: Memory,
    losers: list[Memory],
    *,
    keep_both: bool,
    now: datetime | None = None,
) -> list[ResolutionLabel]:
    """One label per pair the person ruled on, oriented older -> newer.

    The corpus's "existing" is the belief already in the graph and "incoming"
    the fact that arrived later — so the pair is ordered by creation time, not
    by who won.
    """
    now = now or datetime.now(UTC)
    labels = []
    for loser in losers:
        # Only pairs that were actually in dispute. The endpoint accepts any
        # loser ids; a pairing the resolver never escalated is not a label.
        if loser.id not in winner.conflicts_with and winner.id not in loser.conflicts_with:
            continue
        older, newer = sorted((winner, loser), key=lambda m: m.created_at)
        if keep_both:
            decision = Decision.BOTH_HOLD
        elif winner.id == newer.id:
            decision = Decision.NEWER_HOLDS
        else:
            decision = Decision.OLDER_HOLDS
        # Ingest records the escalation on the incoming memory; look both ways
        # in case a later ingest re-escalated the pair from the other side.
        escalation = newer.escalation_against(older.id) or older.escalation_against(newer.id)
        labels.append(
            ResolutionLabel(
                id=uuid.uuid4().hex,
                user_id=user_id,
                created_at=now,
                existing_memory_id=older.id,
                existing_content=older.content,
                existing_category=older.category.value,
                existing_subject=older.subject,
                existing_confidence=older.confidence,
                existing_age_days=round(
                    max(0.0, (newer.created_at - older.created_at).total_seconds() / 86_400), 2
                ),
                incoming_memory_id=newer.id,
                incoming_content=newer.content,
                incoming_category=newer.category.value,
                incoming_subject=newer.subject,
                judge_relation=escalation.judge_relation if escalation else None,
                judge_confidence=escalation.judge_confidence if escalation else None,
                similarity=escalation.similarity if escalation else None,
                gate=escalation.gate if escalation else None,
                forced=escalation.forced if escalation else False,
                decision=decision,
                expected_action=EXPECTED_ACTION[decision],
            )
        )
    return labels


def gate_evidence(
    labels: list[ResolutionLabel], *, gate: float, min_evidence: int
) -> GateEvidence:
    judged = [label for label in labels if label.judge_relation is not None]
    # Policy escalations (the role rule) are not evidence about the gate: no
    # confidence would have changed them.
    supersedes = [
        label
        for label in judged
        if label.judge_relation == "supersedes"
        and not label.forced
        and label.judge_confidence is not None
    ]

    def band(low: float, high: float) -> GateBand:
        inside = [s for s in supersedes if low <= (s.judge_confidence or 0.0) < high]
        confirmed = sum(s.decision is Decision.NEWER_HOLDS for s in inside)
        return GateBand(
            low=low, high=min(high, 1.0), total=len(inside),
            confirmed=confirmed, refuted=len(inside) - confirmed,
        )

    below = [s for s in supersedes if (s.judge_confidence or 0.0) < gate]
    refuted = sum(s.decision is not Decision.NEWER_HOLDS for s in below)
    bound = min(1.0, 3 / len(below)) if below and refuted == 0 else None
    role = [label for label in judged if label.forced]
    conflicts = [label for label in judged if label.judge_relation == "conflict"]

    return GateEvidence(
        gate=gate,
        labels=len(labels),
        with_judgement=len(judged),
        bands=[band(low, high) for low, high in BANDS],
        below_gate_total=len(below),
        below_gate_refuted=refuted,
        error_upper_bound=round(bound, 3) if bound is not None else None,
        role_rule_total=len(role),
        role_rule_shared=sum(label.decision is not Decision.NEWER_HOLDS for label in role),
        conflicts_total=len(conflicts),
        conflicts_both_hold=sum(label.decision is Decision.BOTH_HOLD for label in conflicts),
        recommendation=_recommend(below, refuted, bound, gate, min_evidence),
    )


def _recommend(
    below: list[ResolutionLabel],
    refuted: int,
    bound: float | None,
    gate: float,
    min_evidence: int,
) -> str:
    n = len(below)
    if n == 0:
        return (
            f"Keep {gate:.2f}. No escalated 'supersedes' below the gate has been settled "
            "yet, so there is nothing to learn about the gate from real use."
        )
    if refuted:
        return (
            f"Keep {gate:.2f}. {refuted} of the {n} below-gate 'supersedes' verdicts you "
            "settled were wrong — a lower gate would have retired a true belief without "
            "asking."
        )
    if n < min_evidence:
        return (
            f"Keep {gate:.2f} for now. All {n} below-gate 'supersedes' verdicts you settled "
            f"were right, which only bounds the error below {bound:.0%}. About "
            f"{min_evidence} with none wrong would bound it below {3 / min_evidence:.0%}."
        )
    lowest = min(label.judge_confidence or 0.0 for label in below)
    return (
        f"The evidence supports considering a lower gate: all {n} below-gate "
        f"'supersedes' verdicts you settled were right (error below {bound:.0%} at 95%), "
        f"down to confidence {lowest:.2f}. Changing it is a product decision — replay "
        "the corpus with these cases added before you do."
    )


def to_corpus_cases(labels: list[ResolutionLabel]) -> list[dict]:
    """The labels as Phase 5 resolution cases, each validated against the corpus schema."""
    cases = []
    for label in labels:
        verdict = (
            f" The resolver had said '{label.judge_relation}'"
            + (f" at p={label.judge_confidence:.2f}" if label.judge_confidence is not None else "")
            + (" (escalated by the role rule)" if label.forced else "")
            + "."
            if label.judge_relation
            else ""
        )
        tags = ["from-use", label.decision.value]
        if label.forced:
            tags.append("role-rule")
        if label.judge_relation:
            tags.append(f"judge-{label.judge_relation}")
        case = {
            "id": f"use-{label.id[:12]}",
            "why": f"Settled by a person on {label.created_at.date()}: "
            f"{_PHRASE[label.decision]}.{verdict}",
            "existing": {
                "content": label.existing_content,
                "category": label.existing_category,
                "subject": label.existing_subject,
                "confidence": round(label.existing_confidence, 2),
                "age_days": round(label.existing_age_days, 1),
            },
            "incoming": {
                "content": label.incoming_content,
                "category": label.incoming_category,
                "subject": label.incoming_subject,
            },
            "expect": label.expected_action.value,
            "tags": tags,
        }
        ResolutionCase.model_validate(case)  # an export that will not load is worse than none
        cases.append(case)
    return cases


class FeedbackService:
    def __init__(self, store: LabelStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    async def record_resolution(
        self, user_id: str, winner: Memory, losers: list[Memory], *, keep_both: bool
    ) -> list[ResolutionLabel]:
        """Label a decision the person just made. Never fails the decision itself."""
        labels = build_labels(user_id, winner, losers, keep_both=keep_both)
        try:
            await self.store.add_many(labels)
        except Exception:  # noqa: BLE001 - the graph change is already applied
            log.exception("feedback.record_failed", labels=len(labels))
            return []
        log.info(
            "feedback.recorded",
            labels=len(labels),
            decisions=[label.decision.value for label in labels],
            with_judgement=sum(label.judge_relation is not None for label in labels),
        )
        return labels

    async def labels(self, user_id: str, *, limit: int = 1000) -> list[ResolutionLabel]:
        return await self.store.list_for_user(user_id, limit=limit)

    async def evidence(self, user_id: str) -> GateEvidence:
        return gate_evidence(
            await self.store.list_for_user(user_id),
            gate=self.settings.auto_supersede_confidence,
            min_evidence=self.settings.feedback_min_gate_evidence,
        )

    async def export_yaml(self, user_id: str) -> str:
        labels = await self.store.list_for_user(user_id)
        header = (
            "# Resolution cases from real use: conflicts settled in Continuum.\n"
            "# Score the resolver against them with:\n"
            "#   uv run python scripts/run_eval.py --record run.json --add-cases THIS_FILE\n"
            "# Each case copies memory content: treat this file as private.\n"
        )
        body = yaml.safe_dump(
            to_corpus_cases(list(reversed(labels))), sort_keys=False, allow_unicode=True
        )
        return header + body
