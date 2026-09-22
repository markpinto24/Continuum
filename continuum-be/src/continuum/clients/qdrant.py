"""Qdrant access layer.

We talk to Qdrant directly rather than through a memory framework's vector-store
abstraction. That costs ~80 lines here and buys us payload indexes, filtered
search on status/category, and scroll-based graph queries — all of which Phase 2
and the graph view need, and none of which a unified `add()/search()` facade
exposes cleanly.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import AsyncIterator

import structlog
from qdrant_client import AsyncQdrantClient, models

from continuum.config import Settings, get_settings

log = structlog.get_logger(__name__)

# Payload fields we filter on often enough to warrant an index.
_INDEXED_KEYWORD_FIELDS = ("user_id", "status", "category", "subject", "source_id")


# The marker naming which physical collection holds the live memories.
_MARKER_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "continuum/active-collection"))


def collection_for(settings: Settings) -> str:
    """The physical collection for the configured embedding model.

    One collection per model, because vector size is fixed when a collection is
    created: a 768-dim nomic collection cannot hold 1024-dim bge-m3 vectors.
    Encoding the model in the name means switching models never writes into a
    collection built for another one. `reindex.EmbeddingMigration` copies the
    memories across on startup.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", settings.embedding_model.lower()).strip("-")
    return f"{settings.qdrant_collection}__{slug}-{settings.embedding_dim}"


class QdrantStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.collection = collection_for(self.settings)
        self.base_collection = self.settings.qdrant_collection
        self.marker_collection = f"{self.settings.qdrant_collection}__meta"
        self.client = AsyncQdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key,
            # The pinned server image may trail the client by a few minor
            # versions; the wire format we use is stable across them.
            check_compatibility=False,
        )

    # --- Bootstrap ---------------------------------------------------------

    async def ensure_collection(self) -> None:
        existing = await self.client.get_collections()
        names = {c.name for c in existing.collections}

        if self.collection not in names:
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=self.settings.embedding_dim,
                    distance=models.Distance.COSINE,
                ),
            )
            log.info("qdrant.collection_created", collection=self.collection)

        # Re-creating an existing index is a no-op error; suppressing keeps
        # ensure_collection() idempotent across restarts.
        for field in _INDEXED_KEYWORD_FIELDS:
            with contextlib.suppress(Exception):
                await self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )

        with contextlib.suppress(Exception):
            await self.client.create_payload_index(
                collection_name=self.collection,
                field_name="confidence",
                field_schema=models.PayloadSchemaType.FLOAT,
            )

    # --- Writes ------------------------------------------------------------

    async def upsert(self, *, memory_id: str, vector: list[float], payload: dict) -> None:
        await self.client.upsert(
            collection_name=self.collection,
            points=[models.PointStruct(id=memory_id, vector=vector, payload=payload)],
        )

    async def upsert_many(self, points: list[tuple[str, list[float], dict]]) -> None:
        if not points:
            return
        await self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(id=pid, vector=vec, payload=payload)
                for pid, vec, payload in points
            ],
        )

    async def update_payload(self, *, memory_id: str, payload: dict) -> None:
        """Overwrite the payload of an existing point without re-embedding."""
        await self.client.set_payload(
            collection_name=self.collection,
            payload=payload,
            points=[memory_id],
        )

    async def delete(self, memory_id: str) -> None:
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=[memory_id]),
        )

    # --- Reads -------------------------------------------------------------

    async def search(
        self,
        *,
        vector: list[float],
        user_id: str,
        limit: int = 8,
        statuses: list[str] | None = None,
        categories: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[dict, float]]:
        response = await self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=build_filter(
                user_id=user_id, statuses=statuses, categories=categories
            ),
            with_payload=True,
        )
        return [(p.payload or {}, p.score) for p in response.points]

    async def get(self, memory_id: str) -> dict | None:
        points = await self.client.retrieve(
            collection_name=self.collection, ids=[memory_id], with_payload=True
        )
        return (points[0].payload or {}) if points else None

    async def get_many(self, memory_ids: list[str]) -> list[dict]:
        if not memory_ids:
            return []
        points = await self.client.retrieve(
            collection_name=self.collection, ids=memory_ids, with_payload=True
        )
        return [p.payload or {} for p in points]

    async def list_memories(
        self,
        *,
        user_id: str,
        limit: int = 200,
        statuses: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> list[dict]:
        payloads: list[dict] = []
        offset = None
        while len(payloads) < limit:
            batch, offset = await self.client.scroll(
                collection_name=self.collection,
                scroll_filter=build_filter(
                    user_id=user_id, statuses=statuses, categories=categories
                ),
                limit=min(256, limit - len(payloads)),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            payloads.extend(p.payload or {} for p in batch)
            if offset is None:
                break
        return payloads

    async def count(self, *, user_id: str) -> int:
        result = await self.client.count(
            collection_name=self.collection,
            count_filter=build_filter(user_id=user_id),
            exact=True,
        )
        return result.count

    async def distinct_user_ids(self, limit: int = 10_000) -> list[str]:
        """Scroll the collection for every distinct user_id.

        Fine for a single-tenant or small multi-user deployment. Past that, keep
        the user list in a real table and drive the decay job from there.
        """
        seen: set[str] = set()
        offset = None
        scanned = 0
        while scanned < limit:
            batch, offset = await self.client.scroll(
                collection_name=self.collection,
                limit=min(512, limit - scanned),
                offset=offset,
                with_payload=["user_id"],
                with_vectors=False,
            )
            for point in batch:
                uid = (point.payload or {}).get("user_id")
                if uid:
                    seen.add(str(uid))
            scanned += len(batch)
            if offset is None or not batch:
                break
        return sorted(seen)

    async def distinct_subjects(self, *, user_id: str, limit: int = 10_000) -> set[str]:
        """Every subject slug this user's memories use, whatever their status.

        Scroll-based, like distinct_user_ids. Superseded and archived memories
        count: a subject does not stop existing because one belief about it did.
        """
        seen: set[str] = set()
        offset = None
        scanned = 0
        while scanned < limit:
            batch, offset = await self.client.scroll(
                collection_name=self.collection,
                scroll_filter=build_filter(user_id=user_id),
                limit=min(512, limit - scanned),
                offset=offset,
                with_payload=["subject"],
                with_vectors=False,
            )
            for point in batch:
                subject = (point.payload or {}).get("subject")
                if subject:
                    seen.add(str(subject))
            scanned += len(batch)
            if offset is None or not batch:
                break
        return seen

    # --- Migration support -------------------------------------------------
    #
    # Thin, collection-explicit operations for reindex.EmbeddingMigration. They
    # take the collection name as an argument because a migration reads one
    # collection and writes another; everything above is bound to the live one.

    async def collection_exists(self, name: str) -> bool:
        existing = await self.client.get_collections()
        return name in {c.name for c in existing.collections}

    async def point_count(self, name: str) -> int:
        result = await self.client.count(collection_name=name, exact=True)
        return result.count

    async def scroll_points(
        self, name: str, *, batch: int = 64
    ) -> AsyncIterator[list[tuple[str, dict]]]:
        """Every point in `name` as (id, payload), a batch at a time."""
        offset = None
        while True:
            points, offset = await self.client.scroll(
                collection_name=name,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if points:
                yield [(str(p.id), p.payload or {}) for p in points]
            if offset is None or not points:
                return

    async def upsert_into(self, name: str, points: list[tuple[str, list[float], dict]]) -> None:
        if not points:
            return
        await self.client.upsert(
            collection_name=name,
            points=[models.PointStruct(id=pid, vector=v, payload=pl) for pid, v, pl in points],
        )

    async def read_active_collection(self) -> str | None:
        """Which physical collection holds the live memories, if recorded."""
        if not await self.collection_exists(self.marker_collection):
            return None
        points = await self.client.retrieve(
            collection_name=self.marker_collection, ids=[_MARKER_ID], with_payload=True
        )
        if not points:
            return None
        return (points[0].payload or {}).get("active")

    async def write_active_collection(self, name: str) -> None:
        # A one-point collection with a dummy vector: Qdrant has no place to
        # keep collection-level metadata, and this needs no extra service.
        if not await self.collection_exists(self.marker_collection):
            await self.client.create_collection(
                collection_name=self.marker_collection,
                vectors_config=models.VectorParams(size=1, distance=models.Distance.DOT),
            )
        await self.client.upsert(
            collection_name=self.marker_collection,
            points=[models.PointStruct(id=_MARKER_ID, vector=[1.0], payload={"active": name})],
        )

    # --- Health ------------------------------------------------------------

    async def ping(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("qdrant.ping_failed", error=str(exc))
            return False

    async def aclose(self) -> None:
        await self.client.close()


def build_filter(
    *,
    user_id: str | None = None,
    statuses: list[str] | None = None,
    categories: list[str] | None = None,
) -> models.Filter | None:
    conditions: list[models.Condition] = []

    if user_id:
        conditions.append(
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))
        )
    if statuses:
        conditions.append(
            models.FieldCondition(key="status", match=models.MatchAny(any=list(statuses)))
        )
    if categories:
        conditions.append(
            models.FieldCondition(key="category", match=models.MatchAny(any=list(categories)))
        )

    return models.Filter(must=conditions) if conditions else None
