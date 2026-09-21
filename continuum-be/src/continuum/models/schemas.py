"""Request / response models for the HTTP layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from continuum.models.memory import Memory, MemoryCategory, MemoryStatus


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class IngestRequest(BaseModel):
    """Feed the system something to remember.

    Supply either `text` (a note, a meeting summary, a decision record) or
    `messages` (a conversation transcript). Exactly one is required.
    """

    user_id: str = Field(..., min_length=1)
    text: str | None = None
    messages: list[Message] | None = None
    source_id: str | None = Field(
        default=None, description="Caller-supplied id to trace memories back to their origin."
    )

    def as_transcript(self) -> str:
        if self.text:
            return self.text.strip()
        if self.messages:
            return "\n".join(f"{m.role}: {m.content}" for m in self.messages).strip()
        return ""


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
    resolutions: list[ResolutionRecord] = Field(default_factory=list)


class MemorySearchRequest(BaseModel):
    user_id: str
    query: str
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


# --- Phase 2: conflicts, decay, graph ---------------------------------------


class ConflictPair(BaseModel):
    """One unresolved disagreement, ready for the inbox UI."""

    memory: Memory
    conflicting: list[Memory]


class ConflictListResponse(BaseModel):
    total: int
    conflicts: list[ConflictPair]


class ConflictResolutionRequest(BaseModel):
    """A human's verdict on a conflict.

    `keep_both` is a first-class outcome, not a cop-out: plenty of apparent
    contradictions are two things that are simply both true.
    """

    user_id: str
    winner_id: str
    loser_ids: list[str] = Field(default_factory=list)
    keep_both: bool = False


class ConflictResolutionResponse(BaseModel):
    winner: Memory
    losers: list[Memory]
    action: Literal["superseded", "kept_both"]


class DecaySweepRequest(BaseModel):
    user_id: str
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
    score: float = Field(..., description="similarity x confidence^w x recency^w.")


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


class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    messages: list[Message] = Field(..., min_length=1)
    limit: int | None = Field(
        default=None, ge=1, le=25, description="Memories to inject. Defaults to config."
    )
    remember: bool | None = Field(
        default=None,
        description=(
            "Run this turn back through ingest, confirming memories it repeats and "
            "recording anything new. Defaults to config."
        ),
    )

    def latest_user_message(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                return message.content.strip()
        return ""


class ChatContextRequest(BaseModel):
    """Retrieval preview — what chat *would* see, with no generation."""

    user_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    limit: int | None = Field(default=None, ge=1, le=25)


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
