"""Periodic summaries: what the graph says about one subject, in a few sentences.

A subject with many memories ("atlas": the database, the owner, the deadline,
three decisions) is hard to retrieve whole — the top k for a broad question
fills up with whichever six are worded most like it. A summary gives broad
questions one memory that covers the subject.

A summary is derived, never evidence:
  * it is never a resolution neighbour (it would "confirm" its own sources),
    never decays, and never counts as a belief in an as-of query;
  * it lists the memories it came from (`derived_from`), and the chat prompt
    marks it as a summary;
  * a newer summary supersedes the old one by an edge, like any belief;
  * forgetting any source memory forgets the summaries made from it.

Regenerated only when its sources changed, so a quiet graph costs no LLM calls.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime

from continuum.clients.llm import LLMClient
from continuum.config import Settings
from continuum.core.logger import get_logger
from continuum.models.memory import Memory, MemoryCategory, MemoryStatus
from continuum.services.memory_store import MemoryStore

log = get_logger(__name__)

SUMMARY_SYSTEM = """You condense recorded beliefs about one subject into a short \
summary for later recall.

Rules:
- Use only the statements given. Add nothing, infer nothing.
- Keep names, numbers and dates exactly as written.
- If statements disagree, say that they disagree. Do not pick one.
- Two to four sentences, third person, no preamble.

Reply with JSON: {"summary": "<the summary>"}"""


class SummaryService:
    def __init__(self, memories: MemoryStore, llm: LLMClient, settings: Settings) -> None:
        self.memories = memories
        self.llm = llm
        self.settings = settings

    async def refresh(self, owner: str, *, budget: int | None = None) -> list[Memory]:
        """Summarise every subject in `owner`'s graph that has enough memories and
        changed since its last summary. Returns the summaries written."""
        budget = self.settings.summary_max_per_run if budget is None else budget
        items = await self.memories.list_all(
            user_id=owner, limit=100_000,
            statuses=[MemoryStatus.ACTIVE, MemoryStatus.CONTRADICTED],
        )
        groups: dict[str, list[Memory]] = defaultdict(list)
        current: dict[str, Memory] = {}
        for memory in items:
            if not memory.subject:
                continue
            if memory.kind == "summary":
                current[memory.subject] = memory
            else:
                groups[memory.subject].append(memory)

        written: list[Memory] = []
        for subject, sources in sorted(groups.items()):
            if len(written) >= budget:
                break
            if len(sources) < self.settings.summary_min_memories:
                continue
            previous = current.get(subject)
            if previous and not _changed(previous, sources):
                continue
            summary = await self._summarise(owner, subject, sources)
            if summary is None:
                continue
            await self.memories.add(summary)
            if previous:
                previous.mark_superseded_by(summary.id)
                await self.memories.save(previous)
                summary.mark_supersedes(previous.id)
                await self.memories.save(summary)
            written.append(summary)
        if written:
            log.info("summary.refreshed", written=len(written))
        return written

    async def refresh_all(self) -> int:
        total = 0
        for owner in await self.memories.distinct_user_ids():
            try:
                total += len(await self.refresh(owner))
            except Exception:  # noqa: BLE001 - one graph failing must not stop the rest
                log.exception("summary.refresh_failed")
        return total

    async def _summarise(self, owner: str, subject: str, sources: list[Memory]) -> Memory | None:
        ordered = sorted(sources, key=lambda m: m.created_at)
        statements = "\n".join(
            f"- {m.content} ({m.category}, {m.created_at.date()}"
            + (", disputed" if m.status is MemoryStatus.CONTRADICTED else "")
            + ")"
            for m in ordered
        )
        try:
            payload = await self.llm.complete_json(
                system=SUMMARY_SYSTEM,
                user=f"Subject: {subject}\nStatements:\n{statements}",
            )
            text = str(payload.get("summary", "")).strip() if isinstance(payload, dict) else ""
        except Exception:  # noqa: BLE001 - no summary is better than a broken one
            log.exception("summary.generation_failed")
            return None
        if not text:
            return None
        return Memory(
            user_id=owner,
            content=text[:2000],
            category=MemoryCategory.FACT,
            subject=subject,
            kind="summary",
            derived_from=[m.id for m in ordered],
            derived_fingerprint=fingerprint(ordered),
            confidence=round(sum(m.confidence for m in ordered) / len(ordered), 4),
            source_id="summary",
            created_at=datetime.now(UTC),
        )


def fingerprint(sources: list[Memory]) -> str:
    """What a summary depends on: which memories, and which are disputed.

    Not `updated_at` — every reinforcement touches that, and a summary does not
    need rewriting because a fact was confirmed again.
    """
    parts = sorted(f"{m.id}:{m.status.value}" for m in sources)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _changed(previous: Memory, sources: list[Memory]) -> bool:
    return previous.derived_fingerprint != fingerprint(sources)
