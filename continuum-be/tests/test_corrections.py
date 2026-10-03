"""A person correcting the graph: rejecting a misreading, forgetting a memory,
rating an answer — and keyword retrieval, all against real in-memory Qdrant.

The forget tests check every place Continuum copies memory content, because a
"forget" that leaves the words in a label or a rating has not forgotten.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from continuum.api.deps import get_principal
from continuum.api.routes import feedback as feedback_route
from continuum.api.routes import memories as memories_route
from continuum.clients.authdb import AuthDB
from continuum.clients.labels import LabelStore
from continuum.clients.learning import LearningStore
from continuum.config import Settings, get_settings
from continuum.db.engine import make_engine, migrate
from continuum.evaluation.extraction import score_extraction
from continuum.evaluation.types import ExtractionCase
from continuum.models.auth import Principal, User
from continuum.models.feedback import RejectReason
from continuum.models.memory import (
    SHARED_SPACE,
    ExtractedFact,
    Memory,
    MemoryCategory,
    MemoryStatus,
)
from continuum.services.corrections import CorrectionError, CorrectionService
from continuum.services.feedback import (
    FeedbackService,
    build_labels,
    to_extraction_cases,
)
from continuum.services.memory_store import MemoryStore
from tests.test_ingest_pipeline import StubLLM
from tests.test_ingest_pipeline import settings as settings  # noqa: F401, PLC0414
from tests.test_ingest_pipeline import store as store  # noqa: F401, PLC0414

T0 = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
async def engine():
    db = make_engine(Settings(_env_file=None, database_url="sqlite+aiosqlite://"))
    await migrate(db)
    for user in ("mark", "sara"):
        await AuthDB(db).insert_user(User(id=user, email=f"{user}@example.com", created_at=T0),
                                     "hash", only_if_first=False)
    yield db
    await db.dispose()


@pytest.fixture
def memories(store, settings):  # noqa: F811
    return MemoryStore(store, StubLLM([[]]), settings)  # type: ignore[arg-type]


def make(content: str, *, user: str = "mark", subject: str = "atlas", **extra) -> Memory:  # noqa: ANN003
    return Memory(user_id=user, content=content, category=MemoryCategory.DECISION,
                  subject=subject, source_excerpt=f"note: {content}", **extra)


async def corrections_for(memories: MemoryStore, engine) -> CorrectionService:  # noqa: ANN001
    return CorrectionService(memories, labels=LabelStore(engine), learning=LearningStore(engine))


# --- Reject ----------------------------------------------------------------------------


async def test_rejecting_a_misreading_brings_back_the_belief_it_retired(memories, engine):
    old = make("Atlas uses Postgres")
    wrong = make("Atlas uses Mongo?")  # a question, recorded as a decision
    wrong.mark_supersedes(old.id)
    old.mark_superseded_by(wrong.id)
    await memories.add_many([old, wrong])

    service = await corrections_for(memories, engine)
    rejected, restored = await service.reject(wrong, user_id="mark",
                                              reason=RejectReason.NOT_A_FACT)

    assert restored == [old.id]
    assert (await memories.get(old.id)).status is MemoryStatus.ACTIVE
    stored = await memories.get(wrong.id)
    assert stored.status is MemoryStatus.ARCHIVED and stored.rejected_reason == "not_a_fact"
    [record] = await LearningStore(engine).rejections("mark")
    assert record.content == "Atlas uses Mongo?" and record.reason is RejectReason.NOT_A_FACT

    with pytest.raises(CorrectionError):
        await service.reject(stored, user_id="mark", reason=RejectReason.OTHER)


async def test_rejecting_one_side_of_a_conflict_settles_the_other(memories, engine):
    a, b = make("Atlas uses Postgres"), make("Atlas might use Mongo")
    a.mark_contradicted(b.id)
    b.mark_contradicted(a.id)
    await memories.add_many([a, b])
    await (await corrections_for(memories, engine)).reject(b, user_id="mark",
                                                           reason=RejectReason.MISREAD)
    survivor = await memories.get(a.id)
    assert survivor.status is MemoryStatus.ACTIVE and survivor.conflicts_with == []


def test_rejections_become_forbidden_extraction_cases():
    from continuum.models.feedback import ExtractionFeedback

    item = ExtractionFeedback(id="abcdef123", user_id="mark", created_at=T0, memory_id="m",
                              content="Atlas uses Mongo", category="decision",
                              source_excerpt="should Atlas use Mongo?",
                              reason=RejectReason.NOT_A_FACT)
    [case] = to_extraction_cases([item, item.model_copy(update={"source_excerpt": None})])
    case = ExtractionCase.model_validate(case)
    assert case.forbid == ["Atlas uses Mongo"] and case.expect == []

    hit = score_extraction([(case, [ExtractedFact(content="Atlas uses Mongo",
                                                  category=MemoryCategory.DECISION)])])
    clean = score_extraction([(case, [])])
    assert hit.forbidden_rate == 1.0 and len(hit.forbidden_hits) == 1
    assert clean.forbidden_rate == 0.0


# --- Forget ----------------------------------------------------------------------------


async def test_forgetting_removes_the_words_everywhere_and_keeps_the_record(memories, engine):
    old, new = make("Atlas uses Postgres"), make("Atlas uses the secret vendor Initech")
    old.mark_contradicted(new.id)
    new.mark_contradicted(old.id)
    await memories.add_many([old, new])

    # Copies of the words: a decision label and an answer rating.
    labels = LabelStore(engine)
    await labels.add_many(build_labels("mark", old, [new], keep_both=True))
    learning = LearningStore(engine)
    from continuum.models.feedback import AnswerFeedback

    await learning.add_answer(AnswerFeedback(id="a1", user_id="sara", created_at=T0,
                                             query="who is the vendor?",
                                             answer="Initech [1]", rating=1,
                                             cited_ids=[new.id]))

    forgotten = await (await corrections_for(memories, engine)).forget(new)

    stored = await memories.get(new.id)
    assert stored.content == "[forgotten]" and stored.source_excerpt is None
    assert stored.subject is None and stored.redacted_at is not None
    assert stored.status is MemoryStatus.ARCHIVED and stored.id == forgotten.id
    assert (await memories.get(old.id)).status is MemoryStatus.ACTIVE
    [label] = await labels.list_for_user("mark")
    assert "Initech" not in label.incoming_content + label.existing_content
    [answer] = await learning.answers("sara")
    assert "Initech" not in answer.answer and answer.query == "[forgotten]"
    # Gone from search too.
    hits = await memories.keyword_search(user_id="mark", query="Initech vendor")
    assert hits == []


async def test_a_private_dispute_with_a_shared_memory_is_released(memories, engine):
    """A private memory can point at a shared one that does not point back."""
    shared = make("Atlas uses Postgres", user=SHARED_SPACE)
    mine = make("Atlas uses Mongo")
    mine.team_conflicts_with = [shared.id]
    mine.status = MemoryStatus.CONTRADICTED
    await memories.add_many([shared, mine])
    await (await corrections_for(memories, engine)).forget(shared)
    released = await memories.get(mine.id)
    assert released.team_conflicts_with == [] and released.status is MemoryStatus.ACTIVE


# --- Routes ----------------------------------------------------------------------------


def app_for(memories: MemoryStore, engine, principal: Principal) -> FastAPI:  # noqa: ANN001
    app = FastAPI()
    app.include_router(memories_route.router)
    app.include_router(feedback_route.router)
    app.state.memories = memories
    app.state.corrections = CorrectionService(memories, labels=LabelStore(engine),
                                              learning=LearningStore(engine))
    app.state.feedback = FeedbackService(LabelStore(engine), Settings(_env_file=None),
                                         learning=LearningStore(engine))
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    app.dependency_overrides[get_principal] = lambda: principal
    return app


def person(user_id: str = "mark", *, admin: bool = False) -> Principal:
    return Principal(user_id=user_id, email=f"{user_id}@example.com", is_admin=admin,
                     via="session")


async def call(app: FastAPI, method: str, url: str, **kwargs) -> httpx.Response:  # noqa: ANN003
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


async def test_only_the_author_or_an_admin_corrects_a_shared_memory(memories, engine):
    shared = make("Deploys freeze on Fridays", user=SHARED_SPACE, shared_by="sara")
    await memories.add(shared)
    url = f"/memories/{shared.id}/forget"
    assert (await call(app_for(memories, engine, person()), "POST", url)).status_code == 403
    assert (await call(app_for(memories, engine, person("sara")), "POST", url)).status_code \
        == 200


async def test_someone_elses_private_memory_cannot_be_rejected(memories, engine):
    theirs = make("Sara's private note", user="sara")
    await memories.add(theirs)
    response = await call(app_for(memories, engine, person(admin=True)), "POST",
                          f"/memories/{theirs.id}/reject", json={"reason": "not_a_fact"})
    assert response.status_code == 404


async def test_a_forgotten_memory_cannot_be_reactivated(memories, engine):
    mine = make("Atlas uses Postgres")
    await memories.add(mine)
    app = app_for(memories, engine, person())
    assert (await call(app, "POST", f"/memories/{mine.id}/forget")).status_code == 200
    assert (await call(app, "POST", f"/memories/{mine.id}/reactivate")).status_code == 409


async def test_export_is_your_whole_graph_and_nothing_else(memories, engine):
    mine, archived = make("Atlas uses Postgres"), make("Atlas used MySQL")
    archived.archive()
    await memories.add_many([mine, archived, make("Sara's", user="sara"),
                             make("Team's", user=SHARED_SPACE)])
    response = await call(app_for(memories, engine, person()), "GET", "/memories/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert body["user_id"] == "mark"
    assert {m["id"] for m in body["memories"]} == {mine.id, archived.id}


async def test_rating_an_answer_keeps_only_ids_you_can_see(memories, engine):
    mine, theirs = make("Atlas uses Postgres"), make("Sara's secret", user="sara")
    await memories.add_many([mine, theirs])
    app = app_for(memories, engine, person())
    response = await call(app, "POST", "/feedback/answer", json={
        "query": "what does Atlas use?", "answer": "No idea.", "rating": -1,
        "missing_ids": [mine.id, theirs.id, "not-a-memory"],
    })
    assert response.status_code == 201 and response.json() == {"missing": 1}
    summary = (await call(app, "GET", "/feedback/summary")).json()
    assert summary["answers_down"] == 1 and summary["missing_memories"] == 1

    export = (await call(app, "GET", "/feedback/export?kind=retrieval")).text
    assert "what does Atlas use?" in export and mine.id in export and theirs.id not in export


# --- Keyword retrieval against real Qdrant ----------------------------------------------


async def test_keyword_search_finds_the_words_and_respects_owners(memories):
    await memories.add_many([
        make("Atlas reporting runs nightly on the warehouse"),
        make("Billing moved to Stripe", subject="billing"),
        make("Atlas is Sara's", user="sara"),
    ])
    hits = await memories.keyword_search(user_id=["mark"], query="When does Atlas reporting run?")
    assert len(hits) == 1 and hits[0][1] > 0.5
