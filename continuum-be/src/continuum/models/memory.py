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
from typing import Literal

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
    # Personal mode (requirements.md §4.1). Extracted only by the personal prompt.
    HEALTH = "health"           # "Mark is allergic to penicillin" — never decays
    ROUTINE = "routine"         # "Mark goes to the gym on weekday mornings"
    GOAL = "goal"               # "Mark wants to read 20 books this year"


class MemoryStatus(StrEnum):
    ACTIVE = "active"           # trusted, used in retrieval
    SUPERSEDED = "superseded"   # explicitly replaced by a newer memory
    CONTRADICTED = "contradicted"  # conflict detected, awaiting human resolution
    ARCHIVED = "archived"       # decayed below threshold; recoverable, not retrieved
    # Said, not decided ("maybe", "I'll think about it"). Never a belief: it is
    # never judged against, never retires anything, and is raised as a question
    # until the person answers (requirements.md §14).
    PENDING = "pending"
    DECLINED = "declined"       # the person said no to a pending item; kept for audit


# Categories that represent immutable history and must never be superseded.
IMMUTABLE_CATEGORIES: frozenset[MemoryCategory] = frozenset({MemoryCategory.EVENT})

# Categories that state what is true of the work, and so can contradict each
# other whichever one the extractor chose. It files the same kind of statement
# under different categories ("Atlas is using MongoDB" as a fact, "chose Postgres
# for Atlas" as a decision; ownership as fact or person), and a strict
# same-category filter waved those contradictions through as NEW without asking.
# Comparing within the family costs a judge call between memories about the same
# subject; splitting it silently switches contradiction detection off.
# PREFERENCE stays on its own (a taste does not replace a decision), and EVENT
# is never compared at all.
STATEMENT_CATEGORIES: frozenset[MemoryCategory] = frozenset(
    {
        MemoryCategory.DECISION,
        MemoryCategory.FACT,
        MemoryCategory.CONSTRAINT,
        MemoryCategory.PERSON,
        # "Stopped taking vitamin D" must still be compared with "takes vitamin D
        # daily", whichever category the extractor chose for each.
        MemoryCategory.HEALTH,
        MemoryCategory.ROUTINE,
        MemoryCategory.GOAL,
    }
)

# Never shared, never summarised, kept out of exports unless asked (requirements §6).
SENSITIVE_CATEGORIES: frozenset[MemoryCategory] = frozenset({MemoryCategory.HEALTH})


def comparable_categories(a: MemoryCategory, b: MemoryCategory) -> bool:
    """Could a memory of category `b` be contradicted by a new one of `a`?"""
    if a in IMMUTABLE_CATEGORIES or b in IMMUTABLE_CATEGORIES:
        return False
    return a is b or (a in STATEMENT_CATEGORIES and b in STATEMENT_CATEGORIES)


# Statuses that participate in retrieval for chat context.
#
# CONTRADICTED is deliberately retrievable. When two beliefs are in unresolved
# conflict the honest behaviour is for the agent to surface both and say so —
# not to silently pick one, and not to go quiet on the subject entirely.
RETRIEVABLE_STATUSES: frozenset[MemoryStatus] = frozenset(
    {MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED}
)

