"""The Continuum memory domain model.

This is the heart of the project. A memory is not a flat string — it carries
provenance (where it came from), confidence (how much we still trust it), a
lifecycle status, and explicit graph edges to the memories it replaced.

That `supersedes` edge is the thing most memory frameworks throw away: they
delete the old fact silently. We keep it, which is what makes the belief
history inspectable and the contradiction UX possible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MemoryCategory(StrEnum):
    """What kind of thing this memory is.

    Category drives resolution policy in Phase 2: a DECISION is superseded by a
    later decision, whereas a PREFERENCE decays and an EVENT is immutable
    history that should never be overwritten at all.
    """

    DECISION = "decision"       # "We chose Postgres over Mongo because of X"
    PREFERENCE = "preference"   # "Client prefers async updates over calls"
    FACT = "fact"               # "The billing service runs on ECS"
    EVENT = "event"             # "Shipped v2 on Mar 4" — immutable, never superseded
    PERSON = "person"           # "Sara owns the data platform"
    CONSTRAINT = "constraint"   # "Budget cap is 5k/month"


class MemoryStatus(StrEnum):
    ACTIVE = "active"           # trusted, used in retrieval
    SUPERSEDED = "superseded"   # explicitly replaced by a newer memory
    CONTRADICTED = "contradicted"  # conflict detected, awaiting human resolution
    ARCHIVED = "archived"       # decayed below threshold; recoverable, not retrieved


# Categories that represent immutable history and must never be superseded.
IMMUTABLE_CATEGORIES: frozenset[MemoryCategory] = frozenset({MemoryCategory.EVENT})

# Statuses that participate in retrieval for chat context.
#
# CONTRADICTED is deliberately retrievable. When two beliefs are in unresolved
# conflict the honest behaviour is for the agent to surface both and say so —
# not to silently pick one, and not to go quiet on the subject entirely.
RETRIEVABLE_STATUSES: frozenset[MemoryStatus] = frozenset(
    {MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED}
)

# Confidence half-life per category, in days. Governs how fast an unreinforced
# memory loses trust. A preference goes stale far faster than who someone is;
# an event is immutable history and never decays at all.
CATEGORY_HALF_LIFE_DAYS: dict[MemoryCategory, float | None] = {
    MemoryCategory.CONSTRAINT: 60.0,    # budgets and deadlines expire fastest
    MemoryCategory.PREFERENCE: 90.0,
    MemoryCategory.FACT: 120.0,
    MemoryCategory.DECISION: 180.0,
    MemoryCategory.PERSON: 365.0,
    MemoryCategory.EVENT: None,         # never decays
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class Memory(BaseModel):
    """A single durable belief held by the system."""

    id: str = Field(default_factory=_new_id)
    user_id: str

    content: str
    category: MemoryCategory = MemoryCategory.FACT
    subject: str | None = Field(
        default=None,
        description=(
            "Normalised entity the memory is about ('billing-service', 'acme-corp'). "
            "Phase 2 uses this to narrow the contradiction candidate set before "
            "spending an LLM call."
        ),
    )

    confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    status: MemoryStatus = MemoryStatus.ACTIVE

    # --- Provenance --------------------------------------------------------
    source_id: str | None = Field(
        default=None, description="Ingest batch / conversation this was extracted from."
    )
    source_excerpt: str | None = Field(
        default=None, description="Verbatim span that produced this memory, for traceability."
    )

    # --- Belief graph ------------------------------------------------------
    supersedes: list[str] = Field(
        default_factory=list, description="IDs of memories this one replaced."
    )
    superseded_by: str | None = Field(
        default=None, description="ID of the memory that replaced this one."
    )
    conflicts_with: list[str] = Field(
        default_factory=list, description="IDs of memories in unresolved conflict with this one."
    )

    # --- Lifecycle ---------------------------------------------------------
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_reinforced_at: datetime = Field(default_factory=_utcnow)
    reinforcement_count: int = 0

    def touch(self) -> None:
        self.updated_at = _utcnow()

    def reinforce(self, amount: float = 0.05) -> None:
        """Called when a memory is confirmed or re-observed."""
        self.confidence = min(1.0, self.confidence + amount)
        self.reinforcement_count += 1
        self.last_reinforced_at = _utcnow()
        self.touch()

    def mark_superseded_by(self, memory_id: str) -> None:
        self.status = MemoryStatus.SUPERSEDED
        self.superseded_by = memory_id
        self.touch()

    def mark_contradicted(self, other_id: str) -> None:
        """Flag an unresolved conflict. Stays retrievable — see RETRIEVABLE_STATUSES."""
        self.status = MemoryStatus.CONTRADICTED
        if other_id not in self.conflicts_with:
            self.conflicts_with.append(other_id)
        self.touch()

    def mark_supersedes(self, other_id: str) -> None:
        """Record on the winner that it replaced `other_id`."""
        if other_id not in self.supersedes:
            self.supersedes.append(other_id)
        self.touch()

    def clear_conflict_with(self, other_id: str) -> None:
        """Drop one conflict edge; return to ACTIVE once none remain."""
        self.conflicts_with = [c for c in self.conflicts_with if c != other_id]
        if not self.conflicts_with and self.status is MemoryStatus.CONTRADICTED:
            self.status = MemoryStatus.ACTIVE
        self.touch()

    def archive(self) -> None:
        self.status = MemoryStatus.ARCHIVED
        self.touch()

    def reactivate(self) -> None:
        """Bring an archived or contradicted memory back into retrieval."""
        self.status = MemoryStatus.ACTIVE
        self.superseded_by = None
        self.conflicts_with = []
        self.touch()

    @property
    def half_life_days(self) -> float | None:
        return CATEGORY_HALF_LIFE_DAYS.get(self.category)

    def decayed_confidence(self, *, now: datetime, floor: float) -> float:
        """Confidence after exponential decay since last reinforcement.

        Pure function of stored state, so the sweep is idempotent — running it
        twice in a day gives the same answer as running it once.
        """
        half_life = self.half_life_days
        if half_life is None:
            return self.confidence
        elapsed_days = (now - self.last_reinforced_at).total_seconds() / 86_400
        if elapsed_days <= 0:
            return self.confidence
        decayed = self.confidence * (0.5 ** (elapsed_days / half_life))
        return max(floor, round(decayed, 4))

    def recency_weight(
        self, *, now: datetime, half_life_days: float, floor: float
    ) -> float:
        """How topical this memory is, 0..1, for retrieval ranking.

        Separate from `decayed_confidence` on purpose. Decay answers "do we still
        believe this" and is per-category; recency answers "is this still what
        the person is working on" and applies to every category equally — a
        two-year-old event is no less true and no more relevant.

        Floored rather than allowed to reach zero: age should down-rank a memory,
        never make it unreachable.
        """
        elapsed_days = (now - self.last_reinforced_at).total_seconds() / 86_400
        if elapsed_days <= 0:
            return 1.0
        weight = 0.5 ** (elapsed_days / half_life_days)
        return round(max(floor, min(1.0, weight)), 4)

    @property
    def is_immutable(self) -> bool:
        return self.category in IMMUTABLE_CATEGORIES

    def to_payload(self) -> dict:
        """Qdrant payload. Datetimes are ISO strings so they stay filterable."""
        data = self.model_dump(mode="json")
        return data

    @classmethod
    def from_payload(cls, payload: dict) -> Memory:
        return cls.model_validate(payload)


class ExtractedFact(BaseModel):
    """Raw output of the extraction step, before it becomes a Memory.

    Deliberately separate from `Memory`: extraction is a cheap, stateless LLM
    call, while promoting a fact into a memory is a stateful decision that the
    Phase 2 resolution layer owns.
    """

    content: str
    category: MemoryCategory = MemoryCategory.FACT
    subject: str | None = None
    source_excerpt: str | None = None

    def to_memory(
        self,
        *,
        user_id: str,
        source_id: str | None,
        confidence: float,
    ) -> Memory:
        return Memory(
            user_id=user_id,
            content=self.content,
            category=self.category,
            subject=self.subject,
            source_id=source_id,
            source_excerpt=self.source_excerpt,
            confidence=confidence,
        )
