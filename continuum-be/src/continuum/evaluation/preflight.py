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
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from continuum.clients.llm import LLMClient

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


class PreflightReport(BaseModel):
    embedding_model: str
    duplicate_threshold: float
    entity_pairs: list[PairScore]
    paraphrase_pairs: list[PairScore]

    @property
    def blind_pairs(self) -> list[PairScore]:
        """Entity swaps the model cannot see — scored at or above the duplicate band."""
        return [p for p in self.entity_pairs if p.cosine >= self.duplicate_threshold]

    @property
    def usable(self) -> bool:
        return not self.blind_pairs


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
        label=label, left=left, right=right, cosine=round(cosine(a, b), 4), identical=a == b
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
        "  entity swaps — these MUST score well below the duplicate threshold",
    ]
    for pair in report.entity_pairs:
        flag = "  <-- BLIND" if pair.cosine >= report.duplicate_threshold else ""
        identical = " (byte-identical vectors)" if pair.identical else ""
        lines.append(f"    {pair.cosine:.4f}  {pair.label:<12}{identical}{flag}")

    lines += ["", "  paraphrases — these should stay high"]
    for pair in report.paraphrase_pairs:
        lines.append(f"    {pair.cosine:.4f}  {pair.label}")

    if report.usable:
        lines += ["", "  OK — the model can tell these entities apart."]
    else:
        blind = ", ".join(p.label for p in report.blind_pairs)
        lines += [
            "",
            f"  UNUSABLE — {len(report.blind_pairs)} entity swap(s) invisible: {blind}.",
            "",
            "  A swap at or above the duplicate threshold is classified a DUPLICATE:",
            "  the old belief is reinforced and the contradicting fact is discarded",
            "  without ever reaching the judge. No gate value fixes this.",
            "",
            "  The resolution numbers below are not meaningful until this passes.",
        ]
    return "\n".join(lines)
