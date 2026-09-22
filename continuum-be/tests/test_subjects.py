"""Subject canonicalisation.

The subject filter compares slugs exactly, so one entity must get one slug.
These pin the asymmetry the rule is built on: merging two different subjects
costs a judge call, splitting one subject silently disables contradiction
detection — so the rule leans toward merging.
"""

from __future__ import annotations

import pytest

from continuum.services.subjects import canonical_subject

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
