"""Resolution layer tests.

The confidence gate is the most consequential piece of logic in the project —
it decides when a belief gets retired without asking anyone. These tests pin its
behaviour, including the failure modes, which must all fail *toward* the human.
"""

from __future__ import annotations

import pytest

from continuum.config import Settings
from continuum.models.memory import ExtractedFact, Memory, MemoryCategory
from continuum.services.resolution import (
    ResolutionService,
    Verdict,
    _coerce_verdict,
)


class ScriptedJudge:
    """Stands in for the LLM; returns a fixed judgement."""

    def __init__(self, relation: str, confidence: float, reason: str = "because") -> None:
        self.payload = {"relation": relation, "confidence": confidence, "reason": reason}
        self.calls = 0

    async def complete_json(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        return self.payload


class BrokenJudge:
    async def complete_json(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("model unavailable")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        duplicate_similarity_threshold=0.94,
        conflict_similarity_threshold=0.78,
        auto_supersede_confidence=0.80,
    )


def service(judge, settings: Settings) -> ResolutionService:
    return ResolutionService(memories=None, llm=judge, settings=settings)  # type: ignore[arg-type]


def fact(
    content: str = "Acme prefers async written updates",
    category: MemoryCategory = MemoryCategory.PREFERENCE,
    subject: str | None = "acme",
) -> ExtractedFact:
    return ExtractedFact(content=content, category=category, subject=subject)


def memory(
    content: str = "Acme prefers weekly status calls",
    category: MemoryCategory = MemoryCategory.PREFERENCE,
    subject: str | None = "acme",
) -> Memory:
    return Memory(user_id="u", content=content, category=category, subject=subject)


# --- Free decisions (no LLM call) ------------------------------------------


async def test_no_neighbours_is_new(settings):
    judge = ScriptedJudge("supersedes", 0.99)
    result = await service(judge, settings).resolve(fact(), [])
    assert result.verdict is Verdict.NEW
    assert judge.calls == 0  # never pay for a decision that is already obvious


async def test_near_identical_is_duplicate_without_judging(settings):
    judge = ScriptedJudge("supersedes", 0.99)
    result = await service(judge, settings).resolve(fact(), [(memory(), 0.96)])
    assert result.verdict is Verdict.DUPLICATE
    assert judge.calls == 0


async def test_different_category_never_reaches_the_judge(settings):
    """A preference cannot replace a decision, however similar the wording."""
    judge = ScriptedJudge("supersedes", 0.99)
    result = await service(judge, settings).resolve(
        fact(category=MemoryCategory.PREFERENCE),
        [(memory(category=MemoryCategory.DECISION), 0.88)],
    )
    assert result.verdict is Verdict.NEW
    assert judge.calls == 0


async def test_different_subject_never_reaches_the_judge(settings):
    judge = ScriptedJudge("supersedes", 0.99)
    result = await service(judge, settings).resolve(
        fact(subject="acme"), [(memory(subject="globex"), 0.88)]
    )
    assert result.verdict is Verdict.NEW
    assert judge.calls == 0


async def test_events_are_never_superseded(settings):
    """Two things can both have happened. History is append-only."""
    judge = ScriptedJudge("supersedes", 0.99)
    result = await service(judge, settings).resolve(
        fact(content="Shipped v2 on the 14th", category=MemoryCategory.EVENT),
        [(memory(content="Shipped v1 on the 2nd", category=MemoryCategory.EVENT), 0.90)],
    )
    assert result.verdict is Verdict.NEW
    assert judge.calls == 0


# --- The confidence gate ----------------------------------------------------


async def test_confident_supersede_is_applied(settings):
    judge = ScriptedJudge("supersedes", 0.92)
    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])

    assert result.verdict is Verdict.SUPERSEDES
    assert result.escalated_from is None
    assert result.target is not None


async def test_unsure_supersede_is_escalated_not_applied(settings):
    """The core safety property: a coin-flip never retires a belief."""
    judge = ScriptedJudge("supersedes", 0.55)
    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])

    assert result.verdict is Verdict.CONFLICT
    assert result.escalated_from is Verdict.SUPERSEDES
    assert "below auto-resolve gate" in result.reason


async def test_gate_boundary_is_inclusive(settings):
    judge = ScriptedJudge("supersedes", settings.auto_supersede_confidence)
    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])
    assert result.verdict is Verdict.SUPERSEDES


async def test_explicit_conflict_is_passed_through(settings):
    judge = ScriptedJudge("conflict", 0.95)
    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])
    assert result.verdict is Verdict.CONFLICT
    assert result.escalated_from is None


async def test_independent_creates_without_edges(settings):
    judge = ScriptedJudge("independent", 0.9)
    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])
    assert result.verdict is Verdict.INDEPENDENT


async def test_judge_failure_escalates_rather_than_losing_data(settings):
    result = await service(BrokenJudge(), settings).resolve(fact(), [(memory(), 0.85)])
    assert result.verdict is Verdict.CONFLICT
    assert result.judge_confidence == 0.0


# --- Judge output parsing ---------------------------------------------------


def test_coerce_verdict_happy_path():
    relation, confidence, reason = _coerce_verdict(
        {"relation": "supersedes", "confidence": 0.9, "reason": "later position"}
    )
    assert relation is Verdict.SUPERSEDES
    assert confidence == 0.9
    assert reason == "later position"


@pytest.mark.parametrize(
    "payload",
    [
        {"relation": "nonsense", "confidence": 0.99},
        {"confidence": 0.99},
        "not a dict",
        {"relation": "supersedes", "confidence": "high"},  # unparseable confidence
    ],
)
def test_malformed_judge_output_never_yields_a_confident_supersede(payload):
    relation, confidence, _ = _coerce_verdict(payload)
    assert not (relation is Verdict.SUPERSEDES and confidence >= 0.8)


def test_confidence_is_clamped():
    _, confidence, _ = _coerce_verdict({"relation": "conflict", "confidence": 7.5})
    assert confidence == 1.0
