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

Its known blind spot is the other direction: a right answer worded longer than
its label ("Ana moved to Porto last month" for "Ana lives in Porto") shares too
few words and counts as a miss. So a second pass reads them — only what the
tokens left unpaired, by a model asked "does this state that?", and never across
a changed number or a negation (`states_label`). Embedding similarity was tried
first and dropped: on held-out pairs it matched tea to coffee and Thursday to
Friday (FINDINGS §15). The token figures stay as they were, so every earlier run
still compares; the judged figures sit beside them, with their measured error.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from continuum.clients.llm import LLMClient
from continuum.evaluation.types import ExpectedFact, ExtractionCase
from continuum.models.memory import ExtractedFact, Memory
from continuum.services.subjects import SubjectRelations, canonical_subject

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
    judged_precision: float | None = Field(
        None, description="Precision with a model's reading added to the token matcher."
    )
    judged_recall: float | None = Field(
        None, description="Recall with a model's reading added to the token matcher."
    )
    judged_f1: float | None = None
    judged_pairs: list[str] = Field(
        default_factory=list, description="What the model paired, to read: label <- output."
    )
    subject_meet_rate: float | None = Field(
        None,
        description="Of matched pairs, share whose subject the resolver would compare "
        "with the label's: the same slug, spelling or meaning, or no subject on a side.",
    )
    excerpt_rate: float = Field(
        ..., description="Extracted facts carrying a source excerpt. Provenance coverage."
    )
    tentative_accuracy: float = Field(
        1.0, description="Of matched pairs, share whose decided/pending flag was right."
    )
    tentative_labelled: int = Field(0, description="Labelled facts that are pending.")

    forbidden_total: int = 0
    forbidden_rate: float = Field(
        0.0, description="Forbidden statements extracted anyway (rejected in real use)."
    )
    forbidden_hits: list[str] = Field(default_factory=list)

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


async def subjects_that_meet(
    relations: SubjectRelations,
    results: list[tuple[ExtractionCase, list[ExtractedFact]]],
) -> set[tuple[int, int]]:
    """The matched (label, extraction) pairs, by id, whose subjects would be compared.

    A subject that differs from the label is only wrong if it keeps the fact from
    the beliefs it should be compared with: `gym` for `workout` is fine once the
    two meet. Read through the resolver's own `SubjectRelations`.
    """
    meeting: set[tuple[int, int]] = set()
    for case, actual in results:
        for want, got in match(case.expect, actual):
            a, b = _normalise(want.subject), _normalise(got.subject)
            # No subject on either side is compared with everything.
            if not a or not b or canonical_subject(b, {a}) == a:
                meeting.add((id(want), id(got)))
                continue
            label = Memory(user_id="eval", content=want.content, category=want.category, subject=a)
            fact = got.model_copy(update={"subject": b})
            if await relations.related(fact, [label]):
                meeting.add((id(want), id(got)))
    return meeting


_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
}
_NUMBER = re.compile(r"\b(\d+(?:[.:]\d+)?)(?:st|nd|rd|th)?\b")
_NEGATION = re.compile(r"\b(?:not|no|never|none|without)\b|n't\b", re.IGNORECASE)


def keeps_numbers_and_negation(label: str, output: str) -> bool:
    """Every number of the label is in the output (4 June = June 4th, six = 6),
    and both say no, or neither does. What embeddings cannot see."""
    return _numbers(label) <= _numbers(output) and bool(_NEGATION.search(label)) == bool(
        _NEGATION.search(output)
    )


def _numbers(text: str) -> set[str]:
    found = set(_NUMBER.findall(text))
    # "6:00" is six o'clock: the held-out "6 am" / "6:00 a.m." was split by this.
    found |= {n.split(":")[0] for n in found if n.endswith(":00")}
    found |= {_NUMBER_WORDS[w] for w in _WORD.findall(text.lower()) if w in _NUMBER_WORDS}
    return found


MATCH_SYSTEM_PROMPT = """You check one extraction against one labelled fact.

Answer "yes" if the EXTRACTION states the LABELLED fact: the same claim, even if
worded differently or with extra detail. Answer "no" if it states a different
fact, or only part of the labelled one (a reason, date, number or qualifier in
the label is missing).

Respond with JSON only: {"same": "<yes|no>"}"""


