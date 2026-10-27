"""A typed client for Continuum's HTTP API — long-term memory for an agent.

    from continuum_sdk import Continuum

    memory = Continuum()                       # CONTINUUM_URL, CONTINUUM_API_KEY
    memory.remember("We moved the Atlas event store to MongoDB")
    found = memory.recall("what database does Atlas use?")
    for item in found.memories: ...
    for dispute in found.disputes: ...         # the record disagrees: say so, ask

Five calls, each one endpoint. What they return is shaped for an agent's
prompt: the belief, how sure the graph is of it, when it was recorded — and,
first-class, where the record disagrees with itself.

`settle` is the one call that changes what is believed, and it takes the
person's own words. Continuum refuses an agent that settles a dispute on its own
judgement (commitment 2: a model does not arbitrate a real contradiction); the
words are kept with the decision.

`Continuum` and `AsyncContinuum` share every request and every parser below, so
the two cannot drift apart.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

DEFAULT_URL = "http://localhost:8000"
API_PREFIX = "/api/v1"
#: Writing a note runs extraction and the resolver on a model: seconds, not ms.
DEFAULT_TIMEOUT = 120.0


class ContinuumError(Exception):
    """The API refused or failed a request. `detail` is its reason, as sent."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


# --- What comes back ------------------------------------------------------------


@dataclass(frozen=True)
class Memory:
    id: str
    content: str
    category: str
    status: str
    confidence: float
    created_at: str
    subject: str | None = None
    source_excerpt: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Memory:
        return cls(
            id=data["id"],
            content=data["content"],
            category=data["category"],
            status=data["status"],
            confidence=data["confidence"],
            created_at=data["created_at"],
            subject=data.get("subject"),
            source_excerpt=data.get("source_excerpt"),
        )


@dataclass(frozen=True)
class Recalled:
    memory: Memory
    score: float


@dataclass(frozen=True)
class Disagreement:
    """Beliefs that contradict each other. Surface both; never pick one."""

    subject: str | None
    memories: list[Memory]


@dataclass(frozen=True)
class Recall:
    query: str
    memories: list[Recalled]
    disputes: list[Disagreement]
    #: Said, not decided ("maybe I'll…"): to ask about, never to state as fact.
    pending: list[Memory]


@dataclass(frozen=True)
class Remembered:
    """What one note did to the graph."""

    extracted: int
    created: list[Memory]
    reinforced: list[str]
    superseded: list[str]
    disputes_raised: list[str]
    pending: list[str]
    confirmed: list[str]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Remembered:
        return cls(
            extracted=data["extracted"],
            created=[Memory.from_json(m) for m in data.get("created", [])],
            reinforced=data.get("reinforced", []),
            superseded=data.get("superseded", []),
            disputes_raised=data.get("conflicts_raised", []),
            pending=data.get("pending", []),
            confirmed=data.get("confirmed", []),
        )


@dataclass(frozen=True)
class OpenDispute:
    """One disagreement waiting for the person."""

    memory: Memory
    conflicting: list[Memory]
    #: Your private belief against the team's: settle it in Continuum's inbox.
    team: bool = False


@dataclass(frozen=True)
class Settled:
    action: Literal["superseded", "kept_both"]
    winner: Memory
    losers: list[Memory]


@dataclass(frozen=True)
class ChatAnswer:
    text: str
    cited_ids: list[str] = field(default_factory=list)
    #: None if the turn was not remembered, or writing it failed.
    remembered: Remembered | None = None


# --- Requests and parsers, shared by both clients ---------------------------------


def _remember_body(
    text: str | None, messages: list[dict[str, str]] | None, source_id: str | None
) -> dict[str, Any]:
    if (text is None) == (messages is None):
        raise ValueError("Pass exactly one of `text` or `messages`.")
    body: dict[str, Any] = {"text": text} if text is not None else {"messages": messages}
    if source_id:
        body["source_id"] = source_id
    return body


def _settle_body(
    winner_id: str, said: str, loser_ids: list[str] | None, keep_both: bool
) -> dict[str, Any]:
    if not said or not said.strip():
        # The server refuses this too; failing here says why before a round trip.
        raise ValueError(
            "settle needs the person's own words in `said` — an agent passes on a "
            "decision the person made, it never makes one."
        )
    body: dict[str, Any] = {"winner_id": winner_id, "keep_both": keep_both, "said": said.strip()}
    if loser_ids:
        body["loser_ids"] = loser_ids
    return body


def _chat_body(message: str | list[dict[str, str]], remember: bool) -> dict[str, Any]:
    messages = [{"role": "user", "content": message}] if isinstance(message, str) else message
    return {"messages": messages, "remember": remember}


def _recall(data: dict[str, Any]) -> Recall:
    return Recall(
        query=data["query"],
        memories=[
            Recalled(memory=Memory.from_json(r["memory"]), score=r["score"])
            for r in data.get("memories", [])
        ],
        disputes=[
            Disagreement(
                subject=d.get("subject"), memories=[Memory.from_json(m) for m in d["memories"]]
            )
            for d in data.get("disagreements", [])
        ],
        pending=[Memory.from_json(m) for m in data.get("pending", [])],
    )


def _disputes(data: dict[str, Any]) -> list[OpenDispute]:
    return [
        OpenDispute(
            memory=Memory.from_json(c["memory"]),
            conflicting=[Memory.from_json(m) for m in c["conflicting"]],
            team=c.get("team", False),
        )
        for c in data.get("conflicts", [])
    ]


