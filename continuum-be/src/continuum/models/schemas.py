"""Request / response models for the HTTP layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from continuum.models.feedback import RejectReason, ResolutionLabel
from continuum.models.memory import Memory, MemoryCategory, MemoryStatus


class ClientBody(BaseModel):
    """Base for every request body a client sends.

    Unknown fields are refused rather than ignored, and `user_id` gets its own
    message: it was part of every request before authentication, and a client
    still sending it must learn that it no longer chooses whose graph it writes
    to — not have it silently dropped while it assumes otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_user_id(cls, data: Any) -> Any:
        # Bodies that create an account take a user_id for the NEW account; that
        # is naming, not claiming to be someone, so they declare the field.
        if isinstance(data, dict) and "user_id" in data and "user_id" not in cls.model_fields:
            raise ValueError(
                "user_id is not accepted: the memory owner is whoever the API key or "
                "session belongs to."
            )
        return data


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class _IngestFields(BaseModel):
    """Feed the system something to remember.

    Supply either `text` (a note, a meeting summary, a decision record) or
    `messages` (a conversation transcript). Exactly one is required.
    """

    text: str | None = None
    messages: list[Message] | None = None
    source_id: str | None = Field(
        default=None, description="Caller-supplied id to trace memories back to their origin."
    )
    share: bool = Field(
        default=False,
        description="Store what this extracts in the shared team space instead of your "
        "own graph. Everyone on the instance can then read it.",
    )

    def as_transcript(self) -> str:
        if self.text:
            return self.text.strip()
        if self.messages:
            return "\n".join(f"{m.role}: {m.content}" for m in self.messages).strip()
        return ""


class IngestBody(ClientBody, _IngestFields):
    """What a client posts to /ingest."""


class IngestRequest(_IngestFields):
    """What IngestService consumes: the body plus the owner, set by the server."""

    user_id: str = Field(..., min_length=1)
    # Who is writing, when the owner is the shared space: recorded on every
    # memory it creates there, so teammates can see who said it.
    author: str | None = None
    author_email: str | None = None


class ResolutionRecord(BaseModel):
    """Audit trail for one fact's trip through the resolver.

    Returned on every ingest so the decision is inspectable without trawling
    logs — which matters a lot while tuning the confidence gate.
    """

    memory_id: str
    content: str
    verdict: str
    target_id: str | None = None
    judge_confidence: float | None = None
    reason: str = ""
    escalated: bool = Field(
        default=False,
        description="True when a 'supersedes' was downgraded to a human escalation.",
    )


class IngestResponse(BaseModel):
    source_id: str
    extracted: int = Field(..., description="Facts the LLM pulled out of the input.")
    created: list[Memory] = Field(default_factory=list)
    duplicates_skipped: int = 0
    reinforced: list[str] = Field(
        default_factory=list, description="IDs of existing memories confirmed by this input."
    )
    superseded: list[str] = Field(
        default_factory=list, description="IDs retired automatically by a confident supersede."
    )
    conflicts_raised: list[str] = Field(
        default_factory=list, description="IDs now awaiting human resolution."
    )
    team_conflicts: list[str] = Field(
        default_factory=list,
        description="Team memories a new private one disagrees with (flagged on yours).",
    )
    resolutions: list[ResolutionRecord] = Field(default_factory=list)


class MemorySearchRequest(ClientBody):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=50)
    categories: list[MemoryCategory] | None = None
    statuses: list[MemoryStatus] | None = None


class ScoredMemory(BaseModel):
    memory: Memory
    score: float


class MemorySearchResponse(BaseModel):
    query: str
    results: list[ScoredMemory]


class MemoryListResponse(BaseModel):
    total: int
    memories: list[Memory]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    environment: str
    qdrant: str
    llm: str
    database: str


# --- Phase 2: conflicts, decay, graph ---------------------------------------


class ConflictPair(BaseModel):
    """One unresolved disagreement, ready for the inbox UI."""

    memory: Memory
    conflicting: list[Memory]
    team: bool = Field(
        False,
        description="Your private belief against the team's. Settled with "
        "/conflicts/resolve-team, since a decision never crosses graphs on its own.",
    )


class ConflictListResponse(BaseModel):
    total: int
    conflicts: list[ConflictPair]


class ConflictResolutionRequest(ClientBody):
    """A human's verdict on a conflict.

    `keep_both` is a first-class outcome, not a cop-out: plenty of apparent
    contradictions are two things that are simply both true.
    """

    winner_id: str
    loser_ids: list[str] = Field(default_factory=list)
    keep_both: bool = False


