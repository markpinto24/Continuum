"""Evaluation harness tests.

The harness is a measuring instrument, and an instrument nobody checks will
report whatever is convenient. Everything here is pure — no LLM, no Qdrant — so
the scoring can be trusted before it is pointed at the resolver.

The important assertions are about the two failure modes being kept apart:
retiring a belief that should have lived is not the same kind of wrong as
failing to retire one, and a metric that averages them is useless for setting a
gate.
"""

from __future__ import annotations

import pytest

from continuum.evaluation import metrics
from continuum.evaluation.corpus import (
    CorpusError,
    load_extraction_cases,
    load_resolution_cases,
)
from continuum.evaluation.extraction import (
    MATCH_THRESHOLD,
    match,
    overlap,
    score_extraction,
)
from continuum.evaluation.types import Action, CaseOutcome, ExpectedFact, action_of
from continuum.models.memory import ExtractedFact, MemoryCategory
from continuum.services.resolution import Verdict


def outcome(
    case_id: str,
    expect: Action,
    verdict: Verdict,
    *,
    judge: Verdict | None = None,
    confidence: float | None = None,
    gate: float = 0.80,
    tags: list[str] | None = None,
) -> CaseOutcome:
    return CaseOutcome(
        case_id=case_id,
        expect=expect,
        tags=tags or [],
        verdict=verdict,
        action=action_of(verdict),
        judge_relation=judge,
        judge_confidence=confidence,
        gate=gate,
    )


# --- Replaying a run at a different gate ------------------------------------


def test_replay_downgrades_a_supersede_below_the_gate():
    """The entire point of recording the pre-gate judgement."""
    applied = outcome("x", Action.RETIRE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                      confidence=0.85)

    assert metrics.replay(applied, 0.80) is Action.RETIRE
    assert metrics.replay(applied, 0.90) is Action.ESCALATE


def test_replay_promotes_an_escalation_that_was_only_gated():
    escalated = outcome("x", Action.RETIRE, Verdict.CONFLICT, judge=Verdict.SUPERSEDES,
                        confidence=0.62)

    assert metrics.replay(escalated, 0.80) is Action.ESCALATE
    assert metrics.replay(escalated, 0.60) is Action.RETIRE


def test_a_free_decision_is_gate_independent():
    """Category and subject filters never consulted the judge; the dial cannot move them."""
    free = outcome("x", Action.STORE, Verdict.NEW)

    assert all(metrics.replay(free, g) is Action.STORE for g in (0.5, 0.8, 0.99))


def test_a_judge_conflict_is_gate_independent():
    """Only a `supersedes` passes through the gate. A conflict was never up for it."""
    disputed = outcome("x", Action.ESCALATE, Verdict.CONFLICT, judge=Verdict.CONFLICT,
                       confidence=0.9)

    assert all(metrics.replay(disputed, g) is Action.ESCALATE for g in (0.5, 0.99))


# --- The two failure modes --------------------------------------------------


def test_belief_loss_counts_only_wrongly_retired_beliefs():
    report = metrics.score([
        outcome("a", Action.ESCALATE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.95),  # lost a belief
        outcome("b", Action.RETIRE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.95),  # correct
        outcome("c", Action.STORE, Verdict.NEW),
        outcome("d", Action.STORE, Verdict.NEW),
    ])

    assert report.belief_loss_rate == 0.25
    assert report.stale_belief_rate == 0.0


def test_an_escalated_miss_is_not_counted_as_a_stale_belief():
    """Asking a human is a safe miss. Conflating it with silence would punish caution."""
    report = metrics.score([
        outcome("a", Action.RETIRE, Verdict.CONFLICT, judge=Verdict.SUPERSEDES, confidence=0.6),
    ])

    assert report.stale_belief_rate == 0.0
    assert report.escalation_rate == 1.0
    assert report.accuracy == 0.0  # still wrong, just not dangerously so


def test_stale_belief_counts_a_silent_miss():
    report = metrics.score([
        outcome("a", Action.RETIRE, Verdict.INDEPENDENT, judge=Verdict.INDEPENDENT,
                confidence=0.9),
    ])

    assert report.stale_belief_rate == 1.0


def test_merge_loss_catches_a_false_duplicate():
    """A wrong `duplicate` never stores the new fact at all — that loses information too."""
    report = metrics.score([
        outcome("a", Action.STORE, Verdict.DUPLICATE),
    ])

    assert report.merge_loss_rate == 1.0
    assert report.belief_loss_rate == 0.0  # a different failure, tracked separately


