"""Learning from decisions, beyond the labels themselves.

- the calibration curve (judge confidence -> how often people agreed), and the
  gate using it only when switched on and only once it rests on enough decisions;
- "both are true" rules: suggested from repeated decisions, applied only once a
  person approves them, and never by an API key or a non-admin for the team;
- team-wide gate evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient

from continuum.api.deps import get_principal
from continuum.api.routes import feedback as feedback_route
from continuum.clients.authdb import AuthDB
from continuum.clients.labels import LabelStore
from continuum.clients.learning import LearningStore
from continuum.clients.qdrant import QdrantStore
from continuum.config import Settings, get_settings
from continuum.db.engine import make_engine, migrate
from continuum.models.auth import Principal, User
from continuum.models.feedback import Decision
from continuum.models.memory import SHARED_SPACE, Escalation, Memory, MemoryCategory
from continuum.models.schemas import IngestRequest
from continuum.services.extraction import FactExtractor
from continuum.services.feedback import (
    Calibration,
    FeedbackService,
    build_labels,
    calibration_points,
    rule_suggestions,
)
from continuum.services.ingest import IngestService
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService, Verdict
from tests.auth_helpers import sign_in_as
from tests.test_ingest_pipeline import StubLLM
from tests.test_resolution import ScriptedJudge, fact, memory

T0 = datetime(2026, 3, 1, tzinfo=UTC)


def label(p: float, decision: Decision, *, owner: str = "mark", subject: str = "atlas"):
    old = Memory(user_id=owner, content="Atlas uses Postgres", category=MemoryCategory.DECISION,
                 subject=subject, created_at=T0)
    new = Memory(user_id=owner, content="Atlas uses Mongo", category=MemoryCategory.DECISION,
                 subject=subject, created_at=T0 + timedelta(days=3))
    old.mark_contradicted(new.id)
    new.mark_contradicted(old.id)
    new.escalations.append(Escalation(target_id=old.id, judge_relation="supersedes",
                                      judge_confidence=p, similarity=0.8, gate=0.8))
    winner, loser = (new, old) if decision is Decision.NEWER_HOLDS else (old, new)
    [made] = build_labels("mark", winner, [loser], keep_both=decision is Decision.BOTH_HOLD)
    return made


# --- The calibration curve -----------------------------------------------------------


def test_the_curve_never_falls_as_confidence_rises():
    labels = (
        [label(0.55, Decision.NEWER_HOLDS)] * 6  # often right at 0.55...
        + [label(0.75, Decision.OLDER_HOLDS)] * 6  # ...wrong at 0.75
    )
    values = [point.calibrated for point in calibration_points(labels)]
    assert values == sorted(values)


def test_one_decision_is_not_certainty():
    [point] = [p for p in calibration_points([label(0.65, Decision.NEWER_HOLDS)]) if p.total]
    assert point.observed == 1.0
    assert point.calibrated < 0.8  # (1 + 1) / (1 + 2)


def test_the_curve_is_unused_until_it_rests_on_enough_decisions():
    calibration = Calibration()
    calibration.update([label(0.65, Decision.NEWER_HOLDS)] * 4, min_labels=5)
    assert calibration.map(0.65) is None
    calibration.update([label(0.65, Decision.NEWER_HOLDS)] * 5, min_labels=5)
    assert calibration.map(0.65) == pytest.approx(6 / 7, abs=1e-3)


def test_policy_escalations_do_not_calibrate_the_judge():
    calibration = Calibration()
    forced = label(0.95, Decision.OLDER_HOLDS)
    forced.forced = True
    calibration.update([forced] * 10, min_labels=1)
    assert calibration.labels == 0


# --- The gate with calibration --------------------------------------------------------


def gate_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, duplicate_similarity_threshold=0.94,
                    conflict_similarity_threshold=0.78, auto_supersede_confidence=0.80,
                    **overrides)


async def test_calibration_is_ignored_unless_switched_on():
    resolver = ResolutionService(None, ScriptedJudge("supersedes", 0.6), gate_settings(),  # type: ignore[arg-type]
                                 calibrate=lambda _p: 0.99)
    result = await resolver.resolve(fact(), [(memory(), 0.85)])
    assert result.verdict is Verdict.CONFLICT


async def test_a_judge_people_have_found_reliable_clears_the_gate():
    resolver = ResolutionService(None, ScriptedJudge("supersedes", 0.6),  # type: ignore[arg-type]
                                 gate_settings(calibrated_gate=True), calibrate=lambda _p: 0.9)
    result = await resolver.resolve(fact(), [(memory(), 0.85)])
    assert result.verdict is Verdict.SUPERSEDES
    # The raw probability is what gets recorded — the curve is built from it.
    assert result.judge_confidence == pytest.approx(0.6)


async def test_a_judge_people_keep_overruling_is_escalated_even_when_sure():
    resolver = ResolutionService(None, ScriptedJudge("supersedes", 0.95),  # type: ignore[arg-type]
                                 gate_settings(calibrated_gate=True), calibrate=lambda _p: 0.4)
    result = await resolver.resolve(fact(), [(memory(), 0.85)])
    assert result.verdict is Verdict.CONFLICT
    assert result.escalated_from is Verdict.SUPERSEDES


async def test_an_unusable_curve_falls_back_to_the_raw_judge():
    resolver = ResolutionService(None, ScriptedJudge("supersedes", 0.9),  # type: ignore[arg-type]
                                 gate_settings(calibrated_gate=True), calibrate=lambda _p: None)
    assert (await resolver.resolve(fact(), [(memory(), 0.85)])).verdict is Verdict.SUPERSEDES


# --- Rules ------------------------------------------------------------------------------


async def test_an_approved_rule_stores_a_conflict_side_by_side():
    resolver = ResolutionService(None, ScriptedJudge("conflict", 0.9), gate_settings())  # type: ignore[arg-type]
    result = await resolver.resolve(fact(), [(memory(), 0.85)], compatible_subjects={"acme"})
    assert result.verdict is Verdict.INDEPENDENT
    assert "rule" in result.reason


async def test_a_rule_for_another_subject_changes_nothing():
    resolver = ResolutionService(None, ScriptedJudge("conflict", 0.9), gate_settings())  # type: ignore[arg-type]
    result = await resolver.resolve(fact(), [(memory(), 0.85)], compatible_subjects={"atlas"})
    assert result.verdict is Verdict.CONFLICT


async def test_a_rule_never_stops_a_confident_supersede():
    """A rule only answers what would have been asked; it does not keep a belief
    the judge is sure was replaced."""
    resolver = ResolutionService(None, ScriptedJudge("supersedes", 0.95), gate_settings())  # type: ignore[arg-type]
    result = await resolver.resolve(fact(), [(memory(), 0.85)], compatible_subjects={"acme"})
    assert result.verdict is Verdict.SUPERSEDES


def test_rules_are_suggested_only_from_consistent_both_hold_decisions():
    both = [label(0.6, Decision.BOTH_HOLD, subject="rotation")] * 2
    mixed = [label(0.6, Decision.BOTH_HOLD, subject="atlas"),
             label(0.6, Decision.BOTH_HOLD, subject="atlas"),
             label(0.6, Decision.NEWER_HOLDS, subject="atlas")]
    suggestions = rule_suggestions(both + mixed, owners=["mark"], existing=set(), min_decisions=2)
    assert [(s.subject, s.both_hold) for s in suggestions] == [("rotation", 2)]
    assert rule_suggestions(both, owners=["mark"], existing={("mark", "rotation")},
                            min_decisions=2) == []
    assert rule_suggestions(both, owners=["sara"], existing=set(), min_decisions=2) == []


# --- Storage, services and routes -----------------------------------------------------


@pytest.fixture
async def engine():
    settings = Settings(_env_file=None, database_url="sqlite+aiosqlite://")
    engine = make_engine(settings)
    await migrate(engine)
    auth = AuthDB(engine)
    for user in ("mark", "sara"):
        await auth.insert_user(User(id=user, email=f"{user}@example.com", created_at=T0),
                               "hash", only_if_first=False)
    yield engine
    await engine.dispose()


def feedback_service(engine, **overrides: object) -> FeedbackService:
    settings = Settings(_env_file=None, rule_suggestion_min_decisions=2, **overrides)
    return FeedbackService(LabelStore(engine), settings, learning=LearningStore(engine))


async def test_rules_round_trip_and_revoke(engine):
    feedback = feedback_service(engine)
    rule = await feedback.approve_rule(owner="mark", subject="rotation", approved_by="mark")
    again = await feedback.approve_rule(owner="mark", subject="rotation", approved_by="mark")
    assert again.id == rule.id
    assert await feedback.compatible_subjects("mark") == {"rotation"}
    assert await feedback.compatible_subjects("sara") == set()

    with pytest.raises(LookupError):
        await feedback.revoke_rule(rule.id, owners=["sara"])
    await feedback.revoke_rule(rule.id, owners=["mark"])
    assert await feedback.compatible_subjects("mark") == set()
    # Revoked, not deleted.
    [kept] = (await feedback.learning.rules(["mark"], active_only=False))  # type: ignore[union-attr]
    assert kept.revoked_at is not None


async def test_team_evidence_pools_everyones_decisions(engine):
    feedback = feedback_service(engine)
    await feedback.store.add_many([label(0.6, Decision.NEWER_HOLDS)])
    sara = label(0.6, Decision.NEWER_HOLDS, owner="sara")
    sara.user_id = "sara"
    await feedback.store.add_many([sara])
    assert (await feedback.evidence("mark")).below_gate_total == 1
    assert (await feedback.evidence("mark", team=True)).below_gate_total == 2


async def test_decisions_refresh_the_calibration(engine):
    feedback = feedback_service(engine, calibration_min_labels=1)
    old, new = (Memory(user_id="mark", content=c, category=MemoryCategory.DECISION,
                       subject="atlas", created_at=T0 + timedelta(days=d))
                for c, d in (("Atlas uses Postgres", 0), ("Atlas uses Mongo", 3)))
    old.mark_contradicted(new.id)
    new.mark_contradicted(old.id)
    new.escalations.append(Escalation(target_id=old.id, judge_relation="supersedes",
                                      judge_confidence=0.7, similarity=0.8, gate=0.8))
    assert feedback.calibration.map(0.7) is None
    await feedback.record_resolution("mark", new, [old], keep_both=False)
    assert feedback.calibration.map(0.7) is not None


def app_for(engine, principal: Principal, **overrides: object) -> FastAPI:
    app = FastAPI()
    app.include_router(feedback_route.router)
    app.state.feedback = feedback_service(engine, **overrides)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, **overrides)
    app.dependency_overrides[get_principal] = lambda: principal
    return app


def person(user_id: str = "mark", *, admin: bool = False, via: str = "session") -> Principal:
    return Principal(user_id=user_id, email=f"{user_id}@example.com", is_admin=admin, via=via)  # type: ignore[arg-type]


async def call(app: FastAPI, method: str, url: str, **kwargs) -> httpx.Response:  # noqa: ANN003
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


async def test_a_person_approves_a_rule_for_their_own_graph(engine):
    app = app_for(engine, person())
    response = await call(app, "POST", "/feedback/rules", json={"subject": "rotation"})
    assert response.status_code == 201 and response.json()["owner"] == "mark"
    listed = (await call(app, "GET", "/feedback/rules")).json()
    assert [r["subject"] for r in listed["rules"]] == ["rotation"]


async def test_an_api_key_cannot_create_rules(engine):
    app = app_for(engine, person(via="api_key"))
    response = await call(app, "POST", "/feedback/rules", json={"subject": "rotation"})
    assert response.status_code == 403


async def test_only_an_admin_adds_a_team_rule(engine):
    body = {"subject": "on-call", "scope": "team"}
    assert (await call(app_for(engine, person()), "POST", "/feedback/rules",
                       json=body)).status_code == 403
    made = await call(app_for(engine, person(admin=True)), "POST", "/feedback/rules", json=body)
    assert made.status_code == 201 and made.json()["owner"] == SHARED_SPACE
    # Everyone sees it; a non-admin cannot revoke it.
    member = app_for(engine, person("sara"))
    assert [r["owner"] for r in (await call(member, "GET", "/feedback/rules")).json()["rules"]] \
        == [SHARED_SPACE]
    revoke = await call(member, "DELETE", f"/feedback/rules/{made.json()['id']}")
    assert revoke.status_code == 404


async def test_someone_elses_rule_is_not_found(engine):
    made = await call(app_for(engine, person()), "POST", "/feedback/rules",
                      json={"subject": "rotation"})
    other = app_for(engine, person("sara", admin=True))
    assert (await call(other, "DELETE", f"/feedback/rules/{made.json()['id']}")).status_code \
        == 404


async def test_a_body_naming_a_user_is_refused(engine):
    app = app_for(engine, person())
    response = await call(app, "POST", "/feedback/rules",
                          json={"subject": "rotation", "user_id": "sara"})
    assert response.status_code == 422


async def test_team_scope_and_calibration_routes(engine):
    app = app_for(engine, person())
    sign_in_as(app)
    assert (await call(app, "GET", "/feedback/evidence?scope=team")).status_code == 200
    report = (await call(app, "GET", "/feedback/calibration")).json()
    assert report["in_use"] is False and len(report["points"]) > 0


# --- Through ingest ----------------------------------------------------------------------


async def test_ingest_applies_the_graphs_approved_rules():
    settings = Settings(embedding_dim=64, qdrant_collection="t",
                        duplicate_similarity_threshold=0.99, conflict_similarity_threshold=0.70)
    store = QdrantStore.__new__(QdrantStore)
    store.settings, store.collection = settings, "t"
    store.client = AsyncQdrantClient(":memory:")
    llm = StubLLM(
        [[{"content": f"{who} is on the on-call rotation", "category": "fact",
           "subject": "on-call"}] for who in ("Sara", "Ravi")],
        judgement={"relation": "conflict", "confidence": 0.9, "reason": "who is on call?"},
    )
    try:
        from tests.test_ingest_pipeline import DIM

        settings.embedding_dim = DIM
        await store.ensure_collection()
        memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
        resolver = ResolutionService(memories, llm, settings)  # type: ignore[arg-type]

        async def rules(owner: str) -> set[str]:
            return {"on-call"} if owner == "mark" else set()

        ingest = IngestService(FactExtractor(llm), memories, resolver, llm, settings,  # type: ignore[arg-type]
                               rules=rules)
        await ingest.ingest(IngestRequest(user_id="mark", text="a"))
        second = await ingest.ingest(IngestRequest(user_id="mark", text="b"))
        assert llm.judge_calls == 1  # it was judged a conflict...
        assert second.conflicts_raised == []  # ...and the rule stored it side by side
        assert len(second.created) == 1
    finally:
        await store.client.close()
