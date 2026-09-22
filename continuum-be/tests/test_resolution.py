"""Resolution layer tests.

The confidence gate is the most consequential piece of logic in the project —
it decides when a belief gets retired without asking anyone. These tests pin its
behaviour, including the failure modes, which must all fail *toward* the human.
"""

from __future__ import annotations

import json

import pytest

from continuum.clients.llm import TokenLogprob
from continuum.config import Settings
from continuum.models.memory import ExtractedFact, Memory, MemoryCategory
from continuum.services.resolution import (
    ResolutionService,
    Verdict,
    _coerce_verdict,
    material_difference,
    relation_probabilities,
)


class ScriptedJudge:
    """Stands in for the LLM; returns a fixed judgement."""

    def __init__(self, relation: str, confidence: float, reason: str = "because") -> None:
        self.payload = {"relation": relation, "confidence": confidence, "reason": reason}
        self.calls = 0

    async def complete_json(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        return self.payload

    async def complete_json_with_logprobs(self, **kwargs):  # noqa: ANN003
        """No token stream: the resolver must fall back to the written confidence."""
        self.calls += 1
        return self.payload, json.dumps(self.payload), None


class BrokenJudge:
    async def complete_json(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("model unavailable")

    async def complete_json_with_logprobs(self, **kwargs):  # noqa: ANN003
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
    # A genuine restatement. (This fixture used to pair "async written updates"
    # with "weekly status calls" — a preference reversal, which the duplicate
    # guard now rightly refuses to wave through as a duplicate.)
    judge = ScriptedJudge("supersedes", 0.99)
    result = await service(judge, settings).resolve(
        fact("Acme prefers async written updates"),
        [(memory("Acme prefers written async updates"), 0.96)],
    )
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


# --- The duplicate guard ----------------------------------------------------
#
# DUPLICATE is the only verdict that discards the incoming fact. These pin the
# cases where a near-identical embedding score is lying about that.


@pytest.mark.parametrize(
    ("left", "right", "must_include"),
    [
        # Each of these scored inside the duplicate band on a real embedder.
        ("Acme's budget is capped at 5k per month", "Acme's budget is capped at 8k per month",
         {"5k", "8k"}),
        ("The Atlas launch is set for 14 March", "The Atlas launch has moved to 2 April",
         {"14", "2", "March", "April"}),
        ("Atlas stores events in Postgres", "Atlas stores events in MongoDB",
         {"Postgres", "MongoDB"}),
        ("Sara owns the billing service", "Raj owns the billing service", {"Sara", "Raj"}),
        ("We deploy on Tuesday", "We deploy on Thursday", {"Tuesday", "Thursday"}),
        ("Acme wants weekly calls", "Acme does not want weekly calls", {"not"}),
        # Held-out case release-cadence: no digit anywhere, and it was merged away.
        ("Releases ship every two weeks", "Releases now ship every week", {"two"}),
        ("Standups are held daily", "Standups are held weekly", {"daily", "weekly"}),
        ("Invoices go out monthly", "Invoices go out every quarter", {"monthly"}),
        ("The API returns JSON", "The API now returns XML", {"now"}),
    ],
)
def test_material_difference_catches_contradiction_shapes(left, right, must_include):
    assert must_include <= material_difference(left, right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("The billing service runs on ECS", "Billing runs on ECS"),
        ("Acme prefers written async updates over calls",
         "Acme would rather get written updates than get on a call"),
        ("Atlas stores events in Postgres", "Atlas stores events in Postgres."),
    ],
)
def test_material_difference_ignores_a_real_paraphrase(left, right):
    """Otherwise every duplicate becomes a judge call and the shortcut is worthless."""
    assert material_difference(left, right) == set()


def test_a_shared_sentence_opening_name_is_not_a_difference():
    assert "Atlas" not in material_difference(
        "Atlas uses Redis for caching", "Atlas uses Redis as its cache"
    )


async def test_a_near_duplicate_that_changes_a_number_is_judged_not_discarded(settings):
    """The 5k -> 8k case: before the guard this reinforced the OLD cap and dropped the new one."""
    judge = ScriptedJudge("supersedes", 0.95, reason="the cap was raised")
    old = memory("Acme's budget is capped at 5k per month", MemoryCategory.CONSTRAINT)
    new = fact("Acme's budget is capped at 8k per month", MemoryCategory.CONSTRAINT)

    result = await service(judge, settings).resolve(new, [(old, 0.99)])

    assert result.verdict is Verdict.SUPERSEDES
    assert judge.calls == 1
    assert "5k" in result.reason and "8k" in result.reason  # the audit says why


async def test_an_identical_score_on_an_entity_swap_is_still_judged(settings):
    """nomic-embed-text returns byte-identical vectors for these; the guard must not care."""
    judge = ScriptedJudge("conflict", 0.6)
    old = memory("Atlas stores events in Postgres", MemoryCategory.DECISION, "atlas")
    new = fact("Atlas stores events in MongoDB", MemoryCategory.DECISION, "atlas")

    result = await service(judge, settings).resolve(new, [(old, 1.0)])

    assert result.verdict is Verdict.CONFLICT
    assert judge.calls == 1


async def test_a_real_paraphrase_still_takes_the_free_shortcut(settings):
    judge = ScriptedJudge("supersedes", 0.99)
    old = memory("The billing service runs on ECS", MemoryCategory.FACT, "billing-service")
    new = fact("Billing runs on ECS", MemoryCategory.FACT, "billing-service")

    result = await service(judge, settings).resolve(new, [(old, 0.97)])

    assert result.verdict is Verdict.DUPLICATE
    assert judge.calls == 0


async def test_a_withheld_duplicate_with_a_broken_judge_escalates(settings):
    """Commitment 4: withholding the shortcut must never become a path to silent loss."""
    old = memory("Acme's budget is capped at 5k per month", MemoryCategory.CONSTRAINT)
    new = fact("Acme's budget is capped at 8k per month", MemoryCategory.CONSTRAINT)

    result = await service(BrokenJudge(), settings).resolve(new, [(old, 0.99)])

    assert result.verdict is Verdict.CONFLICT


async def test_a_withheld_duplicate_between_events_keeps_both(settings):
    """Two dated events are two things that happened — never a duplicate, never a supersede."""
    judge = ScriptedJudge("supersedes", 0.99)
    old = memory("Shipped Atlas v1 on 4 March", MemoryCategory.EVENT, "atlas")
    new = fact("Shipped Atlas v1 on 5 March", MemoryCategory.EVENT, "atlas")

    result = await service(judge, settings).resolve(new, [(old, 0.98)])

    assert result.verdict is Verdict.NEW
    assert judge.calls == 0


# --- Confidence from token probabilities ------------------------------------
#
# The written `confidence` was canned (every supersedes at exactly 0.95), so the
# gate could not move. These pin reading the model's actual probability instead.

import math  # noqa: E402


def _tok(text: str, p: float = 1.0, alts: list[tuple[str, float]] | None = None) -> TokenLogprob:
    return TokenLogprob(
        token=text,
        logprob=math.log(p),
        # Zero-probability alternatives are simply absent from a real top-k list.
        top=tuple((t, math.log(q)) for t, q in (alts or [(text, p)]) if q > 0),
    )


def _stream(value_token: TokenLogprob) -> tuple[str, list[TokenLogprob]]:
    tokens = [_tok('{"'), _tok("relation"), _tok('":'), _tok(' "'), value_token,
              _tok('ersedes"'), _tok(', "confidence": 0.95}')]
    return "".join(t.token for t in tokens), tokens


def test_probability_is_read_where_the_relation_is_named():
    raw, tokens = _stream(_tok("sup", 0.7, [("sup", 0.7), ("conf", 0.25), ("dup", 0.03)]))

    probs = relation_probabilities(raw, tokens)

    assert probs is not None
    assert probs[Verdict.SUPERSEDES] == pytest.approx(0.7)
    assert probs[Verdict.CONFLICT] == pytest.approx(0.25)
    assert probs[Verdict.DUPLICATE] == pytest.approx(0.03)


def test_case_variants_of_one_relation_are_pooled():
    """Ollama offered both `conf` and `Conflict` at the same position."""
    raw, tokens = _stream(_tok("conf", 0.65, [("conf", 0.65), ("Conflict", 0.34), ("sup", 0.01)]))

    assert relation_probabilities(raw, tokens)[Verdict.CONFLICT] == pytest.approx(0.99)


def test_a_token_straddling_the_opening_quote_is_handled():
    tokens = [_tok('{"relation":'), _tok(' "sup', 0.6, [(' "sup', 0.6), (' "con', 0.4)]),
              _tok('ersedes"}')]
    raw = "".join(t.token for t in tokens)

    probs = relation_probabilities(raw, tokens)

    assert probs[Verdict.SUPERSEDES] == pytest.approx(0.6)
    assert probs[Verdict.CONFLICT] == pytest.approx(0.4)


def test_mass_on_non_relation_tokens_is_left_unassigned():
    raw, tokens = _stream(_tok("sup", 0.8, [("sup", 0.8), ("冲突", 0.15), ("\t", 0.05)]))

    probs = relation_probabilities(raw, tokens)

    assert probs == {Verdict.SUPERSEDES: pytest.approx(0.8)}


def test_no_relation_key_means_no_probability():
    tokens = [_tok('{"answer": "yes"}')]
    assert relation_probabilities(tokens[0].token, tokens) is None


class TokenJudge:
    """A judge that writes one confidence and assigns a different probability."""

    def __init__(self, relation: str, written: float, p_relation: float) -> None:
        self.payload = {"relation": relation, "confidence": written, "reason": "because"}
        rest = 1.0 - p_relation
        first = relation[:3]
        other = "conf" if first != "con" else "sup"
        self.value = _tok(first, p_relation, [(first, p_relation), (other, rest)])
        self.calls = 0

    async def complete_json_with_logprobs(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        tokens = [_tok('{"relation": "'), self.value, _tok('..."}')]
        return self.payload, "".join(t.token for t in tokens), tokens


async def test_the_gate_acts_on_probability_not_on_the_written_number(settings):
    """Writes 0.95 (the canned value), but only 62% of its mass is on supersedes."""
    judge = TokenJudge("supersedes", written=0.95, p_relation=0.62)

    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])

    assert result.verdict is Verdict.CONFLICT            # 0.62 < 0.80 gate: escalated
    assert result.escalated_from is Verdict.SUPERSEDES
    assert result.judge_confidence == pytest.approx(0.62)
    assert result.confidence_source == "logprob"
    assert result.self_reported_confidence == 0.95       # kept for the audit


