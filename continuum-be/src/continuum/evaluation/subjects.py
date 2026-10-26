"""When do two subject slugs name one thing? Score the rule, derive its threshold.

`SubjectRelations` lets two different slugs be compared when their meanings are
close. Its threshold is a property of the embedding model, like the cosine bands,
so it is derived here from `corpus/subjects.yaml` rather than set by feel. No LLM
calls — only embeddings.

Each pair is decided by the first rule that applies, exactly as in use:

  1. spelling — `canonical_subject` already joins it (atlas / atlas-project);
  2. names    — written as names, they match by spelling only, so they stay apart;
  3. look-alike — `looks_alike` keeps them apart (sara / sarah, team-a / team-b);
  4. meaning  — cosine of `subject_text` against the threshold.

Only pairs that reach rule 4 can say anything about the threshold. Of those, it
sits a small margin below the lowest *training* pair that must meet: a pair kept
apart that should have met is a change nobody is asked about, while a pair that
meets and should not costs a judge call. Held-out pairs never set it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel

from continuum.clients.llm import LLMClient
from continuum.evaluation.types import SubjectPair
from continuum.services.subjects import canonical_subject, looks_alike, subject_text

#: Headroom below the lowest pair that must meet by meaning.
SUBJECT_MARGIN = 0.02
HELD_OUT = "held-out"


class Rule(StrEnum):
    SPELLING = "spelling"
    NAMES = "names"
    LOOK_ALIKE = "look-alike"
    MEANING = "meaning"


class ScoredPair(BaseModel):
    pair: SubjectPair
    cosine: float
    rule: Rule

    @property
    def held_out(self) -> bool:
        return HELD_OUT in self.pair.tags

    def meets(self, threshold: float) -> bool:
        if self.rule is Rule.SPELLING:
            return True
        if self.rule is Rule.MEANING:
            return self.cosine >= threshold
        return False


class SubjectCalibration(BaseModel):
    embedding_model: str
    pairs: list[ScoredPair]
    threshold: float


async def score_pairs(llm: LLMClient, pairs: Sequence[SubjectPair]) -> list[ScoredPair]:
    slugs = list(dict.fromkeys(s for p in pairs for s in (p.a, p.b)))
    vectors = dict(zip(slugs, await llm.embed([subject_text(s) for s in slugs]), strict=True))
    return [
        ScoredPair(pair=p, cosine=round(_cosine(vectors[p.a], vectors[p.b]), 4), rule=rule_for(p))
        for p in pairs
    ]


def rule_for(pair: SubjectPair) -> Rule:
    joined = canonical_subject(pair.a, {pair.b}) == pair.b
    if joined or canonical_subject(pair.b, {pair.a}) == pair.a:
        return Rule.SPELLING
    if pair.names:
        return Rule.NAMES
    if looks_alike(pair.a, pair.b):
        return Rule.LOOK_ALIKE
    return Rule.MEANING


def derive(model: str, scored: Sequence[ScoredPair]) -> SubjectCalibration:
    """Pure: the threshold from scored pairs. Split out so the rule is testable."""
    must_meet = [
        s.cosine for s in scored
        if s.rule is Rule.MEANING and s.pair.meet and not s.held_out
    ]
    threshold = math.floor((min(must_meet) - SUBJECT_MARGIN) * 100) / 100 if must_meet else 1.0
    return SubjectCalibration(embedding_model=model, pairs=list(scored), threshold=threshold)


def render_subjects(cal: SubjectCalibration, threshold: float | None = None) -> str:
    """The table, then how the rule does at `threshold` (default: the derived one)."""
    t = cal.threshold if threshold is None else threshold
    lines = [f"  embedding model: {cal.embedding_model}", ""]
    lines.append("  cosine   want    got     rule        pair")
    for s in sorted(cal.pairs, key=lambda s: (s.held_out, -s.cosine)):
        want = "meet" if s.pair.meet else "apart"
        got = "meet" if s.meets(t) else "apart"
        mark = "  " if want == got else "✗ "
        held = "  [held-out]" if s.held_out else ""
        lines.append(
            f"{mark}{s.cosine:.4f}   {want:<7} {got:<7} {s.rule.value:<11} "
            f"{s.pair.a} / {s.pair.b}{held}"
        )
    lines += ["", f"  derived threshold  >= {cal.threshold:.2f}", ""]
    for label, held in (("training", False), ("held-out", True)):
        group = [s for s in cal.pairs if s.held_out is held]
        lines.append(f"  {label}: {_summary(group, t)}")
    lines += ["", "  sweep (training pairs):"]
    train = [s for s in cal.pairs if not s.held_out]
    for step in range(60, 96, 4):
        lines.append(f"    >= {step / 100:.2f}  {_summary(train, step / 100)}")
    return "\n".join(lines)


def _summary(group: Sequence[ScoredPair], threshold: float) -> str:
    meet = [s for s in group if s.pair.meet]
    apart = [s for s in group if not s.pair.meet]
    met = sum(s.meets(threshold) for s in meet)
    leaked = sum(s.meets(threshold) for s in apart)
    return (
        f"must meet {met}/{len(meet)} met · must stay apart {leaked}/{len(apart)} met"
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 0.0 if norm == 0 else dot / norm