def test_a_system_that_retires_nothing_scores_zero_belief_loss_and_zero_recall():
    """Guards against the metric that flatters cowardice."""
    report = metrics.score([
        outcome("a", Action.RETIRE, Verdict.CONFLICT, judge=Verdict.SUPERSEDES, confidence=0.1),
        outcome("b", Action.RETIRE, Verdict.CONFLICT, judge=Verdict.SUPERSEDES, confidence=0.1),
    ])

    assert report.belief_loss_rate == 0.0
    assert report.retire_recall == 0.0


# --- Precision, recall, reach -----------------------------------------------


def test_retire_precision_and_recall():
    report = metrics.score([
        outcome("tp", Action.RETIRE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.95),
        outcome("fp", Action.STORE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.95),
        outcome("fn", Action.RETIRE, Verdict.INDEPENDENT, judge=Verdict.INDEPENDENT,
                confidence=0.9),
    ])

    assert report.retire_precision == 0.5
    assert report.retire_recall == 0.5


def test_band_miss_flags_a_case_that_never_found_a_candidate():
    """Distinguishes 'the judge was wrong' from 'the fact never got near the belief'."""
    report = metrics.score([
        outcome("unreachable", Action.RETIRE, Verdict.NEW),
        outcome("fine", Action.STORE, Verdict.NEW),
    ])

    assert report.band_miss_rate == 0.5


def test_free_rate_counts_cases_that_skipped_the_judge():
    report = metrics.score([
        outcome("free", Action.STORE, Verdict.NEW),
        outcome("judged", Action.STORE, Verdict.INDEPENDENT, judge=Verdict.INDEPENDENT,
                confidence=0.8),
    ])

    assert report.free_rate == 0.5


def test_judge_agreement_ignores_the_gate():
    """Separates a wrong judge from a wrongly set dial — different problems, different fixes."""
    report = metrics.score([
        # Judge was right; the gate downgraded it.
        outcome("gated", Action.RETIRE, Verdict.CONFLICT, judge=Verdict.SUPERSEDES,
                confidence=0.5),
    ])

    assert report.judge_agreement == 1.0
    assert report.accuracy == 0.0


def test_judge_agreement_is_none_when_nothing_reached_the_judge():
    report = metrics.score([outcome("free", Action.STORE, Verdict.NEW)])
    assert report.judge_agreement is None


def test_scoring_an_empty_corpus_is_an_error_not_a_perfect_score():
    with pytest.raises(ValueError):
        metrics.score([])


# --- The sweep and the recommendation ---------------------------------------


def test_sweep_trades_belief_loss_against_escalation():
    outcomes = [
        # Should be stored, but the judge confidently says supersedes: a belief
        # is lost at any gate it clears.
        outcome("dangerous", Action.STORE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.85),
        # A real reversal the judge is sure about.
        outcome("real", Action.RETIRE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.95),
    ]

    low, high = metrics.score(outcomes, gate=0.80), metrics.score(outcomes, gate=0.90)

    assert low.belief_loss_rate > high.belief_loss_rate
    assert high.escalation_rate > low.escalation_rate


def test_recommend_picks_the_cheapest_gate_with_no_belief_loss():
    outcomes = [
        outcome("bad", Action.STORE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.82),
        outcome("good", Action.RETIRE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.97),
    ]

    best = metrics.recommend_gate(metrics.sweep(outcomes))

    assert best is not None
    assert best.belief_loss_rate == 0.0
    assert best.gate == 0.85  # the lowest gate that clears the bad case


def test_recommend_returns_none_when_no_gate_is_safe():
    """A judge that is confidently wrong cannot be fixed by moving the dial."""
    outcomes = [
        outcome("confidently-wrong", Action.STORE, Verdict.SUPERSEDES,
                judge=Verdict.SUPERSEDES, confidence=1.0),
    ]

    assert metrics.recommend_gate(metrics.sweep(outcomes)) is None


def test_by_tag_isolates_a_regression_to_one_kind_of_case():
    outcomes = [
        outcome("a", Action.ESCALATE, Verdict.SUPERSEDES, judge=Verdict.SUPERSEDES,
                confidence=0.99, tags=["narrowing"]),
        outcome("b", Action.STORE, Verdict.NEW, tags=["different-subject"]),
    ]

    grouped = metrics.by_tag(outcomes)

    assert grouped["narrowing"].belief_loss_rate == 1.0
    assert grouped["different-subject"].belief_loss_rate == 0.0


# --- The corpus itself ------------------------------------------------------


def test_the_shipped_corpus_loads_and_every_case_has_a_rationale():
    cases = load_resolution_cases()

    assert len(cases) >= 25
    assert all(case.why.strip() for case in cases)


def test_the_corpus_covers_every_action():
    labels = {case.expect for case in load_resolution_cases()}
    assert labels == set(Action)


def test_the_corpus_contains_cases_the_system_is_expected_to_fail():
    """A corpus of only passes measures nothing."""
    cases = load_resolution_cases()
    assert any("known-gap" in case.tags for case in cases)