async def test_a_genuinely_sure_judge_still_supersedes(settings):
    judge = TokenJudge("supersedes", written=0.55, p_relation=0.97)

    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])

    assert result.verdict is Verdict.SUPERSEDES
    assert result.judge_confidence == pytest.approx(0.97)


async def test_without_a_token_stream_the_written_number_is_used_and_labelled(settings):
    judge = ScriptedJudge("supersedes", 0.92)

    result = await service(judge, settings).resolve(fact(), [(memory(), 0.85)])

    assert result.verdict is Verdict.SUPERSEDES
    assert result.confidence_source == "self_report"


# --- Judge prompt regressions -------------------------------------------------


def test_the_judge_prompt_shows_no_concrete_answer_to_copy():
    """qwen2.5:7b copied a worked example verbatim on a third of its calls — relation,
    confidence and the placeholder reason. The template must show shape, not a verdict."""
    import re

    from continuum.services.resolution import JUDGE_SYSTEM_PROMPT

    concrete = re.search(
        r'"relation":\s*"(duplicate|supersedes|conflict|independent)"', JUDGE_SYSTEM_PROMPT
    )
    assert concrete is None
    assert not re.search(r'"confidence":\s*[0-9]', JUDGE_SYSTEM_PROMPT)


class CapturingJudge(ScriptedJudge):
    async def complete_json_with_logprobs(self, **kwargs):  # noqa: ANN003
        self.user = kwargs["user"]
        return await super().complete_json_with_logprobs(**kwargs)


