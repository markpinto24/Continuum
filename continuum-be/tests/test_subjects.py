"""Subject canonicalisation.

The subject filter compares slugs exactly, so one entity must get one slug.
These pin the asymmetry the rule is built on: merging two different subjects
costs a judge call, splitting one subject silently disables contradiction
detection — so the rule leans toward merging.

Meaning (`SubjectRelations`) extends that to two words for one thing — gym and
workout, home and flat — without renaming either, and never for names: an
embedder cannot tell Sara from Sarah K.
"""

from __future__ import annotations

import math

import pytest

from continuum.evaluation.corpus import load_subject_pairs
from continuum.evaluation.subjects import HELD_OUT, Rule, ScoredPair, derive, rule_for
from continuum.evaluation.types import SubjectPair
from continuum.models.memory import ExtractedFact, Memory, MemoryCategory
from continuum.services.subjects import (
    SubjectRelations,
    canonical_subject,
    infer_subject,
    looks_alike,
    reads_as_name,
    subject_text,
)

KNOWN = {"atlas-project", "acme", "billing-service", "sara", "search-service"}


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        # Observed in real extractions: the same project, two spellings.
        ("atlas", "atlas-project"),
        ("acme-corp", "acme"),
        ("acme-inc", "acme"),
        ("billing", "billing-service"),
        ("sara-chen", "sara"),
        ("search", "search-service"),
    ],
)
def test_variants_of_one_entity_share_a_slug(candidate, expected):
    assert canonical_subject(candidate, KNOWN) == expected


@pytest.mark.parametrize("candidate", ["orion", "raj", "payments-service", "globex"])
def test_different_entities_stay_apart(candidate):
    assert canonical_subject(candidate, KNOWN) == candidate


def test_an_exact_match_is_returned_unchanged():
    assert canonical_subject("atlas-project", KNOWN) == "atlas-project"


def test_a_null_subject_stays_null():
    """With no subject the filter does not apply at all; guessing one could only narrow it."""
    assert canonical_subject(None, KNOWN) is None


def test_first_seen_spelling_wins():
    """The graph keeps the slug it already has, so earlier memories never need rewriting."""
    assert canonical_subject("atlas-project", {"atlas"}) == "atlas"


def test_the_choice_does_not_depend_on_iteration_order():
    known = ["billing-service", "billing-api-service", "billing"]
    assert canonical_subject("billing-svc", known) == canonical_subject(
        "billing-svc", list(reversed(known))
    )


def test_a_slug_of_only_generic_words_is_its_own_name():
    assert canonical_subject("the-team", KNOWN) == "the-team"


def test_an_empty_core_never_matches_everything():
    """The empty set is a subset of every set — the trap this guards against."""
    assert canonical_subject("-", KNOWN) == "-"


def test_the_accepted_merge_is_the_cheap_direction():
    """billing-team and billing-service collapse together. That costs a judge call
    ("independent"), which is the price of never splitting billing into two."""
    assert canonical_subject("billing-team", {"billing-service"}) == "billing-service"


# --- A missing subject, taken from the words ------------------------------------

NAMED = {"atlas-project", "acme-corp", "orion", "ui"}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Atlas is using MongoDB", "atlas-project"),
        ("The Acme team prefers calls", "acme-corp"),
        ("Atlas and Orion share a database", None),  # about both: no guess
        ("Billing runs on ECS", None),  # names nothing known
        ("The UI is slow", None),  # a two-letter slug is too common a word
        ("Atlantic shipping is delayed", None),  # whole words only
    ],
)
def test_a_missing_subject_is_taken_from_the_words_only_when_unambiguous(content, expected):
    assert infer_subject(content, NAMED) == expected


def test_a_qualified_and_a_plain_spelling_are_one_entity():
    assert infer_subject("Atlas uses Postgres", {"atlas", "atlas-project"}) == "atlas"


@pytest.mark.parametrize("placeholder", ["null", "None", "n/a", "unknown", " — "])
def test_a_placeholder_subject_means_no_subject(placeholder):
    from continuum.services.extraction import _normalise_subject

    assert _normalise_subject(placeholder) is None
    assert _normalise_subject("Atlas Project") == "atlas-project"