async def states_label(llm: LLMClient, label: str, output: str) -> bool:
    """Does `output` state `label`? A model's reading, then the number/negation
    guard, which catches the partial fact it called the same (FINDINGS §15)."""
    if not keeps_numbers_and_negation(label, output):
        return False
    try:
        answer = await llm.complete_json(
            system=MATCH_SYSTEM_PROMPT, user=f"LABELLED: {label}\nEXTRACTION: {output}"
        )
    except Exception:  # noqa: BLE001 - an instrument that cannot read scores a miss
        return False
    return isinstance(answer, dict) and str(answer.get("same", "")).lower().startswith("y")


async def judged_matches(
    llm: LLMClient, results: list[tuple[ExtractionCase, list[ExtractedFact]]]
) -> set[tuple[int, int]]:
    """(label, extraction) pairs, by id, that the tokens left apart and a model
    reads as the same fact. Within one case, closest wording first, each side
    used once — the token matcher's own pairing, with a reader in place of a
    threshold."""
    matched: set[tuple[int, int]] = set()
    for case, actual in results:
        paired = match(case.expect, actual)
        wants = [w for w in case.expect if all(w is not p for p, _ in paired)]
        gots = [g for g in actual if all(g is not q for _, q in paired)]
        candidates = sorted(
            ((overlap(w.content, g.content), wi, gi)
             for wi, w in enumerate(wants) for gi, g in enumerate(gots)),
            reverse=True,
        )
        used_w: set[int] = set()
        used_g: set[int] = set()
        for _, wi, gi in candidates:
            if wi in used_w or gi in used_g:
                continue
            if await states_label(llm, wants[wi].content, gots[gi].content):
                used_w.add(wi)
                used_g.add(gi)
                matched.add((id(wants[wi]), id(gots[gi])))
    return matched


def score_extraction(
    results: list[tuple[ExtractionCase, list[ExtractedFact]]],
    *,
    meeting: set[tuple[int, int]] | None = None,
    judged: set[tuple[int, int]] | None = None,
) -> ExtractionReport:
    labelled = extracted = matched = 0
    right_category = right_subject = right_tentative = tentative_labelled = 0
    subject_meets = 0
    with_excerpt = 0
    missed: list[str] = []
    spurious: list[str] = []
    forbidden_total = 0
    forbidden_hits: list[str] = []

    for case, actual in results:
        forbidden_total += len(case.forbid)
        forbidden_hits.extend(
            f"{case.id}: {bad}"
            for bad in case.forbid
            if any(overlap(bad, got.content) >= MATCH_THRESHOLD for got in actual)
        )
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
            subject_meets += meeting is not None and (id(want), id(got)) in meeting
            right_tentative += want.tentative == got.tentative
        tentative_labelled += sum(want.tentative for want in case.expect)

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
    if judged is not None:
        both = matched + len(judged)
        m_precision, m_recall = _ratio(both, extracted), _ratio(both, labelled)
        m_f1 = (
            0.0 if m_precision + m_recall == 0
            else round(2 * m_precision * m_recall / (m_precision + m_recall), 4)
        )

    return ExtractionReport(
        total_cases=len(results),
        labelled_facts=labelled,
        extracted_facts=extracted,
        fact_precision=precision,
        fact_recall=recall,
        fact_f1=round(f1, 4),
        category_accuracy=_ratio(right_category, matched),
        subject_accuracy=_ratio(right_subject, matched),
        subject_meet_rate=None if meeting is None else _ratio(subject_meets, matched),
        judged_precision=None if judged is None else m_precision,
        judged_recall=None if judged is None else m_recall,
        judged_f1=None if judged is None else m_f1,
        judged_pairs=[
            f"{case.id}: {w.content} <- {g.content}"
            for case, actual in results
            for w in case.expect
            for g in actual
            if judged is not None and (id(w), id(g)) in judged
        ],
        excerpt_rate=_ratio(with_excerpt, extracted),
        tentative_accuracy=_ratio(right_tentative, matched) if matched else 1.0,
        tentative_labelled=tentative_labelled,
        forbidden_total=forbidden_total,
        forbidden_rate=_ratio(len(forbidden_hits), forbidden_total),
        forbidden_hits=forbidden_hits,
        missed=missed,
        spurious=spurious,
    )


def _normalise(subject: str | None) -> str:
    return (subject or "").strip().lower()


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)