def _settled(data: dict[str, Any]) -> Settled:
    return Settled(
        action=data["action"],
        winner=Memory.from_json(data["winner"]),
        losers=[Memory.from_json(m) for m in data["losers"]],
    )


class _ChatCollector:
    """Folds a chat stream's server-sent events into one answer."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.cited: list[str] = []
        self.remembered: Remembered | None = None
        self._event = "message"
        self._data: list[str] = []

    def feed(self, line: str) -> None:
        if line == "":
            self._dispatch()
        elif line.startswith("event:"):
            self._event = line[6:].strip()
        elif line.startswith("data:"):
            self._data.append(line[5:].lstrip())

    def _dispatch(self) -> None:
        if not self._data:
            return
        payload = json.loads("\n".join(self._data))
        if self._event == "delta":
            self.text.append(payload["text"])
        elif self._event == "done":
            self.cited = payload.get("cited_ids", [])
        elif self._event == "remembered" and payload.get("remembered"):
            self.remembered = Remembered.from_json(payload["remembered"])
        elif self._event == "error":
            raise ContinuumError(500, payload.get("message", "chat failed"))
        self._event, self._data = "message", []

    def answer(self) -> ChatAnswer:
        self._dispatch()
        return ChatAnswer(text="".join(self.text), cited_ids=self.cited, remembered=self.remembered)


def _settings(api_key: str | None, base_url: str | None) -> tuple[str, dict[str, str]]:
    key = api_key or os.environ.get("CONTINUUM_API_KEY")
    if not key:
        raise ValueError(
            "No API key: pass api_key= or set CONTINUUM_API_KEY. Create one in Continuum "
            "under Settings → API keys; its mode (Personal or Work) is the graph it uses."
        )
    url = (base_url or os.environ.get("CONTINUUM_URL") or DEFAULT_URL).rstrip("/")
    return f"{url}{API_PREFIX}", {"Authorization": f"Bearer {key}"}


def _checked(response: httpx.Response) -> Any:
    if response.is_success:
        return response.json()
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise ContinuumError(response.status_code, str(detail))


# --- The clients ------------------------------------------------------------------


class Continuum:
    """Continuum for a synchronous agent. Use as a context manager, or `close()`."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url, headers = _settings(api_key, base_url)
        self._http = httpx.Client(
            base_url=url, headers=headers, timeout=timeout, transport=transport
        )

    def remember(
        self,
        text: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        source_id: str | None = None,
    ) -> Remembered:
        """Feed a note or a conversation through extraction and the resolver."""
        body = _remember_body(text, messages, source_id)
        return Remembered.from_json(_checked(self._http.post("/ingest", json=body)))

    def recall(self, query: str, *, limit: int | None = None) -> Recall:
        """What the graph believes about `query` — and where it disagrees."""
        body: dict[str, Any] = {"query": query} | ({"limit": limit} if limit else {})
        return _recall(_checked(self._http.post("/chat/context", json=body)))

    def disputes(self) -> list[OpenDispute]:
        """Every disagreement waiting for the person."""
        return _disputes(_checked(self._http.get("/conflicts")))

    def settle(
        self,
        winner_id: str,
        *,
        said: str,
        loser_ids: list[str] | None = None,
        keep_both: bool = False,
    ) -> Settled:
        """Apply the person's decision on a dispute, in their own words."""
        body = _settle_body(winner_id, said, loser_ids, keep_both)
        return _settled(_checked(self._http.post("/conflicts/resolve", json=body)))

    def chat(self, message: str | list[dict[str, str]], *, remember: bool = True) -> ChatAnswer:
        """Continuum's own memory-augmented answer, read to the end of its stream."""
        collector = _ChatCollector()
        with self._http.stream("POST", "/chat", json=_chat_body(message, remember)) as response:
            if not response.is_success:
                response.read()
                _checked(response)
            for line in response.iter_lines():
                collector.feed(line)
        return collector.answer()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Continuum:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AsyncContinuum:
    """Continuum for an async agent (and the MCP server). `async with`, or `aclose()`."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url, headers = _settings(api_key, base_url)
        self._http = httpx.AsyncClient(
            base_url=url, headers=headers, timeout=timeout, transport=transport
        )

    async def remember(
        self,
        text: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        source_id: str | None = None,
    ) -> Remembered:
        body = _remember_body(text, messages, source_id)
        return Remembered.from_json(_checked(await self._http.post("/ingest", json=body)))

    async def recall(self, query: str, *, limit: int | None = None) -> Recall:
        body: dict[str, Any] = {"query": query} | ({"limit": limit} if limit else {})
        return _recall(_checked(await self._http.post("/chat/context", json=body)))

    async def disputes(self) -> list[OpenDispute]:
        return _disputes(_checked(await self._http.get("/conflicts")))

    async def settle(
        self,
        winner_id: str,
        *,
        said: str,
        loser_ids: list[str] | None = None,
        keep_both: bool = False,
    ) -> Settled:
        body = _settle_body(winner_id, said, loser_ids, keep_both)
        return _settled(_checked(await self._http.post("/conflicts/resolve", json=body)))

    async def chat(
        self, message: str | list[dict[str, str]], *, remember: bool = True
    ) -> ChatAnswer:
        collector = _ChatCollector()
        async with self._http.stream(
            "POST", "/chat", json=_chat_body(message, remember)
        ) as response:
            if not response.is_success:
                await response.aread()
                _checked(response)
            async for line in response.aiter_lines():
                collector.feed(line)
        return collector.answer()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncContinuum:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