async def test_the_judge_is_told_the_new_statement_is_the_later_one(settings):
    """Without this, the judge escalated clear reversals 'without clear indication of
    which is later' — its own supersede rule needed an order it was never given."""
    judge = CapturingJudge("supersedes", 0.9)

    await service(judge, settings).resolve(fact(), [(memory(), 0.85)])

    new_block = judge.user.split("NEW statement:")[1]
    assert "after the EXISTING memory" in new_block


def test_thresholds_come_from_the_calibration_table_for_the_model():
    from continuum.config import CALIBRATED_THRESHOLDS

    # _env_file=None: a developer's local .env must not decide this test.
    for model, (duplicate, conflict) in CALIBRATED_THRESHOLDS.items():
        s = Settings(_env_file=None, embedding_model=model)
        assert (s.duplicate_similarity_threshold, s.conflict_similarity_threshold) == (
            duplicate,
            conflict,
        )


def test_an_explicit_threshold_overrides_the_table():
    s = Settings(_env_file=None, embedding_model="bge-m3", duplicate_similarity_threshold=0.99)
    assert s.duplicate_similarity_threshold == 0.99


def test_a_model_tag_still_finds_its_calibration():
    """`bge-m3:latest` is the same model as `bge-m3`."""
    tagged = Settings(_env_file=None, embedding_model="bge-m3:latest")
    plain = Settings(_env_file=None, embedding_model="bge-m3")
    assert tagged.conflict_similarity_threshold == plain.conflict_similarity_threshold