def test_duplicate_case_ids_are_rejected(tmp_path):
    path = tmp_path / "dupes.yaml"
    path.write_text(
        "- id: same\n"
        "  why: first\n"
        "  existing: {content: a, category: fact}\n"
        "  incoming: {content: b, category: fact}\n"
        "  expect: store\n"
        "- id: same\n"
        "  why: second\n"
        "  existing: {content: c, category: fact}\n"
        "  incoming: {content: d, category: fact}\n"
        "  expect: store\n",
        encoding="utf-8",
    )

    with pytest.raises(CorpusError, match="shadowing"):
        load_resolution_cases(path)


def test_extraction_corpus_includes_cases_where_nothing_should_be_extracted():
    """The most useful case in an extraction corpus is the one with no answer."""
    cases = load_extraction_cases()
    assert any(not case.expect for case in cases)


# --- Extraction matching ----------------------------------------------------


def test_overlap_forgives_a_paraphrase_that_keeps_the_nouns():
    score = overlap(
        "Chose Postgres over Mongo because reporting needs real joins",
        "Postgres was picked over Mongo since reporting needs real joins",
    )
    assert score > MATCH_THRESHOLD


def test_overlap_rejects_two_unrelated_statements():
    assert overlap("Acme's budget is capped at 5k", "Sara leads the data platform") < 0.1


def test_stopwords_do_not_manufacture_a_match():
    """Without stopword removal, two sentences of filler would look similar."""
    assert overlap("it is in the of and", "they are on that to be") == 0.0


def test_each_fact_is_matched_at_most_once():
    """Two near-identical extractions must not both claim the same label."""
    expected = [
        ExpectedFact(
            content="Atlas stores events in Postgres", category=MemoryCategory.DECISION
        )
    ]
    actual = [
        ExtractedFact(content="Atlas stores events in Postgres"),
        ExtractedFact(content="Atlas stores its events in Postgres"),
    ]

    assert len(match(expected, actual)) == 1


def test_extraction_scoring_penalises_an_invented_memory():
    from continuum.evaluation.types import ExtractionCase

    case = ExtractionCase(id="chatter", why="nothing durable", text="thanks!", expect=[])
    report = score_extraction([(case, [ExtractedFact(content="The user said thanks")])])

    assert report.fact_precision == 0.0
    assert report.spurious


def test_extraction_scoring_rewards_a_correct_empty_answer():
    from continuum.evaluation.types import ExtractionCase

    case = ExtractionCase(id="chatter", why="nothing durable", text="thanks!", expect=[])
    report = score_extraction([(case, [])])

    assert report.missed == []
    assert report.spurious == []


# --- The runner, against the real resolver ----------------------------------


class StubJudgeLLM:
    """Deterministic embeddings plus a scripted judge, as in test_ingest_pipeline."""

    def __init__(self, judgement: dict | None = None) -> None:
        self.judgement = judgement or {"relation": "independent", "confidence": 0.9}
        self.judge_calls = 0

    async def complete_json(self, *, system: str, user: str, **kwargs):  # noqa: ARG002
        self.judge_calls += 1
        return self.judgement

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        import hashlib

        subject = text.split("]")[0][1:] if text.startswith("[") else ""

        def unit(seed: str) -> list[float]:
            digest = hashlib.sha512(seed.lower().encode()).digest()
            raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(128)]
            norm = sum(v * v for v in raw) ** 0.5 or 1.0
            return [v / norm for v in raw]

        content = unit(text)
        if not subject:
            return content
        subject_vec = unit(subject)
        combined = [1.0 * s + (1 / 3) * c for s, c in zip(subject_vec, content, strict=True)]
        norm = sum(v * v for v in combined) ** 0.5 or 1.0
        return [v / norm for v in combined]


def _runner(judgement: dict, **overrides):
    from continuum.config import Settings
    from continuum.evaluation.runner import ResolutionRunner

    settings = Settings(
        embedding_dim=128,
        duplicate_similarity_threshold=0.99,
        conflict_similarity_threshold=0.70,
        **overrides,
    )
    llm = StubJudgeLLM(judgement)
    return ResolutionRunner(llm, settings), llm  # type: ignore[arg-type]


def _case(case_id: str, expect: Action, *, subject: str = "atlas"):
    from continuum.evaluation.types import CorpusFact, CorpusMemory, ResolutionCase

    return ResolutionCase(
        id=case_id,
        why="fixture",
        existing=CorpusMemory(
            content="Atlas stores events in Postgres",
            category=MemoryCategory.DECISION,
            subject="atlas",
            age_days=60,
        ),
        incoming=CorpusFact(
            content="Atlas stores events in MongoDB",
            category=MemoryCategory.DECISION,
            subject=subject,
        ),
        expect=expect,
    )


