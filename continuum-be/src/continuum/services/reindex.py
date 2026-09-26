"""Re-embed memories when the embedding model changes.

Vector size is fixed when a Qdrant collection is created, so a new embedding
model needs a new collection. Each model gets its own (`collection_for`), and a
marker records which one holds the live memories. On startup, if the configured
model's collection is not the active one, every memory is re-embedded from the
active collection into it, and only then does the marker move.

Properties this has to keep, in order:

  * **Nothing is deleted.** The source collection is left exactly as it was.
    That is commitment 1, and it is also the rollback: switch the model back and
    its collection is still there.
  * **Nothing is lost.** Payloads are copied verbatim — status, edges, provenance,
    confidence, timestamps — and only the vector is recomputed. Memories are not
    even parsed, so a payload the current model class would reject still moves.
  * **Crash-safe.** The marker moves last. A migration killed halfway leaves the
    marker on the source, and the next startup simply runs it again; upserts by
    id make that idempotent.
  * **Switching back works.** A -> B -> A: A's collection exists but is stale.
    The marker points at B, so B is copied into A, overwriting the stale copies
    by id. Every memory ever written went into whichever collection was active,
    so the active one is always the complete set.
  * **No manual step.** It runs in the lifespan hook, before the API reports
    healthy, per the one-command contract.
"""

from __future__ import annotations

from pydantic import BaseModel

from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.core.logger import get_logger
from continuum.services.memory_store import MemoryStore

log = get_logger(__name__)

BATCH = 64


class MigrationReport(BaseModel):
    source: str | None
    target: str
    memories: int = 0

    @property
    def migrated(self) -> bool:
        return self.source is not None and self.source != self.target


class EmbeddingMigration:
    def __init__(self, store: QdrantStore, llm: LLMClient) -> None:
        self.store = store
        self.llm = llm

    async def run(self) -> MigrationReport:
        target = self.store.collection
        source = await self._source(target)

        if source is None or source == target:
            await self.store.write_active_collection(target)
            return MigrationReport(source=source, target=target)

        log.info("reindex.started", source=source, target=target)
        copied = 0
        async for batch in self.store.scroll_points(source, batch=BATCH):
            texts = [
                MemoryStore.embedding_text_for(
                    str(payload.get("content") or ""), payload.get("subject")
                )
                for _, payload in batch
            ]
            vectors = await self.llm.embed(texts)
            await self.store.upsert_into(
                target,
                [(pid, vec, payload) for (pid, payload), vec in zip(batch, vectors, strict=True)],
            )
            copied += len(batch)
            log.info("reindex.progress", memories=copied)

        # Only now: a crash before this line leaves the source active and the
        # whole copy is redone on the next start.
        await self.store.write_active_collection(target)
        log.info("reindex.completed", source=source, target=target, memories=copied)
        return MigrationReport(source=source, target=target, memories=copied)

    async def _source(self, target: str) -> str | None:
        """Where the live memories are now, if anywhere other than `target`."""
        active = await self.store.read_active_collection()
        if active and await self.store.collection_exists(active):
            return active

        # Before per-model collections existed, memories lived directly in the
        # base name. Adopt it once; afterwards the marker takes over.
        legacy = self.store.base_collection
        if (
            legacy != target
            and await self.store.collection_exists(legacy)
            and await self.store.point_count(legacy) > 0
        ):
            return legacy
        return None
