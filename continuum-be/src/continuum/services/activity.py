"""The resolver over time: what was taken in, asked about, retired, decided.

One graph's own record, read back as days — nothing new is logged for it. Every
number comes from what the graph already keeps: when each memory was created
and retired, each escalation the resolver recorded (`Memory.escalations`), and
each decision a person made (resolution labels).

"Retired without asking" is the number to watch: a supersede no person's
decision explains. With the gate doing its job it stays small, and its
confidence sits above the gate; escalations sit below it.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from continuum.models.feedback import ActivityDay, ActivityReport, Decision, ResolutionLabel
from continuum.models.memory import Memory, MemoryStatus
from continuum.services.feedback import FeedbackService
from continuum.services.memory_store import MemoryStore

#: Enough to cover any graph a person keeps; the dashboard reads them once.
MAX_MEMORIES = 50_000


class ActivityService:
    def __init__(self, memories: MemoryStore, feedback: FeedbackService, gate: float) -> None:
        self.memories = memories
        self.feedback = feedback
        self.gate = gate

    async def report(self, *, user_id: str, owner: str, days: int) -> ActivityReport:
        memories = await self.memories.list_all(user_id=owner, limit=MAX_MEMORIES)
        labels = await self.feedback.labels(user_id, owners=[owner], limit=MAX_MEMORIES)
        return daily_activity(memories, labels, days=days, gate=self.gate)


def daily_activity(
    memories: Iterable[Memory],
    labels: Iterable[ResolutionLabel],
    *,
    days: int,
    gate: float,
    today: date | None = None,
) -> ActivityReport:
    """Pure: the last `days` days, oldest first, every day present even if empty."""
    today = today or datetime.now(UTC).date()
    first = today - timedelta(days=days - 1)
    labels = list(labels)
    # Retired by a person's ruling, not by the resolver: the loser of each decision.
    person_retired = {
        label.existing_memory_id if label.decision is Decision.NEWER_HOLDS
        else label.incoming_memory_id
        for label in labels
        if label.decision is not Decision.BOTH_HOLD
    }

    counts: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    confidences: dict[date, list[float]] = defaultdict(list)
    for memory in memories:
        if memory.kind == "summary":
            continue  # written by the system, not taken in
        counts[memory.created_at.date()]["remembered"] += 1
        for escalation in memory.escalations:
            day = escalation.raised_at.date()
            counts[day]["escalated"] += 1
            # Only a supersede the gate sent to a person speaks to the gate: how
            # close the resolver came to retiring a belief on its own. A
            # "conflict" is the judge sure there is a dispute — near 1.0, and
            # nothing to do with the gate.
            if (
                escalation.judge_relation == "supersedes"
                and escalation.judge_confidence is not None
                and not escalation.forced
            ):
                confidences[day].append(escalation.judge_confidence)
        if (
            memory.status is MemoryStatus.SUPERSEDED
            and memory.superseded_at is not None
            and memory.id not in person_retired
            and not memory.shared_as  # retired into its team copy, by a person sharing it
        ):
            counts[memory.superseded_at.date()]["auto_retired"] += 1
    for label in labels:
        day = label.created_at.date()
        counts[day]["decided"] += 1
        counts[day]["decided_by_agent"] += label.settled_via == "api_key"

    window = [first + timedelta(days=n) for n in range(days)]
    rows = [
        ActivityDay(
            day=day,
            **counts.get(day, {}),
            judge_confidence=round(statistics.median(confidences[day]), 3)
            if confidences.get(day) else None,
        )
        for day in window
    ]
    remembered = sum(r.remembered for r in rows)
    escalated = sum(r.escalated for r in rows)
    return ActivityReport(
        days=rows,
        gate=gate,
        remembered=remembered,
        escalated=escalated,
        auto_retired=sum(r.auto_retired for r in rows),
        decided=sum(r.decided for r in rows),
        escalation_rate=round(escalated / remembered, 4) if remembered else None,
    )
