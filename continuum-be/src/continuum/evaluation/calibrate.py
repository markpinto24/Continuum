"""Derive the two cosine thresholds from the corpus, for one embedding model.

`duplicate_similarity_threshold` and `conflict_similarity_threshold` are
properties of an embedding model, not of the product. 0.94 / 0.78 predate having
any model to calibrate them against, and swapping models without re-deriving
them only moved the failure (FINDINGS §2). This makes the derivation mechanical
and repeatable: embed each corpus pair exactly as ingest would, then ask what the
labels need. No LLM calls — only embeddings.

Two questions, answered separately:

**Conflict band — how low must it reach?** Every pair that needs comparing
(labelled retire, escalate or reinforce) must clear it, or the resolver never
sees the candidate and the stale belief survives unexamined (a band miss). Only
pairs the cheap filters would let through count: a different-subject pair is
decided by the subject filter whatever its score, so no threshold can rescue it,
and letting it drag the band down would only buy judge calls. The band goes a
small margin below the lowest such pair.

**Duplicate band — how high must it start?** A pair above it skips the judge
and discards the incoming fact, unless the duplicate guard sees a material
difference. So the band must sit above every *non-duplicate* the guard cannot
save — pairs whose differences are ordinary vocabulary. Those are the only ones
where a threshold is the last line of defence. If genuine duplicates score below
that, they cost a judge call instead of being free: the safe direction, and the
report says so rather than splitting the difference.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, Field

from continuum.clients.llm import LLMClient
from continuum.evaluation.types import Action, ResolutionCase
from continuum.models.memory import IMMUTABLE_CATEGORIES, Memory
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import material_difference

#: Headroom below the lowest pair that must be compared. Enough to absorb small
#: wording changes; small enough not to invite every loosely-related memory.
CONFLICT_MARGIN = 0.03
#: Headroom above the highest unguarded non-duplicate.
DUPLICATE_MARGIN = 0.01
#: Never let the duplicate band close entirely — at 1.0 only byte-identical
#: vectors short-circuit, which on some models includes real contradictions.
DUPLICATE_CEILING = 0.995


class PairSimilarity(BaseModel):
    case_id: str
    expect: Action
    cosine: float
    comparable: bool = Field(
        ..., description="Same category and subject, neither an event: the judge can see it."
    )
    guarded: bool = Field(
        ..., description="The duplicate guard finds a material difference in the wording."
    )


class Calibration(BaseModel):
    embedding_model: str
    pairs: list[PairSimilarity]
    conflict_threshold: float
    duplicate_threshold: float
    duplicates_needing_judge: list[str] = Field(
        default_factory=list,
        description="Genuine duplicates scoring below the duplicate band: a judge call each.",
    )
    unreachable: list[str] = Field(
        default_factory=list,
        description="Pairs that need comparing but that the cheap filters rule out.",
    )


async def calibrate(llm: LLMClient, cases: Sequence[ResolutionCase]) -> Calibration:
    texts: list[str] = []
    for case in cases:
        for side in (case.existing, case.incoming):
            texts.append(_embedding_text(side.content, side.category, side.subject))
    vectors = await llm.embed(texts)

    pairs = [
        PairSimilarity(
            case_id=case.id,
            expect=case.expect,
            cosine=round(_cosine(vectors[2 * i], vectors[2 * i + 1]), 4),
            comparable=_comparable(case),
            guarded=bool(material_difference(case.incoming.content, case.existing.content)),
        )
        for i, case in enumerate(cases)
    ]
    return derive(llm.settings.embedding_model, pairs)


def derive(model: str, pairs: Sequence[PairSimilarity]) -> Calibration:
    """Pure: thresholds from scored pairs. Split out so the rule is testable."""
    must_compare = [
        p for p in pairs if p.comparable and p.expect is not Action.STORE
    ]
    conflict = (
        _floor(min(p.cosine for p in must_compare) - CONFLICT_MARGIN) if must_compare else 0.78
    )

    # Non-duplicates the guard cannot catch: the threshold is all that stands
    # between them and a discarded fact. Every such pair counts, comparable or
    # not, because the duplicate check runs first on the raw score — before the
    # category and subject filters get a say.
    exposed = [p for p in pairs if p.expect is not Action.REINFORCE and not p.guarded]
    duplicate = min(
        DUPLICATE_CEILING,
        _ceil(max(p.cosine for p in exposed) + DUPLICATE_MARGIN) if exposed else 0.94,
    )
    # The duplicate band must sit above the conflict band or the judge never runs.
    duplicate = max(duplicate, round(conflict + 0.05, 2))

    return Calibration(
        embedding_model=model,
        pairs=list(pairs),
        conflict_threshold=conflict,
        duplicate_threshold=duplicate,
        duplicates_needing_judge=[
            p.case_id for p in pairs if p.expect is Action.REINFORCE and p.cosine < duplicate
        ],
        unreachable=[
            p.case_id for p in pairs if p.expect is not Action.STORE and not p.comparable
        ],
    )


def render_calibration(cal: Calibration) -> str:
    lines = [f"  embedding model: {cal.embedding_model}", ""]
    lines.append("  cosine   want       comparable  guarded   case")
    for p in sorted(cal.pairs, key=lambda p: p.cosine, reverse=True):
        lines.append(
            f"  {p.cosine:.4f}   {p.expect.value:<10} {'yes' if p.comparable else '-':<11} "
            f"{'yes' if p.guarded else '-':<9} {p.case_id}"
        )
    lines += [
        "",
        f"  conflict band   >= {cal.conflict_threshold:.2f}"
        "   (every comparable pair that needs judging)",
        f"  duplicate band  >= {cal.duplicate_threshold:.2f}"
        "   (above every non-duplicate the guard can't see)",
    ]
    if cal.duplicates_needing_judge:
        lines.append(
            f"  {len(cal.duplicates_needing_judge)} genuine duplicate(s) fall below the duplicate"
            " band and will cost a judge call: " + ", ".join(cal.duplicates_needing_judge)
        )
    if cal.unreachable:
        lines.append(
            "  unreachable by any threshold (subject/category/event filter decides first): "
            + ", ".join(cal.unreachable)
        )
    return "\n".join(lines)


def _comparable(case: ResolutionCase) -> bool:
    """Would the cheap filters in ResolutionService._pick_candidate let this reach the judge?"""
    categories = {case.existing.category, case.incoming.category}
    if categories & IMMUTABLE_CATEGORIES:
        return False
    if case.existing.category is not case.incoming.category:
        return False
    return not (
        case.existing.subject and case.incoming.subject
        and case.existing.subject != case.incoming.subject
    )


def _embedding_text(content: str, category, subject: str | None) -> str:  # noqa: ANN001
    return MemoryStore.embedding_text(
        Memory(user_id="calibrate", content=content, category=category, subject=subject)
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 0.0 if norm == 0 else dot / norm


def _floor(value: float) -> float:
    return math.floor(value * 100) / 100


def _ceil(value: float) -> float:
    return math.ceil(value * 100) / 100
