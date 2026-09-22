"""Full ingest pipeline against in-memory Qdrant with a stubbed LLM.

No Docker, no Ollama, no network — but real Qdrant indexing, real filters and
real cosine scores. This is the test that catches payload/schema drift before
it reaches a running stack.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from qdrant_client import AsyncQdrantClient

from continuum.clients.qdrant import QdrantStore
from continuum.config import Settings
from continuum.models.memory import MemoryCategory, MemoryStatus
from continuum.models.schemas import IngestRequest
from continuum.services.extraction import FactExtractor
from continuum.services.ingest import IngestService
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService

DIM = 128

# Weighting that makes the stub behave like a real embedder: memories about the
# same subject land close together (~0.9 cosine) without being identical.
_SUBJECT_WEIGHT = 1.0
_CONTENT_WEIGHT = 1 / 3


def _unit(seed: str) -> list[float]:
    digest = hashlib.sha512(seed.lower().encode()).digest()
    raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(DIM)]
    norm = sum(v * v for v in raw) ** 0.5 or 1.0
    return [v / norm for v in raw]


def _deterministic_vector(text: str) -> list[float]:
    """Stable pseudo-embedding with realistic geometry.

    A pure hash makes every distinct string orthogonal, which would mean the
    resolver never sees a candidate — the opposite of what real embeddings do.
    So the vector is mostly determined by the `[subject]` prefix that
    `MemoryStore.embedding_text` adds, with a smaller content-specific component:

        identical text            -> cosine 1.00  (duplicate band)
        same subject, new content -> cosine ~0.90 (conflict band, judge runs)
        different subject         -> cosine ~0.00 (no candidate)
    """
    subject = text.split("]")[0][1:] if text.startswith("[") else ""
    content = _unit(text)
    if not subject:
        return content

    subject_vec = _unit(subject)
    combined = [
        _SUBJECT_WEIGHT * sv + _CONTENT_WEIGHT * cv
        for sv, cv in zip(subject_vec, content, strict=True)
    ]
    norm = sum(v * v for v in combined) ** 0.5 or 1.0
    return [v / norm for v in combined]


class StubLLM:
    """Scripted extraction + judge responses, and deterministic fake embeddings.

    The two prompts are told apart by their system text, so one stub can serve
    both roles in the pipeline.
    """

    def __init__(
        self,
        scripted: list[list[dict]],
        judgement: dict | None = None,
    ) -> None:
        self.scripted = scripted
        self.judgement = judgement or {"relation": "independent", "confidence": 0.9}
        self.calls = 0
        self.judge_calls = 0

    async def complete_json(self, *, system: str, user: str, **kwargs):  # noqa: ARG002
        if "decide how they relate" in system:
            self.judge_calls += 1
            return self.judgement
        batch = self.scripted[min(self.calls, len(self.scripted) - 1)]
        self.calls += 1
        return {"memories": batch}

    async def complete_json_with_logprobs(self, *, system: str, user: str, **kwargs):  # noqa: ARG002
        payload = await self.complete_json(system=system, user=user)
        return payload, json.dumps(payload), None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_deterministic_vector(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return _deterministic_vector(text)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        embedding_dim=DIM,
        qdrant_collection="test_memories",
        duplicate_similarity_threshold=0.99,
        conflict_similarity_threshold=0.70,
    )


@pytest.fixture
async def store(settings: Settings) -> QdrantStore:
    store = QdrantStore.__new__(QdrantStore)
    store.settings = settings
    store.collection = settings.qdrant_collection
    store.client = AsyncQdrantClient(":memory:")
    await store.ensure_collection()
    return store


def build_service(store: QdrantStore, llm: StubLLM, settings: Settings) -> IngestService:
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    resolver = ResolutionService(memories, llm, settings)  # type: ignore[arg-type]
    return IngestService(FactExtractor(llm), memories, resolver, llm, settings)  # type: ignore[arg-type]


async def test_ingest_creates_categorised_memories(store, settings):
    llm = StubLLM([[
        {
            "content": "Chose Postgres over Mongo because reporting needs real joins",
            "category": "decision",
            "subject": "atlas",
            "source_excerpt": "we decided to use Postgres",
        },
        {
            "content": "Sara leads the data platform",
            "category": "person",
            "subject": "sara",
        },
    ]])
    service = build_service(store, llm, settings)

    result = await service.ingest(IngestRequest(user_id="mark", text="kickoff notes"))

    assert result.extracted == 2
    assert len(result.created) == 2
    categories = {m.category for m in result.created}
    assert categories == {MemoryCategory.DECISION, MemoryCategory.PERSON}

    stored = result.created[0]
    assert stored.status is MemoryStatus.ACTIVE
    assert stored.source_id == result.source_id      # provenance is wired
    assert stored.confidence == settings.default_confidence


async def test_identical_fact_is_reinforced_not_duplicated(store, settings):
    fact = [{"content": "Acme prefers async written updates", "category": "preference",
             "subject": "acme"}]
    llm = StubLLM([fact, fact])
    service = build_service(store, llm, settings)

    first = await service.ingest(IngestRequest(user_id="mark", text="call notes"))
    second = await service.ingest(IngestRequest(user_id="mark", text="call notes again"))

    assert len(first.created) == 1
    assert second.created == []
    assert second.duplicates_skipped == 1
    assert second.reinforced == [first.created[0].id]

    # Confidence rose rather than a second copy appearing.
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    reinforced = await memories.get(first.created[0].id)
    assert reinforced is not None
    assert reinforced.confidence > settings.default_confidence
    assert reinforced.reinforcement_count == 1
    assert await memories.count(user_id="mark") == 1


async def test_memories_are_scoped_per_user(store, settings):
    fact = [{"content": "Budget capped at 5k/month", "category": "constraint"}]
    llm = StubLLM([fact, fact])
    service = build_service(store, llm, settings)

    await service.ingest(IngestRequest(user_id="mark", text="a"))
    await service.ingest(IngestRequest(user_id="other", text="a"))

    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    assert await memories.count(user_id="mark") == 1
    assert await memories.count(user_id="other") == 1

    mine = await memories.list_all(user_id="mark")
    assert all(m.user_id == "mark" for m in mine)


async def test_empty_extraction_is_a_valid_outcome(store, settings):
    llm = StubLLM([[]])
    service = build_service(store, llm, settings)

    result = await service.ingest(IngestRequest(user_id="mark", text="thanks, sounds good!"))

    assert result.extracted == 0
    assert result.created == []


async def test_search_returns_only_active_memories(store, settings):
    llm = StubLLM([[{"content": "Sara leads the data platform", "category": "person",
                     "subject": "sara"}]])
    service = build_service(store, llm, settings)
    created = (await service.ingest(IngestRequest(user_id="mark", text="notes"))).created[0]

    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    hits = await memories.search(user_id="mark", query="Sara leads the data platform")
    assert [m.id for m, _ in hits] == [created.id]

    # Archive it — it must drop out of retrieval but remain fetchable by id.
    created.status = MemoryStatus.ARCHIVED
    await memories.save(created)

    assert await memories.search(user_id="mark", query="Sara leads the data platform") == []
    assert (await memories.get(created.id)) is not None


# --- Phase 2: verdicts applied to the belief graph --------------------------


async def test_confident_supersede_writes_edges_and_retires_the_old_memory(store, settings):
    llm = StubLLM(
        [
            [{"content": "Atlas uses Postgres", "category": "decision", "subject": "atlas"}],
            [{"content": "Atlas uses Mongo", "category": "decision", "subject": "atlas"}],
        ],
        judgement={"relation": "supersedes", "confidence": 0.95, "reason": "later choice"},
    )
    service = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]

    first = (await service.ingest(IngestRequest(user_id="mark", text="a"))).created[0]
    second = await service.ingest(IngestRequest(user_id="mark", text="b"))

    assert second.superseded == [first.id]
    winner = second.created[0]

    old = await memories.get(first.id)
    assert old is not None
    assert old.status is MemoryStatus.SUPERSEDED
    assert old.superseded_by == winner.id      # edge, not a delete
    assert winner.supersedes == [first.id]      # traversable both ways
    assert await memories.get(first.id) is not None  # nothing was destroyed


async def test_unsure_supersede_flags_both_sides_for_a_human(store, settings):
    llm = StubLLM(
        [
            [{"content": "Atlas uses Postgres", "category": "decision", "subject": "atlas"}],
            [{"content": "Atlas uses Mongo", "category": "decision", "subject": "atlas"}],
        ],
        judgement={"relation": "supersedes", "confidence": 0.4, "reason": "unclear"},
    )
    service = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]

    first = (await service.ingest(IngestRequest(user_id="mark", text="a"))).created[0]
    second = await service.ingest(IngestRequest(user_id="mark", text="b"))

    assert second.superseded == []
    assert second.conflicts_raised == [first.id]
    assert second.resolutions[0].escalated is True

    old = await memories.get(first.id)
    new = await memories.get(second.created[0].id)
    assert old is not None and new is not None
    assert old.status is MemoryStatus.CONTRADICTED
    assert new.status is MemoryStatus.CONTRADICTED
    assert old.conflicts_with == [new.id]
    assert new.conflicts_with == [old.id]


async def test_contradicted_memories_remain_retrievable(store, settings):
    """The agent should surface the disagreement, not go silent on the subject."""
    llm = StubLLM(
        [
            [{"content": "Atlas uses Postgres", "category": "decision", "subject": "atlas"}],
            [{"content": "Atlas uses Mongo", "category": "decision", "subject": "atlas"}],
        ],
        judgement={"relation": "conflict", "confidence": 0.9},
    )
    service = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]

    await service.ingest(IngestRequest(user_id="mark", text="a"))
    await service.ingest(IngestRequest(user_id="mark", text="b"))

    hits = await memories.search(user_id="mark", query="Atlas uses Postgres", limit=10)
    assert any(m.status is MemoryStatus.CONTRADICTED for m, _ in hits)


async def test_superseded_memories_drop_out_of_retrieval(store, settings):
    llm = StubLLM(
        [
            [{"content": "Atlas uses Postgres", "category": "decision", "subject": "atlas"}],
            [{"content": "Atlas uses Mongo", "category": "decision", "subject": "atlas"}],
        ],
        judgement={"relation": "supersedes", "confidence": 0.95},
    )
    service = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]

    first = (await service.ingest(IngestRequest(user_id="mark", text="a"))).created[0]
    await service.ingest(IngestRequest(user_id="mark", text="b"))

    hits = await memories.search(user_id="mark", query="Atlas uses Postgres", limit=10)
    assert first.id not in {m.id for m, _ in hits}


async def test_supersede_winner_gets_elevated_confidence(store, settings):
    llm = StubLLM(
        [
            [{"content": "Atlas uses Postgres", "category": "decision", "subject": "atlas"}],
            [{"content": "Atlas uses Mongo", "category": "decision", "subject": "atlas"}],
        ],
        judgement={"relation": "supersedes", "confidence": 0.95},
    )
    service = build_service(store, llm, settings)

    await service.ingest(IngestRequest(user_id="mark", text="a"))
    winner = (await service.ingest(IngestRequest(user_id="mark", text="b"))).created[0]

    assert winner.confidence == settings.superseded_winner_confidence
    assert winner.confidence > settings.default_confidence


async def test_resolution_records_are_returned_for_audit(store, settings):
    llm = StubLLM(
        [[{"content": "Sara leads the data platform", "category": "person", "subject": "sara"}]]
    )
    service = build_service(store, llm, settings)

    result = await service.ingest(IngestRequest(user_id="mark", text="notes"))

    assert len(result.resolutions) == 1
    record = result.resolutions[0]
    assert record.verdict == "new"
    assert record.memory_id == result.created[0].id


async def test_two_spellings_of_one_subject_are_still_judged_against_each_other(store, settings):
    """`atlas-project` then `atlas`: before canonicalisation the subject filter treated
    them as different entities and the reversal was stored as NEW — never judged."""
    postgres = {"content": "Atlas uses Postgres", "category": "decision",
                "subject": "atlas-project"}
    mongo = {"content": "Atlas uses Mongo", "category": "decision", "subject": "atlas"}
    llm = StubLLM(
        [[postgres], [mongo]],
        judgement={"relation": "supersedes", "confidence": 0.95, "reason": "later choice"},
    )
    service = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]

    first = (await service.ingest(IngestRequest(user_id="mark", text="a"))).created[0]
    second = await service.ingest(IngestRequest(user_id="mark", text="b"))

    assert llm.judge_calls == 1
    assert second.superseded == [first.id]
    assert second.created[0].subject == "atlas-project"   # the graph's existing spelling
    old = await memories.get(first.id)
    assert old is not None and old.status is MemoryStatus.SUPERSEDED


async def test_one_note_cannot_introduce_two_spellings(store, settings):
    llm = StubLLM([[
        {"content": "Acme prefers async updates", "category": "preference", "subject": "acme"},
        {"content": "Acme budget is 5k", "category": "constraint", "subject": "acme-corp"},
    ]])
    service = build_service(store, llm, settings)

    result = await service.ingest(IngestRequest(user_id="mark", text="notes"))

    assert {m.subject for m in result.created} == {"acme"}


async def test_subjects_are_scoped_per_user(store, settings):
    """Another user's slug must never become this user's canonical spelling."""
    llm = StubLLM([
        [{"content": "Atlas uses Postgres", "category": "decision", "subject": "atlas-project"}],
        [{"content": "Atlas uses Mongo", "category": "decision", "subject": "atlas"}],
    ])
    service = build_service(store, llm, settings)

    await service.ingest(IngestRequest(user_id="someone-else", text="a"))
    mine = await service.ingest(IngestRequest(user_id="mark", text="b"))

    assert mine.created[0].subject == "atlas"