class ConflictResolutionResponse(BaseModel):
    winner: Memory
    losers: list[Memory]
    action: Literal["superseded", "kept_both"]


class DecaySweepRequest(ClientBody):
    dry_run: bool = Field(
        default=False, description="Compute the sweep without writing anything."
    )


class GraphNode(BaseModel):
    id: str
    label: str
    category: MemoryCategory
    status: MemoryStatus
    confidence: float
    subject: str | None = None
    created_at: str
    shared: bool = False
    shared_by_email: str | None = None
    kind: Literal["fact", "summary"] = "fact"


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: Literal["supersedes", "conflicts_with"]


class GraphResponse(BaseModel):
    """Shape consumed directly by the Phase 4 Three.js view."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


# --- Phase 3: memory-augmented chat -----------------------------------------


class RetrievedMemory(BaseModel):
    """One memory selected for a chat turn, with its ranking broken out.

    The three factors are returned separately rather than folded into a single
    number so the Phase 4 UI can explain *why* a memory surfaced, and so Phase 5
    can tune the weights against real retrievals.
    """

    memory: Memory
    similarity: float = Field(..., description="Raw cosine score from the vector search.")
    recency: float = Field(..., description="Topicality weight, 1.0 = reinforced just now.")
    keyword: float = Field(
        0.0, description="Share of the question's content words this memory contains."
    )
    score: float = Field(
        ..., description="max(similarity, keyword x w_kw) x confidence^w x recency^w."
    )


class Disagreement(BaseModel):
    """Two or more retrieved beliefs that contradict each other.

    Surfaced to the model *and* to the client. An agent that silently picks one
    side of an unresolved contradiction is the failure mode this project exists
    to prevent, so the disagreement is a first-class part of the response.
    """

    subject: str | None = None
    memories: list[Memory]


class ChatContext(BaseModel):
    query: str
    memories: list[RetrievedMemory] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    as_of: datetime | None = Field(
        default=None, description="Set when these are the beliefs held at a past moment."
    )


class _ChatFields(BaseModel):
    messages: list[Message] = Field(..., min_length=1)
    limit: int | None = Field(
        default=None, ge=1, le=25, description="Memories to inject. Defaults to config."
    )
    share: bool = Field(
        default=False,
        description="Remember this turn in the shared team space instead of your own graph.",
    )
    remember: bool | None = Field(
        default=None,
        description=(
            "Run this turn back through ingest, confirming memories it repeats and "
            "recording anything new. Defaults to config."
        ),
    )
    as_of: datetime | None = Field(
        default=None,
        description="Answer from what was believed at this moment. Such a turn is never "
        "remembered: a question about the past is not news about the present.",
    )

    def latest_user_message(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                return message.content.strip()
        return ""

    def total_chars(self) -> int:
        return sum(len(m.content) for m in self.messages)


class ChatBody(ClientBody, _ChatFields):
    """What a client posts to /chat."""


class ChatRequest(_ChatFields):
    """What ChatService consumes: the body plus the owner, set by the server."""

    user_id: str = Field(..., min_length=1)
    author_email: str | None = None


class ChatContextRequest(ClientBody):
    """Retrieval preview — what chat *would* see, with no generation."""

    query: str = Field(..., min_length=1)
    limit: int | None = Field(default=None, ge=1, le=25)
    as_of: datetime | None = None


class ChatDone(BaseModel):
    """Terminal event of a chat stream."""

    memory_ids: list[str] = Field(
        default_factory=list, description="Every memory placed in the model's context."
    )
    cited_ids: list[str] = Field(
        default_factory=list, description="Memories the answer actually cited, in order."
    )
    disagreements: int = 0
    remembered: IngestResponse | None = Field(
        default=None, description="What this turn contributed back to the graph."
    )


class ChatEvent(BaseModel):
    """One server-sent event. The service yields these; the route serialises them.

    Keeps `ChatService` free of any FastAPI/Starlette import, per the layering
    rules.
    """

    event: Literal["context", "delta", "done", "error"]
    data: dict


# --- Shared team space ----------------------------------------------------------


class RejectBody(ClientBody):
    reason: RejectReason
    note: str | None = Field(default=None, max_length=500)


class RejectResponse(BaseModel):
    memory: Memory
    restored: list[str] = Field(
        default_factory=list,
        description="Beliefs this misreading had retired, now active again.",
    )


class SummaryRefreshResponse(BaseModel):
    written: list[Memory]


class MemoryExport(BaseModel):
    """Everything in your own graph, every status, with its edges."""

    exported_at: datetime
    user_id: str
    total: int
    memories: list[Memory]


class ShareResponse(BaseModel):
    """What sharing a memory did, as the shared graph's resolver saw it."""

    outcome: Literal["created", "merged", "superseded", "conflict"] = Field(
        ...,
        description="created: new to the team. merged: the team already knew it. "
        "superseded: it replaced an older shared belief. conflict: it contradicts "
        "one, and is waiting in the shared inbox.",
    )
    original: Memory = Field(
        ..., description="Your private memory, now retired into the shared one."
    )
    shared: Memory