# Choices the person can adopt from a suggestion, or leave pending. Never a fact
# about who they are, and never health: "ok" to a suggested supplement is not a
# medication (requirements.md §14).
CHOICE_CATEGORIES: frozenset[MemoryCategory] = frozenset(
    {MemoryCategory.DECISION, MemoryCategory.ROUTINE, MemoryCategory.GOAL,
     MemoryCategory.PREFERENCE}
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
    # Not extracted in Workspace, but a value for every category keeps the
    # table total (and the frontend's mirror type-checked).
    MemoryCategory.HEALTH: None,
    MemoryCategory.ROUTINE: 45.0,
    MemoryCategory.GOAL: 120.0,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


# The owner id of the shared team space. A reserved value no account can take:
# account ids must start with a letter or digit (models/auth.USER_ID_PATTERN).
# Shared knowledge is simply another graph with this owner, so resolution,
# disputes, decay and labels all work on it unchanged.
SHARED_SPACE = "_shared"

Owners = str | list[str]


class Mode(StrEnum):
    """Which of a person's graphs is in use (requirements.md).

    Each mode is its own graph with its own owner id, like the team space: the
    resolver, decay, summaries and labels all work per graph, so a personal fact
    can never retire a work one, or appear in a work answer — structurally, not
    by remembering to add a filter.
    """

    PERSONAL = "personal"
    WORKSPACE = "workspace"


# Appended to an account id for its Personal graph. `:` cannot appear in an
# account id (models/auth.USER_ID_PATTERN), so no account can claim the result.
PERSONAL_SUFFIX = ":personal"


def graph_owner(user_id: str, mode: Mode) -> str:
    """The owner id of `user_id`'s graph in `mode`. Workspace keeps the plain id,
    so every memory stored before modes existed is still where it was."""
    return f"{user_id}{PERSONAL_SUFFIX}" if mode is Mode.PERSONAL else user_id


def mode_of(owner: str) -> Mode:
    """The mode a graph belongs to, read from its owner id."""
    return Mode.PERSONAL if owner.endswith(PERSONAL_SUFFIX) else Mode.WORKSPACE


# How fast an unconfirmed memory fades, per mode (requirements.md §4.1). Life
# facts change more slowly than work ones, except routines and limits; health
# never fades — a forgotten allergy is dangerous.
PERSONAL_HALF_LIFE_DAYS: dict[MemoryCategory, float | None] = {
    MemoryCategory.PERSON: 730.0,
    MemoryCategory.PREFERENCE: 180.0,
    MemoryCategory.FACT: 240.0,
    MemoryCategory.DECISION: 180.0,
    MemoryCategory.CONSTRAINT: 60.0,
    MemoryCategory.EVENT: None,
    MemoryCategory.HEALTH: None,
    MemoryCategory.ROUTINE: 45.0,
    MemoryCategory.GOAL: 120.0,
}


# A choice taken up from the assistant's suggestion. The extractor files the
# same adopted split as a decision (180 d) in one run and a routine (45 d) in the
# next (FINDINGS §14), so how fast it faded was a coin toss. One rate for all of
# them, in both modes — a product decision (2026-10-26). Restating it in the
# person's own words reinforces it, which restarts the clock.
ADOPTED_CHOICE_HALF_LIFE_DAYS = 90.0


def half_life_for(
    category: MemoryCategory, mode: Mode, *, adopted: bool = False
) -> float | None:
    if adopted and category in CHOICE_CATEGORIES:
        return ADOPTED_CHOICE_HALF_LIFE_DAYS
    table = PERSONAL_HALF_LIFE_DAYS if mode is Mode.PERSONAL else CATEGORY_HALF_LIFE_DAYS
    return table.get(category)


def visible_owners(user_id: str, *, shared: bool = True) -> list[str]:
    """Whose memories a graph may read: its own, plus the team space.

    The team space belongs to Workspace only. A Personal graph reads itself and
    nothing else — decided from the owner id, so every caller is mode-safe.
    """
    if mode_of(user_id) is Mode.PERSONAL or user_id == SHARED_SPACE or not shared:
        return [user_id]
    return [user_id, SHARED_SPACE]


class Escalation(BaseModel):
    """Why the resolver asked a human about this memory, recorded when it did.

    Kept on the incoming memory so that when a person later settles the
    conflict, their answer can be set against what the system thought — the
    judge's relation and probability, the similarity, the gate in force. That
    pairing is the labelled data the resolver is checked and tuned against
    (services/feedback.py). Without it a human decision fixes one conflict and
    teaches nothing.
    """

    target_id: str = Field(..., description="The existing memory this one was judged against.")
    judge_relation: str | None = Field(
        default=None,
        description="What the judge said: 'supersedes' (downgraded by the gate or a "
        "policy) or 'conflict'. None when no judge call was made.",
    )
    judge_confidence: float | None = None
    similarity: float | None = None
    gate: float = Field(..., description="auto_supersede_confidence at the time.")
    forced: bool = Field(
        default=False, description="Escalated by policy (the role rule) whatever the confidence."
    )
    reason: str = ""
    raised_at: datetime = Field(default_factory=_utcnow)


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
    # --- Sharing -----------------------------------------------------------
    shared_by: str | None = Field(
        default=None, description="Account that put this memory in the shared space."
    )
    shared_by_email: str | None = Field(
        default=None, description="Their email, so teammates can see who shared it."
    )
    shared_as: str | None = Field(
        default=None,
        description="On a private memory that was shared: the shared memory that now "
        "carries it. The private one is retired by that edge, never deleted.",
    )
    escalations: list[Escalation] = Field(
        default_factory=list,
        description="What the resolver thought each time it escalated this memory to a human.",
    )
    team_conflicts_with: list[str] = Field(
        default_factory=list,
        description="Shared-space memories this private one disagrees with. Recorded on "
        "the private side only: a private belief never changes team knowledge.",
    )

    # --- Derived memories ---------------------------------------------------
    kind: Literal["fact", "summary"] = Field(
        default="fact",
        description="'summary': written by the system from other memories. Retrieved "
        "for the big picture, never judged against facts and never counted as evidence.",
    )
    derived_from: list[str] = Field(
        default_factory=list, description="For a summary: the memories it was written from."
    )
    derived_fingerprint: str | None = Field(
        default=None, description="For a summary: which sources, and which were disputed."
    )

    # --- People's corrections -------------------------------------------------
    rejected_reason: str | None = Field(
        default=None,
        description="Someone said this was never a real fact (a question, two facts "
        "merged, a misreading). Archived, and kept as a lesson for the extractor.",
    )
    redacted_at: datetime | None = Field(
        default=None,
        description="Forgotten on request: the words are gone, the record and its edges "
        "remain so the history still makes sense.",
    )

    # --- Lifecycle ---------------------------------------------------------
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_reinforced_at: datetime = Field(default_factory=_utcnow)
    reinforcement_count: int = 0
    # When it stopped being believed, so "what did we believe on 3 March?" can be
    # answered. None on memories retired before this was recorded; as_of()
    # falls back to updated_at for those.
    superseded_at: datetime | None = None
    archived_at: datetime | None = None

    # --- Pending (said, not decided) ------------------------------------------
    reminders: int = Field(
        default=0, description="How many times a pending item was put to the person."
    )
    last_reminded_at: datetime | None = None
    decided_at: datetime | None = Field(
        default=None, description="When a pending item was confirmed or declined."
    )
    adopted: bool = Field(
        default=False,
        description="A choice the user took up from the assistant's answer rather than "
        "stated in their own words. Fades at one rate whatever its category.",
    )
    tentative_wording: str | None = Field(
        default=None,
        description="How a confirmed pending item read while it was only a maybe. "
        "Kept for audit; the content now says it as decided.",
    )

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
        self.superseded_at = _utcnow()
        self.touch()

    def mark_contradicted(self, other_id: str) -> None:
        """Flag an unresolved conflict. Stays retrievable — see RETRIEVABLE_STATUSES."""
        self.status = MemoryStatus.CONTRADICTED
        if other_id not in self.conflicts_with:
            self.conflicts_with.append(other_id)
        self.touch()

    def mark_team_conflict(self, shared_id: str) -> None:
        """Flag a private belief that disagrees with the team's. Only the private
        side carries the edge: a private fact never changes team knowledge."""
        self.status = MemoryStatus.CONTRADICTED
        if shared_id not in self.team_conflicts_with:
            self.team_conflicts_with.append(shared_id)
        self.touch()

    def mark_supersedes(self, other_id: str) -> None:
        """Record on the winner that it replaced `other_id`."""
        if other_id not in self.supersedes:
            self.supersedes.append(other_id)
        self.touch()

    def escalation_against(self, other_id: str) -> Escalation | None:
        """The most recent escalation of this memory against `other_id`, if any."""
        matches = [e for e in self.escalations if e.target_id == other_id]
        return matches[-1] if matches else None

    def clear_conflict_with(self, other_id: str) -> None:
        """Drop one conflict edge; return to ACTIVE once none remain."""
        self.conflicts_with = [c for c in self.conflicts_with if c != other_id]
        self.team_conflicts_with = [c for c in self.team_conflicts_with if c != other_id]
        if (
            not self.conflicts_with
            and not self.team_conflicts_with
            and self.status is MemoryStatus.CONTRADICTED
        ):
            self.status = MemoryStatus.ACTIVE
        self.touch()

    def archive(self) -> None:
        self.status = MemoryStatus.ARCHIVED
        self.archived_at = _utcnow()
        self.touch()

    def reactivate(self) -> None:
        """Bring an archived or contradicted memory back into retrieval."""
        self.status = MemoryStatus.ACTIVE
        self.superseded_by = None
        self.superseded_at = None
        self.archived_at = None
        self.rejected_reason = None
        self.conflicts_with = []
        self.team_conflicts_with = []
        self.touch()

    # --- Pending lifecycle --------------------------------------------------------

    @property
    def is_pending(self) -> bool:
        return self.status is MemoryStatus.PENDING

    def remind(self) -> None:
        """Counted when the question is actually put to the person, not when the
        item is merely retrieved — silence is not an answer."""
        self.reminders += 1
        self.last_reminded_at = _utcnow()
        self.touch()

    def confirm(self) -> None:
        """Said yes. It becomes a belief now, so it starts its life now: decay is
        measured from the moment it was decided, not when it was first floated."""
        self.status = MemoryStatus.ACTIVE
        self.decided_at = self.last_reinforced_at = _utcnow()
        self.touch()

    def decline(self) -> None:
        """Said no. Out of retrieval and never raised again; the record stays."""
        self.status = MemoryStatus.DECLINED
        self.decided_at = _utcnow()
        self.touch()

    def pending_expired(self, *, now: datetime, max_reminders: int, max_age_days: float) -> bool:
        """Asked enough times, or around long enough, with no answer."""
        if not self.is_pending:
            return False
        age_days = (now - self.created_at).total_seconds() / 86400
        return self.reminders >= max_reminders or age_days >= max_age_days

    def believed_at(self, when: datetime) -> bool:
        """Was this held as a belief at `when`? Pure, so time-travel queries are testable.

        Recorded by then, and not yet superseded or archived by then. Disputed
        memories count as held — a dispute is two beliefs, not none. A summary,
        a rejected misreading or a forgotten memory never counts.
        """
        if self.kind == "summary" or self.rejected_reason or self.redacted_at:
            return False
        if self.status in (MemoryStatus.PENDING, MemoryStatus.DECLINED):
            return False  # never a belief, then or now
        if self.created_at > when:
            return False
        ended = None
        if self.status is MemoryStatus.SUPERSEDED:
            ended = self.superseded_at or self.updated_at
        elif self.status is MemoryStatus.ARCHIVED:
            ended = self.archived_at or self.updated_at
        return ended is None or ended > when

    def redact(self) -> None:
        """Forget the words, keep the record.

        The one way content ever leaves Continuum. The memory keeps its id, its
        edges and its dates, so what superseded what still reads correctly —
        but nothing that says what it was: content, excerpt, subject and the
        resolver's reasons are cleared, and it leaves retrieval.
        """
        self.content = "[forgotten]"
        self.source_excerpt = None
        self.subject = None
        self.escalations = [e.model_copy(update={"reason": ""}) for e in self.escalations]
        self.redacted_at = _utcnow()
        if self.status in (MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED):
            self.status = MemoryStatus.ARCHIVED
            self.archived_at = self.redacted_at
        self.conflicts_with = []
        self.team_conflicts_with = []
        self.touch()

    @property
    def is_shared(self) -> bool:
        return self.user_id == SHARED_SPACE

    @property
    def half_life_days(self) -> float | None:
        return half_life_for(self.category, mode_of(self.user_id), adopted=self.adopted)

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
    tentative: bool = Field(
        default=False,
        description="Said, not decided ('maybe', 'I'll think about it'): stored as "
        "pending, never resolved against beliefs until the person confirms it.",
    )
    adopted: bool = Field(
        default=False,
        description="Taken from the assistant's answer the user agreed to, not said in "
        "their own words. Never reinforces an existing belief: an echo is not evidence.",
    )

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
            status=MemoryStatus.PENDING if self.tentative else MemoryStatus.ACTIVE,
            adopted=self.adopted,
        )