# --- Meaning ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("sara", "sarah-k"),  # TODO.md's held-out look-alike
        ("anna", "ana"),
        ("john", "jon"),
        ("alice", "alicia"),
        ("team-a", "team-b"),
        ("vitamin-d", "vitamin-c"),
        # "service" is generic, but these are still two services.
        ("billing-service", "search-service"),
    ],
)
def test_look_alikes_are_different_things(a, b):
    assert looks_alike(a, b) and looks_alike(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("allergy", "allergies"),
        ("running", "run"),
        ("deploy", "deployment"),
        ("home", "house"),  # two letters in common is not a near-spelling
        ("gym", "workout"),
        ("home", "flat"),
    ],
)
def test_one_word_inflected_or_two_words_are_not_look_alikes(a, b):
    assert not looks_alike(a, b)


@pytest.mark.parametrize(
    ("slug", "content", "expected"),
    [
        ("atlas", "Atlas stores events in Postgres", True),
        ("sarah-k", "Sarah K prefers a daily standup call", True),
        ("gym", "The user goes to the gym six days a week", False),
        ("workout", "The user trains four days a week", False),  # not in the words
        # A sentence-initial capital reads as a name: wrong, and harmless — a
        # name only matches by spelling, as every subject did before.
        ("billing", "Billing runs through Stripe", True),
    ],
)
def test_a_subject_written_with_a_capital_reads_as_a_name(slug, content, expected):
    assert reads_as_name(slug, content) is expected


def test_a_slug_is_embedded_as_words():
    assert subject_text("travel-to-work") == "my travel to work"


class SlugEmbedder:
    """Unit vectors at fixed angles, keyed by slug: cosine = cos(angle difference)."""

    def __init__(self, angles: dict[str, float], fail: bool = False) -> None:
        self.angles = angles
        self.fail = fail
        self.embedded: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding endpoint down")
        self.embedded.extend(texts)
        slugs = [t.removeprefix("my ").replace(" ", "-") for t in texts]
        return [[math.cos(self.angles[s]), math.sin(self.angles[s])] for s in slugs]


def _fact(subject: str, content: str = "The user follows a four-day split") -> ExtractedFact:
    return ExtractedFact(content=content, category=MemoryCategory.ROUTINE, subject=subject)


def _memory(subject: str, content: str = "The user follows a six-day split") -> Memory:
    return Memory(user_id="u", content=content, category=MemoryCategory.ROUTINE, subject=subject)


# cos(0.3) = 0.955, cos(1.2) = 0.362: well either side of 0.67.
ANGLES = {"gym": 0.0, "workout": 0.3, "diet": 1.2, "self": 0.05, "sarah-k": 0.0, "sara": 0.01}


async def test_two_words_for_one_thing_meet():
    gym, diet = _memory("gym"), _memory("diet")
    relations = SubjectRelations(SlugEmbedder(ANGLES), threshold=0.67)  # type: ignore[arg-type]
    assert await relations.related(_fact("workout"), [gym, diet]) == {gym.id}


async def test_the_person_themself_never_meets_a_topic_by_meaning():
    embedder = SlugEmbedder(ANGLES)
    relations = SubjectRelations(embedder, threshold=0.67)  # type: ignore[arg-type]
    assert await relations.related(_fact("self"), [_memory("gym")]) == frozenset()
    assert await relations.related(_fact("gym"), [_memory("self")]) == frozenset()
    assert embedder.embedded == []


async def test_names_and_look_alikes_are_never_embedded():
    embedder = SlugEmbedder(ANGLES)
    relations = SubjectRelations(embedder, threshold=0.67)  # type: ignore[arg-type]
    sara = _memory("sara", "Sara prefers async written standups")
    assert await relations.related(
        _fact("sarah-k", "Sarah K prefers a daily standup call"), [sara]
    ) == frozenset()
    # Even written in lower case, the look-alike guard keeps them apart.
    lower = _memory("sara", "standups for sara are async")
    assert await relations.related(_fact("sarah-k", "a daily call"), [lower]) == frozenset()
    assert embedder.embedded == []


