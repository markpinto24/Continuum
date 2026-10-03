"""Domain-level memory operations.

Sits between the API and Qdrant, and is the only place that knows how a
`Memory` maps onto a vector point.
"""

from __future__ import annotations

from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import Settings, get_settings
from continuum.core.logger import get_logger
from continuum.models.memory import (
    RETRIEVABLE_STATUSES,
    Memory,
    MemoryCategory,
    MemoryStatus,
)
from continuum.services import keywords

log = get_logger(__name__)


class MemoryStore:
    def __init__(
        self,
        store: QdrantStore,
        llm: LLMClient,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.settings = settings or get_settings()

    # --- Embedding text ----------------------------------------------------

    @staticmethod
    def embedding_text(memory: Memory) -> str:
        """What we actually embed.

        Prefixing the subject keeps 'Sara owns billing' and 'Raj owns billing'
        far enough apart in vector space that the conflict band stays meaningful.
        """
        return MemoryStore.embedding_text_for(memory.content, memory.subject)

    @staticmethod
    def embedding_text_for(content: str, subject: str | None) -> str:
        """The format itself, usable on a raw payload without building a Memory."""
        return f"[{subject}] {content}" if subject else content

    # --- Writes ------------------------------------------------------------

    async def add(self, memory: Memory) -> Memory:
        vector = await self.llm.embed_one(self.embedding_text(memory))
        await self.store.upsert(
            memory_id=memory.id, vector=vector, payload=memory.to_payload()
        )
        log.info(
            "memory.created",
            memory_id=memory.id,
            category=memory.category,
            subject=memory.subject,
        )
        return memory

    async def add_many(self, memories: list[Memory]) -> list[Memory]:
        if not memories:
            return []
        vectors = await self.llm.embed([self.embedding_text(m) for m in memories])
        await self.store.upsert_many(
            [(m.id, vec, m.to_payload()) for m, vec in zip(memories, vectors, strict=True)]
        )
        log.info("memory.batch_created", count=len(memories))
        return memories

    async def write_with_vector(self, memory: Memory, vector: list[float]) -> Memory:
        """Persist a memory using a vector the caller already computed.

        Ingest embeds its whole batch up front and reuses each vector for both
        the neighbour lookup and the write, so this avoids a second embedding
        call per memory.
        """
        await self.store.upsert(
            memory_id=memory.id, vector=vector, payload=memory.to_payload()
        )
        log.info(
            "memory.created",
            memory_id=memory.id,
            category=memory.category,
            subject=memory.subject,
            status=memory.status,
        )
        return memory

    async def save(self, memory: Memory) -> Memory:
        """Persist a mutated memory without re-embedding (content unchanged)."""
        memory.touch()
        await self.store.update_payload(memory_id=memory.id, payload=memory.to_payload())
        return memory

    async def reinforce(self, memory: Memory, amount: float = 0.05) -> Memory:
        memory.reinforce(amount)
        return await self.save(memory)

    # --- Reads -------------------------------------------------------------

    async def get(self, memory_id: str) -> Memory | None:
        payload = await self.store.get(memory_id)
        return Memory.from_payload(payload) if payload else None

    async def search(
        self,
        *,
        user_id: str | list[str],
        query: str,
        limit: int | None = None,
        statuses: list[MemoryStatus] | None = None,
        categories: list[MemoryCategory] | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[Memory, float]]:
        vector = await self.llm.embed_one(query)
        rows = await self.store.search(
            vector=vector,
            user_id=user_id,
            limit=limit or self.settings.retrieval_top_k,
            statuses=(
                [s.value for s in statuses]
                if statuses
                else [s.value for s in RETRIEVABLE_STATUSES]
            ),
            categories=[c.value for c in categories] if categories else None,
            score_threshold=score_threshold,
        )
        return [(Memory.from_payload(p), score) for p, score in rows]

    async def keyword_search(
        self, *, user_id: str | list[str], query: str, limit: int = 50
    ) -> list[tuple[str, float]]:
        """(memory id, keyword score), best first. No embedding call."""
        found = await self.keyword_memories(user_id=user_id, query=query, limit=limit)
        return [(m.id, score) for m, score in found]

    async def keyword_memories(
        self,
        *,
        user_id: str | list[str],
        query: str,
        limit: int = 50,
        statuses: list[MemoryStatus] | None = None,
    ) -> list[tuple[Memory, float]]:
        """Memories sharing content words with `query`, with their keyword score."""
        words = keywords.terms(query)[:12]
        payloads = await self.store.text_match(
            words=words,
            user_id=user_id,
            statuses=[s.value for s in (statuses or RETRIEVABLE_STATUSES)],
            limit=limit * 4,
        )
        scored = [
            (Memory.from_payload(p), keywords.keyword_score(words, p.get("content", "")))
            for p in payloads
        ]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    async def search_by_vector(
        self,
        *,
        user_id: str | list[str],
        vector: list[float],
        limit: int = 10,
        statuses: list[MemoryStatus] | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[Memory, float]]:
        """Used by ingest, which already has the vector and must not re-embed.

        Never returns summaries: they are derived from the graph, and judging a new
        fact against one would let a summary confirm its own sources.
        """
        rows = await self.store.search(
            vector=vector,
            user_id=user_id,
            limit=limit,
            statuses=(
                [s.value for s in statuses]
                if statuses
                else [s.value for s in RETRIEVABLE_STATUSES]
            ),
            score_threshold=score_threshold,
            exclude_kind="summary",
        )
        return [(Memory.from_payload(p), score) for p, score in rows]

    async def list_all(
        self,
        *,
        user_id: str | list[str],
        limit: int = 200,
        statuses: list[MemoryStatus] | None = None,
        categories: list[MemoryCategory] | None = None,
    ) -> list[Memory]:
        payloads = await self.store.list_memories(
            user_id=user_id,
            limit=limit,
            statuses=[s.value for s in statuses] if statuses else None,
            categories=[c.value for c in categories] if categories else None,
        )
        return [Memory.from_payload(p) for p in payloads]

    async def count(self, *, user_id: str) -> int:
        return await self.store.count(user_id=user_id)

    # --- Graph / conflict queries -----------------------------------------

    async def list_conflicts(
        self, *, user_id: str | list[str], limit: int = 200
    ) -> list[Memory]:
        """Memories awaiting human resolution — the contradiction inbox."""
        return await self.list_all(
            user_id=user_id, limit=limit, statuses=[MemoryStatus.CONTRADICTED]
        )

    async def resolve_ids(self, memory_ids: list[str]) -> dict[str, Memory]:
        payloads = await self.store.get_many(memory_ids)
        memories = [Memory.from_payload(p) for p in payloads]
        return {m.id: m for m in memories}

    async def summaries_of(self, memory_ids: list[str]) -> list[Memory]:
        """Summaries written from any of `memory_ids`."""
        return [Memory.from_payload(p) for p in await self.store.derived_from_any(memory_ids)]

    async def referencing(
        self, memory_ids: str | list[str], *, user_id: str | None = None
    ) -> list[Memory]:
        """Memories holding a conflict edge to any of `memory_ids`."""
        ids = [memory_ids] if isinstance(memory_ids, str) else memory_ids
        return [
            Memory.from_payload(p) for p in await self.store.referencing(ids, user_id=user_id)
        ]

    async def distinct_subjects(self, *, user_id: str) -> set[str]:
        """Subject slugs already in this user's graph — what canonicalisation snaps to."""
        return await self.store.distinct_subjects(user_id=user_id)

    async def distinct_user_ids(self, limit: int = 10_000) -> list[str]:
        """Every user id present in the collection.

        Used by the decay scheduler. Naive by design — see DecayService.sweep_all.
        """
        return await self.store.distinct_user_ids(limit=limit)
