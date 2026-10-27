"""The observability dashboard's arithmetic, pure: what was taken in, asked
about, retired by the resolver alone, and decided by a person — per day."""

from __future__ import annotations

from datetime import UTC, date, datetime

from continuum.models.feedback import Decision, ExpectedAction, ResolutionLabel
from continuum.models.memory import Escalation, Memory, MemoryStatus
from continuum.services.activity import daily_activity

TODAY = date(2026, 10, 27)


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def memory(created: int, **change) -> Memory:  # noqa: ANN003
    return Memory(user_id="mark", content="x", created_at=at(created), **change)


def escalation(
    day: int, confidence: float | None, *, forced: bool = False, relation: str = "supersedes"
) -> Escalation:
    return Escalation(
        target_id="t", judge_relation=relation, judge_confidence=confidence, gate=0.8,
        forced=forced, raised_at=at(day),
    )


def label(day: int, decision: Decision, existing: str, incoming: str, via: str) -> ResolutionLabel:
    return ResolutionLabel(
        id=f"l{day}{existing}", user_id="mark", created_at=at(day),
        existing_memory_id=existing, existing_content="a", existing_category="decision",
        existing_confidence=0.7, existing_age_days=3, incoming_memory_id=incoming,
        incoming_content="b", incoming_category="decision", decision=decision,
        expected_action=ExpectedAction.RETIRE, settled_via=via,
    )


def test_each_day_counts_what_happened_on_it():
    by_resolver = memory(25, status=MemoryStatus.SUPERSEDED, superseded_at=at(26))
    by_person = memory(25, status=MemoryStatus.SUPERSEDED, superseded_at=at(26))
    shared = memory(25, status=MemoryStatus.SUPERSEDED, superseded_at=at(26), shared_as="s1")
    asked = memory(26, escalations=[escalation(26, 0.6), escalation(26, 0.7),
                                    escalation(26, 0.99, forced=True),
                                    escalation(26, 1.0, relation="conflict")])
    summary = memory(26, kind="summary")
    decisions = [
        label(27, Decision.NEWER_HOLDS, by_person.id, asked.id, "session"),
        label(27, Decision.BOTH_HOLD, "m8", "m9", "api_key"),
    ]

    report = daily_activity(
        [by_resolver, by_person, shared, asked, summary], decisions, days=3, gate=0.8, today=TODAY
    )
    d25, d26, d27 = report.days
    assert (d25.day, d25.remembered) == (date(2026, 10, 25), 3)
    # Only the supersede no person's decision explains, and not a share.
    assert d26.auto_retired == 1
    assert (d26.remembered, d26.escalated) == (1, 4)  # the summary is not taken in
    # Median of the supersedes the gate held back; a forced escalation had no
    # say, and a confident "conflict" says nothing about the gate.
    assert d26.judge_confidence == 0.65
    assert (d27.decided, d27.decided_by_agent) == (2, 1)
    assert report.escalation_rate == round(4 / 4, 4)


def test_quiet_days_are_still_days():
    report = daily_activity([], [], days=7, gate=0.8, today=TODAY)
    assert [d.day for d in report.days][0] == date(2026, 10, 21) and len(report.days) == 7
    assert report.escalation_rate is None and all(d.judge_confidence is None for d in report.days)


def test_what_happened_before_the_window_is_left_out():
    old = memory(1, escalations=[escalation(1, 0.5)])
    report = daily_activity([old], [], days=3, gate=0.8, today=TODAY)
    assert report.remembered == 0 and report.escalated == 0
