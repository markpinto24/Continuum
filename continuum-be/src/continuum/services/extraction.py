"""Fact extraction: turn raw input into candidate memories.

Design note — why this prompt and not Mem0's built-in `add()`:

Mem0's fact-retrieval prompt is domain-agnostic and emits flat strings. We need
three extra things it does not give us:

  1. a `category`, because resolution policy differs per category (a DECISION is
     superseded by a later decision; an EVENT is immutable history),
  2. a normalised `subject`, so Phase 2 can narrow the conflict candidate set
     with a cheap payload filter before spending an LLM call,
  3. a `source_excerpt`, so every memory is traceable back to the span that
     produced it.

Mem0 remains in the dependency set and `Mem0Extractor` below wraps it, so the
two are swappable and directly comparable — but the native extractor is the
default because the extra structure is load-bearing downstream.
"""

from __future__ import annotations

import contextlib

import structlog

from continuum.clients.llm import LLMClient
from continuum.config import Settings
from continuum.models.memory import ExtractedFact, MemoryCategory

log = structlog.get_logger(__name__)


EXTRACTION_SYSTEM_PROMPT = """You extract durable work memories from text.

You are building the long-term memory of a professional. Capture only what will
still be worth knowing weeks from now. Ignore pleasantries, restated questions,
and anything transient.

For each memory, choose exactly one category:
- decision   : a choice that was made, ideally with its reason
- preference : how someone likes to work or be communicated with
- fact       : a stable state of the world (systems, ownership, numbers)
- event      : something that happened at a point in time (immutable history)
- person     : who someone is, their role or responsibility
- constraint : a limit, deadline, budget or hard requirement

Rules:
- Write each memory as a standalone third-person statement. It must be
  understandable with no surrounding context. Resolve pronouns to real names
  where the text makes them unambiguous.
- One fact per memory. Never join two ideas with "and".
- Preserve the REASON when one is given. "Chose Postgres over Mongo for
  relational integrity" is worth far more than "Chose Postgres".
- Set `subject` to a short normalised slug for the main entity the memory is
  about (e.g. "billing-service", "acme-corp", "sara"). Use null if there is no
  single clear entity.
- Set `source_excerpt` to the shortest verbatim span from the input that
  supports the memory.
- Record what the text asserts NOW. If it states a change of mind, record the
  new position as the memory. Do not try to reconcile it with anything else.
- If there is nothing durable to remember, return an empty list. An empty list
  is a correct and common answer.

Respond with JSON only, in exactly this shape:
{"memories": [{"content": "...", "category": "decision",
               "subject": "...", "source_excerpt": "..."}]}"""


