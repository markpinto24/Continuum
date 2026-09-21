"""Decay tests.

Decay is the quietest part of the system and the easiest to get subtly wrong, so
the properties worth pinning are: it is monotonic, it is idempotent, it respects
per-category half-lives, and it never touches history.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from continuum.models.memory import Memory, MemoryCategory, MemoryStatus

NOW = datetime(2026, 6, 1, tzinfo=UTC)
FLOOR = 0.05


def aged(category: MemoryCategory, days: int, confidence: float = 0.8) -> Memory:
    return Memory(
        user_id="u",
        content="x",
        category=category,
        confidence=confidence,
        last_reinforced_at=NOW - timedelta(days=days),
    )


def test_fresh_memory_does_not_decay():
    m = aged(MemoryCategory.PREFERENCE, days=0)
    assert m.decayed_confidence(now=NOW, floor=FLOOR) == m.confidence


def test_one_half_life_halves_confidence():
    m = aged(MemoryCategory.PREFERENCE, days=90)  # preference half-life is 90d
    assert m.decayed_confidence(now=NOW, floor=FLOOR) == round(0.8 * 0.5, 4)


def test_two_half_lives_quarter_confidence():
    m = aged(MemoryCategory.PREFERENCE, days=180)
    assert m.decayed_confidence(now=NOW, floor=FLOOR) == round(0.8 * 0.25, 4)


def test_events_never_decay():
    """History is not a belief that can weaken."""
    m = aged(MemoryCategory.EVENT, days=3650)
    assert m.decayed_confidence(now=NOW, floor=FLOOR) == 0.8
    assert m.half_life_days is None


def test_constraints_decay_faster_than_people():
    days = 90
    constraint = aged(MemoryCategory.CONSTRAINT, days).decayed_confidence(now=NOW, floor=FLOOR)
    person = aged(MemoryCategory.PERSON, days).decayed_confidence(now=NOW, floor=FLOOR)
    assert constraint < person  # budgets expire; who someone is does not


def test_decay_respects_the_floor():
    m = aged(MemoryCategory.CONSTRAINT, days=3650, confidence=0.9)
    assert m.decayed_confidence(now=NOW, floor=FLOOR) == FLOOR


def test_decay_is_idempotent():
    """Applying the sweep twice at the same instant must be a no-op the second time."""
    m = aged(MemoryCategory.FACT, days=120)

    first = m.decayed_confidence(now=NOW, floor=FLOOR)
    m.confidence = first
    m.last_reinforced_at = NOW  # the sweep does not move the clock back
    second = m.decayed_confidence(now=NOW, floor=FLOOR)

    assert second == first


def test_reinforcement_resets_the_decay_clock():
    m = aged(MemoryCategory.PREFERENCE, days=90)
    before = m.decayed_confidence(now=NOW, floor=FLOOR)

    m.reinforce(0.05)
    after = m.decayed_confidence(now=datetime.now(UTC), floor=FLOOR)

    assert after > before
    assert m.reinforcement_count == 1


def test_archive_is_reversible():
    m = aged(MemoryCategory.FACT, days=0)
    m.archive()
    assert m.status is MemoryStatus.ARCHIVED

    m.reactivate()
    assert m.status is MemoryStatus.ACTIVE
    assert m.superseded_by is None