async def test_runner_records_a_confident_supersede():
    runner, llm = _runner({"relation": "supersedes", "confidence": 0.95})

    result = await runner.run_case(_case("swap", Action.RETIRE))

    assert result.action is Action.RETIRE
    assert result.judge_relation is Verdict.SUPERSEDES
    assert result.judge_confidence == 0.95
    assert result.similarity is not None
    assert llm.judge_calls == 1


async def test_runner_recovers_the_pre_gate_judgement_from_an_escalation():
    """The recorded judgement must survive the downgrade, or the sweep is fiction."""
    runner, _ = _runner({"relation": "supersedes", "confidence": 0.55})

    result = await runner.run_case(_case("gated", Action.RETIRE))

    assert result.action is Action.ESCALATE          # what the gate did
    assert result.judge_relation is Verdict.SUPERSEDES  # what the judge said
    assert result.judge_confidence == 0.55

    # And replaying below the gate recovers the retire.
    assert metrics.replay(result, 0.50) is Action.RETIRE


async def test_runner_records_a_free_decision_with_no_judge_call():
    """A different subject is settled by the filter; nothing should reach the judge."""
    runner, llm = _runner({"relation": "supersedes", "confidence": 0.99})

    result = await runner.run_case(_case("other-project", Action.STORE, subject="orion"))

    assert result.action is Action.STORE
    assert result.judge_relation is None
    assert result.used_llm is False
    assert llm.judge_calls == 0


async def test_runner_isolates_cases_from_each_other():
    """Case 2 must not see case 1's memory, or the corpus stops being independent."""
    runner, _ = _runner({"relation": "supersedes", "confidence": 0.95})

    first = await runner.run_case(_case("one", Action.RETIRE))
    second = await runner.run_case(_case("two", Action.RETIRE))

    # Each case saw exactly one neighbour — its own seeded belief.
    assert first.similarity == second.similarity


async def test_recorded_outcomes_round_trip_through_json():
    """The whole record/replay workflow depends on this."""
    import json

    runner, _ = _runner({"relation": "supersedes", "confidence": 0.55})
    original = await runner.run_case(_case("gated", Action.RETIRE))

    restored = CaseOutcome.model_validate(json.loads(original.model_dump_json()))

    assert restored == original
    assert metrics.replay(restored, 0.5) is Action.RETIRE


# --- Embedding preflight ----------------------------------------------------


class BlindEmbedder:
    """Stands in for a model that cannot see an entity swap.

    Returns the same vector for anything sharing a sentence shape, which is
    exactly what nomic-embed-text does with out-of-vocabulary proper nouns.
    """

    def __init__(self, settings) -> None:  # noqa: ANN001
        self.settings = settings

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class SightedEmbedder:
    def __init__(self, settings) -> None:  # noqa: ANN001
        self.settings = settings

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        # One-hot over a wide space: distinct texts land on distinct axes, so
        # any real difference reads as low cosine. Crude, but it isolates the
        # preflight logic from an embedder's own quirks.
        size = 4096
        vectors = []
        for text in texts:
            index = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big") % size
            vector = [0.0] * size
            vector[index] = 1.0
            vectors.append(vector)
        return vectors


async def test_preflight_flags_a_model_that_cannot_see_an_entity_swap():
    """The check that would have stopped the first corpus run from meaning nothing."""
    from continuum.config import Settings
    from continuum.evaluation.preflight import run_preflight

    settings = Settings(embedding_model="blind")
    report = await run_preflight(BlindEmbedder(settings), duplicate_threshold=0.94)  # type: ignore[arg-type]

    assert not report.usable
    assert len(report.blind_pairs) == len(report.entity_pairs)
    assert all(pair.identical for pair in report.entity_pairs)


async def test_preflight_passes_a_model_that_discriminates():
    from continuum.config import Settings
    from continuum.evaluation.preflight import run_preflight

    settings = Settings(embedding_model="sighted")
    report = await run_preflight(SightedEmbedder(settings), duplicate_threshold=0.94)  # type: ignore[arg-type]

    assert report.usable
    assert report.blind_pairs == []


async def test_preflight_report_names_the_blind_pairs():
    from continuum.config import Settings
    from continuum.evaluation.preflight import render_preflight, run_preflight

    settings = Settings(embedding_model="blind")
    rendered = render_preflight(
        await run_preflight(BlindEmbedder(settings), duplicate_threshold=0.94)  # type: ignore[arg-type]
    )

    assert "UNUSABLE" in rendered
    assert "byte-identical vectors" in rendered
    assert "datastore" in rendered


def test_cosine_of_identical_vectors_is_one():
    from continuum.evaluation.preflight import cosine

    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_handles_a_zero_vector_without_dividing_by_zero():
    from continuum.evaluation.preflight import cosine

    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
