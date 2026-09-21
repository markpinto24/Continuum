"""Qdrant access layer.

We talk to Qdrant directly rather than through a memory framework's vector-store
abstraction. That costs ~80 lines here and buys us payload indexes, filtered
search on status/category, and scroll-based graph queries — all of which Phase 2
and the graph view need, and none of which a unified `add()/search()` facade
exposes cleanly.
"""

from __future__ import annotations

import contextlib

import structlog
from qdrant_client import AsyncQdrantClient, models

from continuum.config import Settings, get_settings

log = structlog.get_logger(__name__)

# Payload fields we filter on often enough to warrant an index.
_INDEXED_KEYWORD_FIELDS = ("user_id", "status", "category", "subject", "source_id")


class QdrantStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.collection = self.settings.qdrant_collection
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
