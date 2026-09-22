"""Check the embedding model before trusting any resolution number.

This exists because the first real run of the corpus produced a confident,
well-formatted, completely meaningless table.

`nomic-embed-text` — the project default — returns **byte-identical vectors** for
"Atlas stores events in Postgres" and "Atlas stores events in MongoDB". Not
similar: identical. Common words are fine (`cat` vs `dog` scores 0.86), but
out-of-vocabulary proper nouns collapse onto the same representation, and product
names, service names and people's names are exactly what work memories are about.

The consequence runs through the whole pipeline. Cosine 1.0 is above
`duplicate_similarity_threshold`, so a direct contradiction is classified a
duplicate, the old belief is *reinforced*, and the new fact is discarded without
ever reaching the judge. The contradiction the system exists to catch is invisible
to it.

No judge prompt and no gate value can recover from that, so the resolution
metrics are not worth reading until this check passes. It runs first, and it
reports rather than assumes.

Since the duplicate guard landed, a swap that merely scores *high* is not
disqualifying on its own: the guard reads the words, sees "Postgres" vs
"MongoDB", and sends the pair to the judge whatever the cosine. Two things still
are. A **blind** pair — vectors effectively identical — means retrieval cannot
tell the entities apart either, so a question about MongoDB surfaces the
Postgres memory at equal rank. An **unsafe** pair — inside the duplicate band
with no difference the guard can see — would still be silently discarded.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from continuum.clients.llm import LLMClient
from continuum.services.resolution import material_difference

#: At or above this the two vectors are, for retrieval purposes, the same point.
BLIND_COSINE = 0.99

#: Minimal pairs: one entity swapped, everything else held constant. Each pair
#: MUST land well below the duplicate threshold, or the swap is invisible.
ENTITY_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("datastore", "Atlas stores events in Postgres", "Atlas stores events in MongoDB"),
    ("runtime", "The billing service runs on ECS", "The billing service runs on EKS"),
    ("person", "Sara owns the billing service", "Raj owns the billing service"),
    ("vendor", "Chose Auth0 for authentication", "Chose Okta for authentication"),
    ("quantity", "The budget is capped at 5k per month", "The budget is capped at 8k per month"),
)

#: A sanity pair in the other direction: two statements that really are the same
#: fact must still score high, or the model is simply noisy rather than blind.
PARAPHRASE_PAIRS: tuple[tuple[str, str, str], ...] = (
    (
        "reworded",
        "The billing service runs on ECS",
        "Billing runs on ECS",
    ),
)


class PairScore(BaseModel):
    label: str
    left: str
    right: str
    cosine: float
    identical: bool = Field(
        ..., description="Byte-identical vectors — the model saw no difference at all."
    )
    guarded: bool = Field(
        default=False, description="The duplicate guard sees a material difference in the words."
    )


class PreflightReport(BaseModel):
    embedding_model: str
    duplicate_threshold: float
    entity_pairs: list[PairScore]
    paraphrase_pairs: list[PairScore]

    @property
    def blind_pairs(self) -> list[PairScore]:
        """Entity swaps the embedding cannot see at all. Retrieval is blind to these too."""
        return [p for p in self.entity_pairs if p.identical or p.cosine >= BLIND_COSINE]

    @property
    def unsafe_pairs(self) -> list[PairScore]:
        """In the duplicate band with nothing for the guard to catch: silently discarded."""
        return [
            p for p in self.entity_pairs
            if p.cosine >= self.duplicate_threshold and not p.guarded
        ]

    @property
    def guard_dependent_pairs(self) -> list[PairScore]:
        """In the duplicate band, but saved by the guard. Fine — worth knowing."""
        return [
            p for p in self.entity_pairs
            if p.cosine >= self.duplicate_threshold and p.guarded and p not in self.blind_pairs
        ]

    @property
    def usable(self) -> bool:
        return not self.blind_pairs and not self.unsafe_pairs


async def run_preflight(llm: LLMClient, duplicate_threshold: float) -> PreflightReport:
    entity = [await _score(llm, *pair) for pair in ENTITY_PAIRS]
    paraphrase = [await _score(llm, *pair) for pair in PARAPHRASE_PAIRS]
    return PreflightReport(
        embedding_model=llm.settings.embedding_model,
        duplicate_threshold=duplicate_threshold,
        entity_pairs=entity,
        paraphrase_pairs=paraphrase,
    )


async def _score(llm: LLMClient, label: str, left: str, right: str) -> PairScore:
    a, b = await llm.embed([left, right])
    return PairScore(
        label=label,
        left=left,
        right=right,
        cosine=round(cosine(a, b), 4),
        identical=a == b,
        guarded=bool(material_difference(left, right)),
    )


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return 0.0 if norm == 0 else dot / norm


def render_preflight(report: PreflightReport) -> str:
    lines = [
        f"  embedding model: {report.embedding_model}",
        f"  duplicate threshold: {report.duplicate_threshold:.2f}",
        "",
        "  entity swaps — must not be blind, and must not be both in the duplicate",
        "  band AND invisible to the duplicate guard",
    ]
    blind = {id(p) for p in report.blind_pairs}
    unsafe = {id(p) for p in report.unsafe_pairs}
    leaning = {id(p) for p in report.guard_dependent_pairs}
    for pair in report.entity_pairs:
        identical = " (byte-identical vectors)" if pair.identical else ""
        if id(pair) in blind:
            flag = "  <-- BLIND"
        elif id(pair) in unsafe:
            flag = "  <-- UNSAFE: duplicate band, guard sees nothing"
        elif id(pair) in leaning:
            flag = "  (in the duplicate band; the guard catches it)"
        else:
            flag = ""
        lines.append(f"    {pair.cosine:.4f}  {pair.label:<12}{identical}{flag}")

    lines += ["", "  paraphrases — these should stay high"]
    for pair in report.paraphrase_pairs:
        lines.append(f"    {pair.cosine:.4f}  {pair.label}")

    if report.usable:
        lines += ["", "  OK — the model can tell these entities apart."]
        return "\n".join(lines)

    lines.append("")
    if report.blind_pairs:
        names = ", ".join(p.label for p in report.blind_pairs)
        lines += [
            f"  UNUSABLE — {len(report.blind_pairs)} entity swap(s) invisible: {names}.",
            "  The vectors are effectively identical, so retrieval cannot tell these",
            "  entities apart: a question about one surfaces the other at equal rank.",
            "  The duplicate guard protects ingest, but it cannot fix the ranking.",
        ]
    if report.unsafe_pairs:
        names = ", ".join(p.label for p in report.unsafe_pairs)
        lines += [
            f"  UNSAFE — {names}: inside the duplicate band with no difference the",
            "  guard can see. These would be classified DUPLICATE and discarded.",
        ]
    lines += ["", "  The resolution numbers below are not meaningful until this passes."]
    return "\n".join(lines)
