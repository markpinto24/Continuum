"""Embedding migration.

Switching embedding model must never lose, alter, or hide a memory. These run
against real in-memory Qdrant with collections of genuinely different vector
sizes, so a migration that skipped re-embedding would fail on upsert.
"""

from __future__ import annotations

import hashlib

import pytest
from qdrant_client import AsyncQdrantClient, models

from continuum.clients.qdrant import QdrantStore, collection_for
from continuum.config import Settings
from continuum.models.memory import Memory, MemoryCategory, MemoryStatus
from continuum.services.reindex import EmbeddingMigration


class Embedder:
    def __init__(self, settings: Settings, *, fail_on_batch: int | None = None) -> None:
        self.settings = settings
        self.fail_on_batch = fail_on_batch
        self.batches = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches += 1
        if self.fail_on_batch == self.batches:
            raise RuntimeError("embedding endpoint went away")
        dim = self.settings.embedding_dim
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append([(digest[i % len(digest)] / 255.0) + 0.01 for i in range(dim)])
        return out


def settings_for(model: str, dim: int) -> Settings:
    return Settings(_env_file=None, embedding_model=model, embedding_dim=dim)


async def store_for(client: AsyncQdrantClient, model: str, dim: int) -> QdrantStore:
    settings = settings_for(model, dim)
    store = QdrantStore.__new__(QdrantStore)
    store.settings = settings
    store.collection = collection_for(settings)
    store.base_collection = settings.qdrant_collection
    store.marker_collection = f"{settings.qdrant_collection}__meta"
    store.client = client
    await store.ensure_collection()
    return store


def memory(content: str, **kwargs) -> Memory:
    return Memory(user_id="mark", content=content, category=MemoryCategory.DECISION, **kwargs)


async def write(store: QdrantStore, *memories: Memory) -> None:
    embedder = Embedder(store.settings)
    vectors = await embedder.embed([m.content for m in memories])
    await store.upsert_many(
        [(m.id, v, m.to_payload()) for m, v in zip(memories, vectors, strict=True)]
    )


@pytest.fixture
async def client() -> AsyncQdrantClient:
    return AsyncQdrantClient(":memory:")


async def test_legacy_collection_is_adopted_and_left_untouched(client):
    """The one-time move out of the pre-migration `continuum_memories` collection."""
    await client.create_collection(
        "continuum_memories",
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
    )
    old = memory("Atlas uses Postgres", subject="atlas", status=MemoryStatus.SUPERSEDED,
                 superseded_by="m2", confidence=0.42)
    await client.upsert("continuum_memories", points=[
        models.PointStruct(id=old.id, vector=[0.1, 0.2, 0.3], payload=old.to_payload())
    ])

    store = await store_for(client, "bge-m3", 8)
    report = await EmbeddingMigration(store, Embedder(store.settings)).run()  # type: ignore[arg-type]

    assert report.migrated and report.memories == 1
    moved = await client.retrieve(store.collection, ids=[old.id], with_payload=True,
                                  with_vectors=True)
    assert moved[0].payload == old.to_payload()          # status, edges, confidence: verbatim
    assert len(moved[0].vector) == 8                      # actually re-embedded
    assert await store.point_count("continuum_memories") == 1   # source never deleted
    assert await store.read_active_collection() == store.collection


async def test_switching_back_to_an_old_model_recovers_everything_written_since(client):
    """A -> B -> A: A's collection is stale, but must end up complete."""
    a = await store_for(client, "nomic-embed-text", 4)
    first = memory("Atlas uses Postgres")
    await write(a, first)
    await EmbeddingMigration(a, Embedder(a.settings)).run()  # type: ignore[arg-type]

    b = await store_for(client, "bge-m3", 8)
    await EmbeddingMigration(b, Embedder(b.settings)).run()  # type: ignore[arg-type]
    later = memory("Atlas uses Mongo")                        # only ever written under B
    await write(b, later)

    a_again = await store_for(client, "nomic-embed-text", 4)
    report = await EmbeddingMigration(a_again, Embedder(a_again.settings)).run()  # type: ignore[arg-type]

    assert report.source == b.collection
    ids = {p.id for p in (await client.scroll(a_again.collection, limit=10))[0]}
    assert {first.id, later.id} <= {str(i) for i in ids}


async def test_a_migration_that_dies_halfway_is_redone_on_the_next_start(client):
    source = await store_for(client, "nomic-embed-text", 4)
    await write(source, *[memory(f"fact {i}") for i in range(130)])   # > 2 batches
    await EmbeddingMigration(source, Embedder(source.settings)).run()  # type: ignore[arg-type]

    target = await store_for(client, "bge-m3", 8)
    with pytest.raises(RuntimeError):
        await EmbeddingMigration(target, Embedder(target.settings, fail_on_batch=2)).run()  # type: ignore[arg-type]

    # The marker only moves at the end, so the live memories are still the source's.
    assert await target.read_active_collection() == source.collection

    report = await EmbeddingMigration(target, Embedder(target.settings)).run()  # type: ignore[arg-type]
    assert report.memories == 130
    assert await target.point_count(target.collection) == 130


async def test_an_unchanged_model_does_no_work(client):
    store = await store_for(client, "bge-m3", 8)
    await write(store, memory("Atlas uses Postgres"))
    await EmbeddingMigration(store, Embedder(store.settings)).run()  # type: ignore[arg-type]

    embedder = Embedder(store.settings)
    report = await EmbeddingMigration(store, embedder).run()  # type: ignore[arg-type]

    assert not report.migrated
    assert embedder.batches == 0


async def test_a_fresh_install_just_records_itself(client):
    store = await store_for(client, "bge-m3", 8)
    report = await EmbeddingMigration(store, Embedder(store.settings)).run()  # type: ignore[arg-type]

    assert not report.migrated
    assert await store.read_active_collection() == store.collection


def test_each_model_gets_its_own_collection():
    names = {collection_for(settings_for(m, d)) for m, d in
             [("bge-m3", 1024), ("nomic-embed-text", 768), ("qwen3-embedding:0.6b", 1024)]}
    assert len(names) == 3
    assert collection_for(settings_for("qwen3-embedding:0.6b", 1024)) == (
        "continuum_memories__qwen3-embedding-0-6b-1024"
    )
