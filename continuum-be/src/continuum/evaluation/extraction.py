"""Extraction scoring, and the Mem0 baseline comparison.

Matching an extracted fact to a labelled one is the hard part. Two correct
extractions of the same fact rarely share a string, so exact matching would score
a good extractor at zero.

The matcher here is **token overlap over content words**, greedily paired
best-first. It is crude, and deliberately so: it is transparent, deterministic,
and needs no model, which means the extraction numbers do not themselves depend
on an embedding model that might be the thing under test. It will forgive a
paraphrase that keeps the nouns and punish one that does not.

Read the numbers accordingly. `fact_recall` of 0.8 means "four in five labelled
facts had something recognisably similar extracted", not "four in five were
extracted correctly".
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from continuum.evaluation.types import ExpectedFact, ExtractionCase
from continuum.models.memory import ExtractedFact

# Words that carry no distinguishing signal; keeping them inflates every overlap.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from",
        "had", "has", "have", "in", "into", "is", "it", "its", "of", "on", "or", "over",
        "that", "the", "their", "then", "there", "these", "they", "this", "to", "was",
        "were", "will", "with",
    }
)
_WORD = re.compile(r"[a-z0-9]+")

#: Overlap above which two statements are treated as the same fact.
MATCH_THRESHOLD = 0.34


class ExtractionReport(BaseModel):
    total_cases: int
    labelled_facts: int
    extracted_facts: int

    fact_precision: float = Field(..., description="Extracted facts that matched a label.")
    fact_recall: float = Field(..., description="Labelled facts that were found.")
    fact_f1: float

    category_accuracy: float = Field(
        ..., description="Of matched pairs, share with the right category."
    )
    subject_accuracy: float = Field(
        ..., description="Of matched pairs, share with the right subject slug."
    )
    excerpt_rate: float = Field(
        ..., description="Extracted facts carrying a source excerpt. Provenance coverage."
    )

    missed: list[str] = Field(default_factory=list, description="Labelled facts never found.")
    spurious: list[str] = Field(default_factory=list, description="Extractions matching no label.")


def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1}


def overlap(left: str, right: str) -> float:
    """Jaccard over content words. 1.0 identical, 0.0 disjoint."""
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match(
    expected: list[ExpectedFact], actual: list[ExtractedFact]
) -> list[tuple[ExpectedFact, ExtractedFact]]:
    """Greedy best-first pairing. Each fact is used at most once."""
    scored = sorted(
        (
            (overlap(want.content, got.content), w_index, g_index)
            for w_index, want in enumerate(expected)
            for g_index, got in enumerate(actual)
        ),
        reverse=True,
    )

    used_expected: set[int] = set()
    used_actual: set[int] = set()
    pairs: list[tuple[ExpectedFact, ExtractedFact]] = []

    for score, w_index, g_index in scored:
        if score < MATCH_THRESHOLD:
            break
        if w_index in used_expected or g_index in used_actual:
            continue
        used_expected.add(w_index)
        used_actual.add(g_index)
        pairs.append((expected[w_index], actual[g_index]))

    return pairs


def score_extraction(
    results: list[tuple[ExtractionCase, list[ExtractedFact]]],
) -> ExtractionReport:
    labelled = extracted = matched = 0
    right_category = right_subject = 0
    with_excerpt = 0
    missed: list[str] = []
    spurious: list[str] = []

    for case, actual in results:
        labelled += len(case.expect)
        extracted += len(actual)
        with_excerpt += sum(bool(f.source_excerpt) for f in actual)

        pairs = match(case.expect, actual)
        matched += len(pairs)

        for want, got in pairs:
            right_category += want.category is got.category
            # A null label means "no single clear entity", and the extractor
            # agreeing that there is none counts as correct.
            right_subject += _normalise(want.subject) == _normalise(got.subject)

        paired_expected = {id(want) for want, _ in pairs}
        paired_actual = {id(got) for _, got in pairs}
        missed.extend(
            f"{case.id}: {w.content}" for w in case.expect if id(w) not in paired_expected
        )
        spurious.extend(
            f"{case.id}: {g.content}" for g in actual if id(g) not in paired_actual
        )

    precision = _ratio(matched, extracted)
    recall = _ratio(matched, labelled)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    return ExtractionReport(
        total_cases=len(results),
        labelled_facts=labelled,
        extracted_facts=extracted,
        fact_precision=precision,
        fact_recall=recall,
        fact_f1=round(f1, 4),
        category_accuracy=_ratio(right_category, matched),
        subject_accuracy=_ratio(right_subject, matched),
        excerpt_rate=_ratio(with_excerpt, extracted),
        missed=missed,
        spurious=spurious,
    )


def _normalise(subject: str | None) -> str:
    return (subject or "").strip().lower()


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)