async def test_without_a_calibrated_threshold_meaning_is_not_read():
    embedder = SlugEmbedder(ANGLES)
    relations = SubjectRelations(embedder, threshold=None)  # type: ignore[arg-type]
    assert await relations.related(_fact("workout"), [_memory("gym")]) == frozenset()
    assert embedder.embedded == []


async def test_an_embedding_failure_fails_toward_comparing():
    gym, diet = _memory("gym"), _memory("diet")
    relations = SubjectRelations(SlugEmbedder(ANGLES, fail=True), threshold=0.67)  # type: ignore[arg-type]
    # A pair compared costs a judge call; a pair split is never seen by anyone.
    assert await relations.related(_fact("workout"), [gym, diet]) == {gym.id, diet.id}


async def test_each_slug_is_embedded_once():
    embedder = SlugEmbedder(ANGLES)
    relations = SubjectRelations(embedder, threshold=0.67)  # type: ignore[arg-type]
    await relations.related(_fact("workout"), [_memory("gym")])
    await relations.related(_fact("workout"), [_memory("gym"), _memory("diet")])
    assert sorted(embedder.embedded) == ["my diet", "my gym", "my workout"]


# --- The instrument --------------------------------------------------------


def _pair(a: str, b: str, meet: bool, *, names: bool = False, held: bool = False) -> SubjectPair:
    return SubjectPair(
        id=f"{a}--{b}", a=a, b=b, meet=meet, names=names, why="test",
        tags=[HELD_OUT] if held else [],
    )


def test_each_pair_is_decided_by_the_first_rule_that_applies():
    assert rule_for(_pair("atlas", "atlas-project", True)) is Rule.SPELLING
    assert rule_for(_pair("mike", "michael", False, names=True)) is Rule.NAMES
    assert rule_for(_pair("anna", "ana", False)) is Rule.LOOK_ALIKE
    assert rule_for(_pair("gym", "workout", True)) is Rule.MEANING


def test_the_threshold_sits_below_the_lowest_training_pair_that_must_meet():
    scored = [
        ScoredPair(pair=_pair("gym", "workout", True), cosine=0.87, rule=Rule.MEANING),
        ScoredPair(pair=_pair("home", "flat", True), cosine=0.78, rule=Rule.MEANING),
        # Neither sets it: held-out pairs never do, and a name never reaches meaning.
        ScoredPair(pair=_pair("kids", "children", True, held=True), cosine=0.40, rule=Rule.MEANING),
        ScoredPair(pair=_pair("doctor", "gp", True, names=True), cosine=0.30, rule=Rule.NAMES),
        # A pair that must stay apart costs a judge call, never a silent split.
        ScoredPair(pair=_pair("workout", "diet", False), cosine=0.79, rule=Rule.MEANING),
    ]
    assert derive("m", scored).threshold == 0.76


def test_the_subject_corpus_is_labelled_and_holds_cases_back():
    pairs = load_subject_pairs()
    assert all(p.why.strip() for p in pairs)
    held = [p for p in pairs if HELD_OUT in p.tags]
    assert {p.id for p in held} >= {"gym--workout", "home--flat", "sara--sarah-k"}
    assert any(p.meet for p in held) and any(not p.meet for p in held)


async def test_an_extracted_subject_is_right_when_it_meets_the_label():
    from continuum.evaluation.extraction import score_extraction, subjects_that_meet
    from continuum.evaluation.types import ExpectedFact, ExtractionCase

    def case(subject: str | None) -> tuple[ExtractionCase, list[ExtractedFact]]:
        want = ExpectedFact(
            content="The user follows a four-day split",
            category=MemoryCategory.ROUTINE, subject="gym",
        )
        got = _fact(subject)  # type: ignore[arg-type]
        return ExtractionCase(id=f"c-{subject}", why="t", text="t", expect=[want]), [got]

    results = [case("workout"), case("self"), case(None)]
    relations = SubjectRelations(SlugEmbedder(ANGLES), threshold=0.67)  # type: ignore[arg-type]
    report = score_extraction(results, meeting=await subjects_that_meet(relations, results))
    assert report.subject_accuracy == 0.0
    # workout meets gym; no subject is compared with everything; self meets nothing.
    assert report.subject_meet_rate == round(2 / 3, 4)
