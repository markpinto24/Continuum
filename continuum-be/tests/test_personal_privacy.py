"""Personal mode's privacy rules (requirements.md §6): health facts stay out of
logs, summaries (tests/test_personal_model.py), and exports unless asked for."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from continuum.models.feedback import ExtractionFeedback, RejectReason
from continuum.models.memory import Escalation, Memory, MemoryCategory
from continuum.models.schemas import IngestRequest
from continuum.services.feedback import build_labels
from tests.test_corrections import engine  # noqa: F401
from tests.test_ingest_pipeline import StubLLM, build_service, settings, store  # noqa: F401
from tests.test_learning import feedback_service

SECRET = "Mark is allergic to penicillin"


async def test_a_health_ingest_never_writes_the_words_to_the_logs(store, settings, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    llm = StubLLM([[{"content": SECRET, "category": "health", "subject": "mark"}]])
    service = build_service(store, llm, settings)
    result = await service.ingest(IngestRequest(user_id="mark:personal", text=SECRET))
    assert result.created[0].category is MemoryCategory.HEALTH
    logged = " ".join(f"{r.getMessage()} {r.args} {r.__dict__.get('msg')}" for r in caplog.records)
    assert "penicillin" not in logged


def _health_label():  # noqa: ANN202
    old = Memory(user_id="mark:personal", content="Mark takes vitamin D daily",
                 category=MemoryCategory.HEALTH, subject="mark")
    new = Memory(user_id="mark:personal", content="Mark stopped taking vitamin D",
                 category=MemoryCategory.HEALTH, subject="mark")
    old.mark_contradicted(new.id)
    new.mark_contradicted(old.id)
    new.escalations.append(Escalation(target_id=old.id, judge_relation="supersedes",
                                      judge_confidence=0.99, gate=0.8, forced=True))
    return build_labels("mark", new, [old], keep_both=False)


async def test_health_decisions_are_left_out_of_exports_unless_asked(engine):  # noqa: F811
    feedback = feedback_service(engine)
    await feedback.store.add_many(_health_label())
    owners = ["mark:personal"]
    assert "vitamin" not in await feedback.export_yaml("mark", owners=owners)
    assert "vitamin" in await feedback.export_yaml("mark", owners=owners, sensitive=True)


async def test_rejected_health_facts_are_left_out_unless_asked(engine):  # noqa: F811
    feedback = feedback_service(engine)
    await feedback.learning.add_rejection(ExtractionFeedback(  # type: ignore[union-attr]
        id="r1", user_id="mark", graph_owner="mark:personal", created_at=datetime.now(UTC),
        memory_id="m1", content=SECRET, category="health",
        source_excerpt="I'm allergic to penicillin?", reason=RejectReason.MISREAD,
    ))
    owners = ["mark:personal"]
    assert "penicillin" not in await feedback.extraction_yaml("mark", owners=owners)
    assert "penicillin" in await feedback.extraction_yaml("mark", owners=owners, sensitive=True)


async def test_decisions_are_counted_per_mode(engine):  # noqa: F811
    feedback = feedback_service(engine)
    await feedback.store.add_many(_health_label())
    assert len(await feedback.labels("mark", owners=["mark:personal"])) == 1
    assert await feedback.labels("mark", owners=["mark", "_shared"]) == []
