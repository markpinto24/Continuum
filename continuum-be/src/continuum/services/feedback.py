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
from continuum.clients.learning import LearningStore
from continuum.config import Settings
from continuum.core.logger import get_logger
from continuum.evaluation.types import ResolutionCase
from continuum.models.feedback import (
    EXPECTED_ACTION,
    AnswerFeedback,
    CalibrationPoint,
    CalibrationReport,
    Decision,
    ExtractionFeedback,
    FeedbackSummary,
    GateBand,
    GateEvidence,
    ResolutionLabel,
    ResolutionRule,
    RuleList,
    RuleSuggestion,
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
        disputed = (
            loser.id in winner.conflicts_with + winner.team_conflicts_with
            or winner.id in loser.conflicts_with + loser.team_conflicts_with
        )
        if not disputed:
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
                graph_owner=older.user_id if older.user_id == newer.user_id else "cross",
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


def _supersede_labels(labels: list[ResolutionLabel]) -> list[ResolutionLabel]:
    """Escalated `supersedes` verdicts with a probability: the only ones a gate decides."""
    return [
        label
        for label in labels
        if label.judge_relation == "supersedes"
        and not label.forced
        and label.judge_confidence is not None
    ]


def calibration_points(labels: list[ResolutionLabel]) -> list[CalibrationPoint]:
    """How often the judge was right per confidence band, made monotone.

    Each band gets a smoothed rate ((confirmed + 1) / (total + 2): one decision
    is not certainty), then pool-adjacent-violators forces the curve to rise with
    confidence — a judge that is "more sure" must never map to "less likely".
    Empty bands borrow the nearest band below (or above, at the bottom).
    """
    supersedes = _supersede_labels(labels)
    raw = []
    for low, high in BANDS:
        inside = [s for s in supersedes if low <= (s.judge_confidence or 0.0) < high]
        confirmed = sum(s.decision is Decision.NEWER_HOLDS for s in inside)
        raw.append((low, high, len(inside), confirmed))

    # Pool adjacent violators over the non-empty bands, weighted by size.
    blocks: list[list[float]] = []  # [value, weight, first_index, last_index]
    for index, (_, _, total, confirmed) in enumerate(raw):
        if total == 0:
            continue
        blocks.append([(confirmed + 1) / (total + 2), float(total), index, index])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            value_b, weight_b, _, last = blocks.pop()
            value_a, weight_a, first, _ = blocks.pop()
            weight = weight_a + weight_b
            blocks.append([(value_a * weight_a + value_b * weight_b) / weight, weight, first, last])
    fitted: dict[int, float] = {}
    for value, _, first, last in blocks:
        for index in range(int(first), int(last) + 1):
            fitted[index] = value

    points = []
    last_value = None
    for index, (low, high, total, confirmed) in enumerate(raw):
        if index in fitted:
            last_value = fitted[index]
        value = last_value if last_value is not None else (fitted[min(fitted)] if fitted else 0.5)
        points.append(
            CalibrationPoint(
                low=low,
                high=min(high, 1.0),
                total=total,
                confirmed=confirmed,
                observed=round(confirmed / total, 3) if total else None,
                calibrated=round(value, 3),
            )
        )
    return points


class Calibration:
    """The curve the resolver's gate can use, refreshed as decisions arrive."""

    def __init__(self) -> None:
        self.points: list[CalibrationPoint] = []
        self.labels = 0
        self.min_labels = 0

    def update(self, labels: list[ResolutionLabel], *, min_labels: int) -> None:
        self.points = calibration_points(labels)
        self.labels = len(_supersede_labels(labels))
        self.min_labels = min_labels

    @property
    def usable(self) -> bool:
        return self.labels >= self.min_labels > 0

    def map(self, confidence: float) -> float | None:
        """The calibrated probability for a raw judge confidence, or None if unusable."""
        if not self.usable:
            return None
        for point in self.points:
            if point.low <= confidence < point.high or (point.high >= 1.0 and confidence >= 1.0):
                return point.calibrated
        return None


def rule_suggestions(
    labels: list[ResolutionLabel],
    *,
    owners: list[str],
    existing: set[tuple[str, str]],
    min_decisions: int,
) -> list[RuleSuggestion]:
    """Subjects where people keep saying both statements hold, and never the opposite."""
    counts: dict[tuple[str, str], list[Decision]] = {}
    for label in labels:
        subject = label.existing_subject or label.incoming_subject
        if not subject or label.graph_owner not in owners:
            continue
        counts.setdefault((label.graph_owner, subject), []).append(label.decision)
    suggestions = []
    for (owner, subject), decisions in sorted(counts.items()):
        both = sum(d is Decision.BOTH_HOLD for d in decisions)
        if both >= min_decisions and both == len(decisions) and (owner, subject) not in existing:
            suggestions.append(RuleSuggestion(owner=owner, subject=subject, both_hold=both))
    return suggestions


def to_extraction_cases(rejected: list[ExtractionFeedback]) -> list[dict]:
    """Only rejections with an excerpt: without the text it came from there is
    nothing to re-run the extractor on."""
    cases = []
    for item in rejected:
        if not item.source_excerpt or item.content == "[forgotten]":
            continue
        cases.append({
            "id": f"from-use-reject-{item.id[:8]}",
            "why": f"Rejected in use ({item.reason.value})"
            + (f": {item.note}" if item.note else "."),
            "text": item.source_excerpt,
            "expect": [],
            "forbid": [item.content],
            "tags": ["from-use", f"rejected-{item.reason.value}"],
        })
    return cases


def to_retrieval_cases(
    user_id: str, answers: list[AnswerFeedback], lookup: dict[str, Memory]
) -> list[dict]:
    cases = []
    for answer in answers:
        expect = [
            {"id": mid, "content": lookup[mid].content}
            for mid in answer.missing_ids
            if mid in lookup and not lookup[mid].redacted_at
        ]
        if not expect or answer.query == "[forgotten]":
            continue
        cases.append({
            "id": f"from-use-answer-{answer.id[:8]}",
            "why": "You said these should have come up"
            + (f": {answer.note}" if answer.note else "."),
            "owner": user_id,
            "query": answer.query,
            "expect": expect,
            "tags": ["from-use"],
        })
    return cases


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
    def __init__(
        self,
        store: LabelStore,
        settings: Settings,
        *,
        learning: LearningStore | None = None,
        calibration: Calibration | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.learning = learning
        self.calibration = calibration or Calibration()

    async def refresh_calibration(self) -> None:
        """Rebuild the gate's calibration curve from every decision on the instance."""
        try:
            labels = await self.store.list_all()
        except Exception:  # noqa: BLE001 - the gate falls back to the raw judge
            log.exception("feedback.calibration_refresh_failed")
            return
        self.calibration.update(labels, min_labels=self.settings.calibration_min_labels)

    async def calibration_report(self) -> CalibrationReport:
        await self.refresh_calibration()
        return CalibrationReport(
            labels=self.calibration.labels,
            points=self.calibration.points,
            usable=self.calibration.usable,
            in_use=self.settings.calibrated_gate and self.calibration.usable,
            min_labels=self.settings.calibration_min_labels,
        )

    # --- Rules ---------------------------------------------------------------

    async def rules(self, owners: list[str]) -> RuleList:
        assert self.learning is not None
        rules = await self.learning.rules(owners)
        labels = [
            label for label in await self.store.list_all() if label.graph_owner in owners
        ]
        suggestions = rule_suggestions(
            labels,
            owners=owners,
            existing={(r.owner, r.subject) for r in rules},
            min_decisions=self.settings.rule_suggestion_min_decisions,
        )
        return RuleList(rules=rules, suggestions=suggestions)

    async def approve_rule(self, *, owner: str, subject: str, approved_by: str) -> ResolutionRule:
        assert self.learning is not None
        existing = [r for r in await self.learning.rules([owner]) if r.subject == subject]
        if existing:
            return existing[0]
        rule = ResolutionRule(
            id=uuid.uuid4().hex,
            owner=owner,
            subject=subject,
            created_by=approved_by,
            created_at=datetime.now(UTC),
        )
        await self.learning.add_rule(rule)
        log.info("feedback.rule_approved", rule_id=rule.id, owner=owner)
        return rule

    async def revoke_rule(self, rule_id: str, *, owners: list[str]) -> None:
        assert self.learning is not None
        rule = await self.learning.get_rule(rule_id)
        if rule is None or rule.owner not in owners or rule.revoked_at is not None:
            raise LookupError("No such active rule.")
        await self.learning.revoke_rule(rule_id, datetime.now(UTC))
        log.info("feedback.rule_revoked", rule_id=rule_id)

    async def compatible_subjects(self, owner: str) -> set[str]:
        """Subjects whose statements this graph's approved rules say can all hold."""
        if self.learning is None:
            return set()
        return {r.subject for r in await self.learning.rules([owner]) if r.kind == "compatible"}

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
        await self.refresh_calibration()
        log.info(
            "feedback.recorded",
            labels=len(labels),
            decisions=[label.decision.value for label in labels],
            with_judgement=sum(label.judge_relation is not None for label in labels),
        )
        return labels

    async def labels(self, user_id: str, *, limit: int = 1000) -> list[ResolutionLabel]:
        return await self.store.list_for_user(user_id, limit=limit)

    async def evidence(self, user_id: str, *, team: bool = False) -> GateEvidence:
        """Yours, or pooled across everyone on the instance (counts only — no
        one's content leaves their own labels)."""
        labels = await (self.store.list_all() if team else self.store.list_for_user(user_id))
        return gate_evidence(
            labels,
            gate=self.settings.auto_supersede_confidence,
            min_evidence=self.settings.feedback_min_gate_evidence,
        )

    # --- Extraction and answer feedback ------------------------------------------

    async def record_answer(self, item: AnswerFeedback) -> None:
        assert self.learning is not None
        await self.learning.add_answer(item)
        log.info("feedback.answer_rated", rating=item.rating, missing=len(item.missing_ids),
                 used=len(item.used_ids))

    async def summary(self, user_id: str) -> FeedbackSummary:
        decisions = await self.store.list_for_user(user_id)
        rejected = await self.learning.rejections(user_id) if self.learning else []
        answers = await self.learning.answers(user_id) if self.learning else []
        return FeedbackSummary(
            decisions=len(decisions),
            rejected_facts=len(rejected),
            answers_up=sum(a.rating > 0 for a in answers),
            answers_down=sum(a.rating < 0 for a in answers),
            missing_memories=sum(len(a.missing_ids) for a in answers),
        )

    async def extraction_yaml(self, user_id: str) -> str:
        """Rejected extractions as extraction-corpus cases: the excerpt is the
        input, and what was wrongly extracted from it must not come back."""
        rejected = await self.learning.rejections(user_id) if self.learning else []
        cases = to_extraction_cases(list(reversed(rejected)))
        header = (
            "# Extraction cases from real use: memories you rejected in Continuum.\n"
            "# Score the extractor against them with:\n"
            "#   uv run python scripts/run_eval.py --extraction --add-extraction THIS_FILE\n"
            "# Each case copies memory content: treat this file as private.\n"
        )
        return header + yaml.safe_dump(cases, sort_keys=False, allow_unicode=True)

    async def retrieval_yaml(self, user_id: str, lookup: dict[str, Memory]) -> str:
        """Answers where a memory should have come up, as retrieval cases."""
        answers = await self.learning.answers(user_id) if self.learning else []
        cases = to_retrieval_cases(user_id, list(reversed(answers)), lookup)
        header = (
            "# Retrieval cases from real use: memories you said should have come up.\n"
            "# Check recall, and sweep the ranking weights, against the live graph:\n"
            "#   uv run python scripts/run_eval.py --retrieval THIS_FILE\n"
            "# Each case copies your questions: treat this file as private.\n"
        )
        return header + yaml.safe_dump(cases, sort_keys=False, allow_unicode=True)

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