class FactExtractor:
    """Native extractor — the default."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def extract(self, transcript: str) -> list[ExtractedFact]:
        text = transcript.strip()
        if not text:
            return []

        try:
            payload = await self.llm.complete_json(
                system=EXTRACTION_SYSTEM_PROMPT,
                user=f"Input:\n---\n{text}\n---",
            )
        except Exception as exc:  # noqa: BLE001
            log.error("extraction.failed", error=str(exc))
            return []

        return _coerce_facts(payload)


def _coerce_facts(payload: object) -> list[ExtractedFact]:
    """Be liberal in what we accept — small models drift from the schema."""
    if isinstance(payload, dict):
        raw = payload.get("memories") or payload.get("facts") or []
    elif isinstance(payload, list):
        raw = payload
    else:
        raw = []

    facts: list[ExtractedFact] = []
    for item in raw:
        if isinstance(item, str):
            content = item.strip()
            if content:
                facts.append(ExtractedFact(content=content))
            continue
        if not isinstance(item, dict):
            continue

        content = str(item.get("content") or item.get("memory") or item.get("text") or "").strip()
        if not content:
            continue

        category = _coerce_category(item.get("category"))
        subject = item.get("subject")
        excerpt = item.get("source_excerpt") or item.get("excerpt")

        facts.append(
            ExtractedFact(
                content=content,
                category=category,
                subject=_normalise_subject(subject),
                source_excerpt=str(excerpt).strip() if excerpt else None,
            )
        )

    log.info("extraction.completed", count=len(facts))
    return facts


def _coerce_category(value: object) -> MemoryCategory:
    if isinstance(value, str):
        try:
            return MemoryCategory(value.strip().lower())
        except ValueError:
            pass
    return MemoryCategory.FACT


def _normalise_subject(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    slug = value.strip().lower().replace(" ", "-").replace("_", "-")
    return slug or None


class Mem0Extractor:
    """Adapter around Mem0's own extraction — the Phase 5 baseline.

    This exists to answer one question with a number instead of an opinion: is
    the domain-tuned prompt above actually earning its keep, or would Mem0's
    general-purpose extraction have done just as well?

    Mem0 returns flat strings, so `category`, `subject` and `source_excerpt` fall
    back to defaults — which is precisely the gap being measured. A memory with
    no category cannot be routed by resolution policy, and one with no subject
    cannot be filtered before the judge, so a baseline that ties on raw fact
    recall can still be unusable downstream.

    Isolation matters: Mem0's `add()` performs its own dedup-and-update pass
    against whatever it has already stored, so a shared store would measure
    extraction and update together and credit the result to extraction. `reset()`
    empties the store between documents, which makes every result an ADD.

    Reset rather than a fresh instance per document, because Mem0 also keeps an
    internal migrations Qdrant under ~/.mem0, an embedded Qdrant permits one
    concurrent opener, and that store is not released by `close()`. Building a
    second instance in the same process dies on the second document.
    """

    def __init__(self, memory: object) -> None:
        self._memory = memory

    @classmethod
    def create(cls, settings: Settings, storage_dir: str) -> Mem0Extractor:
        """Build an extractor backed by an isolated, local Mem0 store.

        `storage_dir` is a temporary directory; Qdrant runs embedded from it, so
        nothing touches the real collection. Build one per run, not per document.
        """
        from mem0 import AsyncMemory

        # `from_config` is a plain classmethod in mem0 2.x, not a coroutine —
        # only `add()` is awaitable.
        memory = AsyncMemory.from_config(
            {
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": settings.llm_model,
                        "openai_base_url": settings.llm_base_url,
                        "api_key": settings.llm_api_key,
                        "temperature": settings.llm_temperature,
                    },
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": settings.embedding_model,
                        "openai_base_url": settings.embedding_base_url,
                        "api_key": settings.embedding_api_key,
                        "embedding_dims": settings.embedding_dim,
                    },
                },
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "collection_name": "mem0_baseline",
                        "embedding_model_dims": settings.embedding_dim,
                        "path": storage_dir,
                    },
                },
            }
        )
        return cls(memory)

    async def extract(self, transcript: str) -> list[ExtractedFact]:
        text = transcript.strip()
        if not text:
            return []

        try:
            response = await self._memory.add(  # type: ignore[attr-defined]
                text, user_id="baseline", infer=True
            )
        except Exception as exc:  # noqa: BLE001 - a baseline failure is a data point
            log.error("extraction.mem0_failed", error=str(exc))
            return []

        return _coerce_mem0(response)

    async def reset(self) -> None:
        """Empty the store so the next document starts from nothing."""
        with contextlib.suppress(Exception):
            await self._memory.reset()  # type: ignore[attr-defined]

    def close(self) -> None:
        close = getattr(self._memory, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def _coerce_mem0(response: object) -> list[ExtractedFact]:
    """Read facts out of Mem0's add() envelope.

    Everything lands as `FACT` with no subject: Mem0 does not produce either, and
    inventing them here would flatter the baseline by crediting it with structure
    it never returned.
    """
    if isinstance(response, dict):
        rows = response.get("results") or []
    elif isinstance(response, list):
        rows = response
    else:
        rows = []

    facts: list[ExtractedFact] = []
    for row in rows:
        if isinstance(row, str):
            content = row.strip()
        elif isinstance(row, dict):
            if str(row.get("event", "ADD")).upper() == "DELETE":
                continue
            content = str(row.get("memory") or row.get("text") or "").strip()
        else:
            continue
        if content:
            facts.append(ExtractedFact(content=content, category=MemoryCategory.FACT))

    log.info("extraction.mem0_completed", count=len(facts))
    return facts