# --- Supersede safety: person-role policies ----------------------------------
#
# FINDINGS §10. Both may only turn a retirement into an escalation.


def _safety_settings(**overrides) -> Settings:
    return Settings(_env_file=None, duplicate_similarity_threshold=0.94,
                    conflict_similarity_threshold=0.78, auto_supersede_confidence=0.80,
                    **overrides)


@pytest.mark.parametrize(
    "text",
    [
        "Nia has taken over from Tom as lead of the mobile team",
        "Lee is now Acme's primary contact",
        "Sara has moved off the data platform and now leads infrastructure",
        "Dev no longer manages the Acme account",
    ],
)
def test_a_stated_handover_is_recognised(text):
    from continuum.services.resolution import states_a_change

    assert states_a_change(text)


@pytest.mark.parametrize(
    "text",
    ["Raj owns the billing service", "Ben maintains the search indexer",
     "Tom reviews auth pull requests"],
)
def test_a_bare_second_name_is_not_a_handover(text):
    from continuum.services.resolution import states_a_change

    assert not states_a_change(text)


async def test_a_bare_second_owner_is_escalated_even_at_certainty():
    """The Sara/Raj case: the judge supersedes at 1.0; the text never says Sara stopped."""
    judge = TokenJudge("supersedes", written=1.0, p_relation=1.0)
    settings = _safety_settings(person_supersede_requires_change_language=True)
    old = memory("Sara owns the billing service", MemoryCategory.PERSON, "billing-service")
    new = fact("Raj owns the billing service", MemoryCategory.PERSON, "billing-service")

    result = await service(judge, settings).resolve(new, [(old, 0.85)])

    assert result.verdict is Verdict.CONFLICT
    assert result.forced_escalation is True


async def test_a_stated_handover_is_still_applied():
    judge = TokenJudge("supersedes", written=1.0, p_relation=0.98)
    settings = _safety_settings(person_supersede_requires_change_language=True)
    old = memory("Tom leads the mobile team", MemoryCategory.PERSON, "mobile-team")
    new = fact("Nia has taken over from Tom as mobile lead", MemoryCategory.PERSON, "mobile-team")

    result = await service(judge, settings).resolve(new, [(old, 0.85)])

    assert result.verdict is Verdict.SUPERSEDES


async def test_the_change_language_rule_only_applies_to_people():
    """A database swap never says 'now' and must still be applied."""
    judge = TokenJudge("supersedes", written=1.0, p_relation=0.98)
    settings = _safety_settings(person_supersede_requires_change_language=True)
    old = memory("Atlas stores events in Postgres", MemoryCategory.DECISION, "atlas")
    new = fact("Atlas stores events in MongoDB", MemoryCategory.DECISION, "atlas")

    result = await service(judge, settings).resolve(new, [(old, 0.85)])

    assert result.verdict is Verdict.SUPERSEDES


async def test_a_second_owner_filed_as_a_fact_is_still_escalated():
    """The live case: the extractor categorised both ownership statements as `fact`."""
    judge = TokenJudge("supersedes", written=1.0, p_relation=1.0)
    settings = _safety_settings(person_supersede_requires_change_language=True)
    old = memory("Sara owns the billing service", MemoryCategory.FACT, "billing-service")
    new = fact("Raj owns the billing service", MemoryCategory.FACT, "billing-service")

    result = await service(judge, settings).resolve(new, [(old, 0.85)])

    assert result.verdict is Verdict.CONFLICT
    assert result.forced_escalation is True


async def test_a_stated_handover_filed_as_a_fact_is_applied():
    judge = TokenJudge("supersedes", written=1.0, p_relation=0.99)
    settings = _safety_settings(person_supersede_requires_change_language=True)
    old = memory("Carl owns the deploy pipeline", MemoryCategory.FACT, "deploy-pipeline")
    new = fact("Dina has taken over ownership of the deploy pipeline from Carl",
               MemoryCategory.FACT, "deploy-pipeline")

    result = await service(judge, settings).resolve(new, [(old, 0.85)])

    assert result.verdict is Verdict.SUPERSEDES


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("The billing service runs on ECS", "The billing service runs on EKS"),
        ("Acme's budget is capped at 5k per month", "Acme's budget is capped at 8k per month"),
    ],
)
def test_non_role_facts_are_not_treated_as_roles(old, new):
    """A `runs on` swap must not need the words 'now' or 'instead' to be applied."""
    from continuum.services.resolution import is_role_statement

    assert not is_role_statement(
        fact(new, MemoryCategory.FACT, "x"), memory(old, MemoryCategory.FACT, "x")
    )