# --- Learning from decisions ---------------------------------------------------


class LabelListResponse(BaseModel):
    total: int
    labels: list[ResolutionLabel]


# --- Speech -------------------------------------------------------------------


class SpeechStatus(BaseModel):
    enabled: bool
    ready: bool = Field(..., description="Model loaded. False during the first download.")
    max_seconds: int
    language: str | None = None
    # Server-side voice for read-aloud and autopilot. When off, the UI falls back
    # to the browser's own speech synthesis.
    synthesis: bool = False
    synthesis_ready: bool = False
    synthesis_max_chars: int = 800


class SynthesisRequest(ClientBody):
    text: str = Field(..., min_length=1)
    # Normally the person's saved preference, sent by the UI; Settings also uses
    # these to let someone hear a voice before choosing it.
    voice: str | None = None
    speed: float | None = Field(default=None, ge=0.6, le=1.8)


class VoiceOption(BaseModel):
    id: str
    label: str
    accent: str
    gender: str


class VoiceSettings(BaseModel):
    """Your spoken-answer settings, and what you can choose from."""

    voice: str = Field(..., description="Your voice, or the server default if you never chose.")
    speed: float = 1.0
    default_voice: str
    voices: list[VoiceOption]


class VoiceSettingsUpdate(ClientBody):
    voice: str | None = Field(default=None, description="A voice id from `voices`; null = default.")
    speed: float | None = Field(default=None, ge=0.6, le=1.8)


# --- Authentication -----------------------------------------------------------


class AuthStatus(BaseModel):
    """Public: tells the web UI whether to show sign-in or first-run setup."""

    needs_setup: bool
    web_setup_allowed: bool


class SetupRequest(ClientBody):
    email: str
    password: str
    user_id: str | None = Field(
        default=None,
        description=(
            "Memory owner id for this first admin. Set it to adopt memories stored "
            "before authentication existed; otherwise a random id is assigned."
        ),
    )


class LoginRequest(ClientBody):
    email: str
    password: str


class Me(BaseModel):
    user_id: str
    email: str
    is_admin: bool
    via: Literal["session", "api_key"]


class PasswordChangeRequest(ClientBody):
    current_password: str
    new_password: str


class ApiKeyCreateRequest(ClientBody):
    name: str = Field(..., description="What this key is for, e.g. 'laptop agent'.")


class ApiKeySummary(BaseModel):
    id: str
    name: str
    prefix: str = Field(..., description="The first characters of the key, to recognise it.")
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(BaseModel):
    key: ApiKeySummary
    secret: str = Field(
        ..., description="The full key. Shown once; it cannot be retrieved again."
    )


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeySummary]


class UserSummary(BaseModel):
    id: str
    email: str
    is_admin: bool
    disabled: bool
    created_at: datetime


class UserListResponse(BaseModel):
    users: list[UserSummary]


class UserCreateRequest(ClientBody):
    email: str
    password: str
    user_id: str | None = None
    is_admin: bool = False


class UserUpdateRequest(ClientBody):
    disabled: bool | None = None
    is_admin: bool | None = None


class TeamResolutionRequest(ClientBody):
    """Your private belief disagrees with the team's. Which holds?

    team_holds: retire yours into the team's. mine_holds: share yours, so the team
    graph's resolver (and, if it is unsure, the team) decides. both_hold: keep both.
    """

    memory_id: str
    shared_id: str
    decision: Literal["team_holds", "mine_holds", "both_hold"]


class TeamResolutionResponse(BaseModel):
    memory: Memory
    action: Literal["team_holds", "mine_holds", "both_hold"]
    shared: ShareResponse | None = None
