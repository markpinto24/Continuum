"""Learning from decisions: labels, the gate evidence, the export.

The interesting properties are the mappings — a decision must become the
action that was right for the resolver — and that the evidence never
recommends a lower gate on thin or contrary data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import yaml
from fastapi import FastAPI

from continuum.clients.labels import LabelStore
from continuum.config import Settings, get_settings
from continuum.db.engine import make_engine, migrate
from continuum.evaluation.corpus import load_resolution_cases
from continuum.models.feedback import Decision, ExpectedAction, ResolutionLabel
from continuum.models.memory import Escalation, Memory, MemoryCategory
from continuum.services.feedback import (
    FeedbackService,
    build_labels,
    gate_evidence,
    to_corpus_cases,
)
from tests.auth_helpers import sign_in_as

T0 = datetime(2026, 3, 1, tzinfo=UTC)


def pair(
    *, relation: str | None = "supersedes", p: float | None = 0.7, forced: bool = False
) -> tuple[Memory, Memory]:
    old = Memory(user_id="mark", content="Atlas uses Postgres", category=MemoryCategory.DECISION,
                 subject="atlas", created_at=T0)
    new = Memory(user_id="mark", content="Atlas uses Mongo", category=MemoryCategory.DECISION,
                 subject="atlas", created_at=T0 + timedelta(days=45))
    old.mark_contradicted(new.id)
    new.mark_contradicted(old.id)
    if relation is not None:
        new.escalations.append(Escalation(target_id=old.id, judge_relation=relation,
                                          judge_confidence=p, similarity=0.81, gate=0.8,
                                          forced=forced))
    return old, new


# --- Decisions become labels ----------------------------------------------------


def test_newer_holds_means_the_resolver_should_have_retired():
    old, new = pair()
    [label] = build_labels("mark", new, [old], keep_both=False)
    assert label.decision is Decision.NEWER_HOLDS
    assert label.expected_action is ExpectedAction.RETIRE
    # Oriented as the corpus is: existing = older, incoming = newer.
    assert label.existing_content == "Atlas uses Postgres"
    assert label.incoming_content == "Atlas uses Mongo"
    assert label.existing_age_days == 45
    assert label.judge_relation == "supersedes" and label.judge_confidence == 0.7


def test_older_holds_means_asking_was_right():
    """Picking the older side: auto-retiring would have destroyed a true belief."""
    old, new = pair()
    [label] = build_labels("mark", old, [new], keep_both=False)
    assert label.decision is Decision.OLDER_HOLDS
    assert label.expected_action is ExpectedAction.ESCALATE
    assert label.existing_content == "Atlas uses Postgres"  # orientation ignores the winner


def test_keep_both_means_store():
    old, new = pair()
    [label] = build_labels("mark", old, [new], keep_both=True)
    assert label.decision is Decision.BOTH_HOLD
    assert label.expected_action is ExpectedAction.STORE


def test_a_conflict_from_before_recording_is_still_a_label():
    old, new = pair(relation=None)
    [label] = build_labels("mark", new, [old], keep_both=False)
    assert label.judge_relation is None and label.judge_confidence is None


def test_a_pair_that_was_never_disputed_is_not_labelled():
    old, new = pair()
    stranger = Memory(user_id="mark", content="Vega uses Redis", category=MemoryCategory.DECISION)
    assert [x.incoming_content for x in build_labels("mark", new, [old, stranger],
                                                      keep_both=False)] == ["Atlas uses Mongo"]


# --- The gate evidence ------------------------------------------------------------


def labels(*specs: tuple[float, Decision], forced: bool = False) -> list[ResolutionLabel]:
    out = []
    for p, decision in specs:
        old, new = pair(p=p, forced=forced)
        winner, loser = (new, old) if decision is Decision.NEWER_HOLDS else (old, new)
        out += build_labels("mark", winner, [loser], keep_both=decision is Decision.BOTH_HOLD)
    return out


def test_no_evidence_means_keep_the_gate():
    report = gate_evidence([], gate=0.8, min_evidence=15)
    assert report.below_gate_total == 0
    assert report.recommendation.startswith("Keep 0.80")


def test_one_refutation_below_the_gate_rules_out_lowering_it():
    report = gate_evidence(
        labels((0.6, Decision.NEWER_HOLDS), (0.65, Decision.OLDER_HOLDS)),
        gate=0.8, min_evidence=15,
    )
    assert report.below_gate_refuted == 1
    assert report.error_upper_bound is None
    assert "would have retired a true belief" in report.recommendation


def test_a_few_confirmations_are_not_enough():
    report = gate_evidence(labels(*[(0.7, Decision.NEWER_HOLDS)] * 3), gate=0.8, min_evidence=15)
    assert report.error_upper_bound == 1.0  # 3/3: zero failures in 3 proves nothing
    assert report.recommendation.startswith("Keep 0.80 for now")


def test_enough_clean_confirmations_suggest_considering_a_lower_gate():
    report = gate_evidence(labels(*[(0.62, Decision.NEWER_HOLDS)] * 15), gate=0.8,
                           min_evidence=15)
    assert report.error_upper_bound == 0.2
    assert "supports considering a lower gate" in report.recommendation
    assert "product decision" in report.recommendation
    band = next(b for b in report.bands if b.low == 0.6)
    assert band.total == 15 and band.confirmed == 15


def test_policy_escalations_are_not_evidence_about_the_gate():
    report = gate_evidence(
        labels((0.99, Decision.BOTH_HOLD), (0.98, Decision.NEWER_HOLDS), forced=True),
        gate=0.8, min_evidence=15,
    )
    assert report.below_gate_total == 0
    assert report.role_rule_total == 2 and report.role_rule_shared == 1


def test_over_escalated_conflicts_are_counted():
    old, new = pair(relation="conflict", p=0.9)
    report = gate_evidence(build_labels("mark", old, [new], keep_both=True), gate=0.8,
                           min_evidence=15)
    assert report.conflicts_total == 1 and report.conflicts_both_hold == 1


# --- Export, round-tripped through the corpus loader ------------------------------


def test_exported_cases_load_as_corpus_cases(tmp_path):
    exported = to_corpus_cases(
        labels((0.7, Decision.NEWER_HOLDS), (0.6, Decision.OLDER_HOLDS), (0.9, Decision.BOTH_HOLD))
    )
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(exported))

    cases = load_resolution_cases(path)
    assert [c.expect.value for c in cases] == ["retire", "escalate", "store"]
    assert all("from-use" in c.tags and c.why.startswith("Settled by a person") for c in cases)
    assert cases[0].existing.age_days == 45


# --- Storage and the inbox endpoint ------------------------------------------------


@pytest.fixture
async def feedback():
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite://")
    engine = make_engine(settings)
    await migrate(engine)
    # Labels reference a user; create the one the tests use.
    from continuum.clients.authdb import AuthDB
    from continuum.models.auth import User

    await AuthDB(engine).insert_user(User(id="mark", email="m@example.com", created_at=T0),
                                     "hash", only_if_first=False)
    yield FeedbackService(LabelStore(engine), settings)
    await engine.dispose()


async def test_labels_round_trip_through_the_database(feedback):
    old, new = pair()
    await feedback.record_resolution("mark", new, [old], keep_both=False)

    [stored] = await feedback.labels("mark")
    assert stored.decision is Decision.NEWER_HOLDS
    assert stored.created_at.tzinfo is not None
    assert (await feedback.evidence("mark")).below_gate_total == 1
    assert "use-" in await feedback.export_yaml("mark")


class FakeMemories:
    def __init__(self, *memories: Memory) -> None:
        self.items = {m.id: m for m in memories}
        self.saved: list[str] = []

    async def get(self, memory_id):  # noqa: ANN001, ANN201
        return self.items.get(memory_id)

    async def resolve_ids(self, ids):  # noqa: ANN001, ANN201
        return {i: self.items[i] for i in ids if i in self.items}

    async def save(self, memory):  # noqa: ANN001, ANN201
        self.saved.append(memory.id)
        return memory


async def test_resolving_in_the_inbox_records_the_label(feedback):
    from continuum.api.routes import conflicts

    old, new = pair()
    app = FastAPI()
    app.include_router(conflicts.router)
    app.state.memories = FakeMemories(old, new)
    app.state.feedback = feedback
    app.dependency_overrides[get_settings] = lambda: feedback.settings
    sign_in_as(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/conflicts/resolve", json={"winner_id": new.id, "loser_ids": [old.id]}
        )
    assert response.status_code == 200

    [label] = await feedback.labels("mark")
    assert label.decision is Decision.NEWER_HOLDS
    # Taken from before the decision: the winner's reinforcement is not in it.
    assert label.incoming_memory_id == new.id
    assert new.confidence > 0.70 and label.existing_confidence == 0.70


async def test_a_failed_recording_never_fails_the_decision():
    class BrokenStore:
        async def add_many(self, _labels):  # noqa: ANN001, ANN202
            raise RuntimeError("database down")

    service = FeedbackService(BrokenStore(), Settings(_env_file=None))  # type: ignore[arg-type]
    old, new = pair()
    assert await service.record_resolution("mark", new, [old], keep_both=False) == []
