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

import structlog

from continuum.clients.llm import LLMClient
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
    """Adapter around Mem0's own extraction, kept for comparison.

    Mem0 returns flat strings, so category/subject/excerpt fall back to defaults.
    Useful as a baseline when evaluating whether the native prompt above is
    actually earning its keep.
    """

    def __init__(self, memory: object) -> None:
        self._memory = memory

    async def extract(self, transcript: str) -> list[ExtractedFact]:
        raise NotImplementedError(
            "Wire this up in Phase 2 evaluation: call mem0's Memory.add() against a "
            "throwaway collection and read back the extracted facts."
        )
