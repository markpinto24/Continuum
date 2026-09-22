"""Phase 5 — run the evaluation.

    # one pass over the corpus against a live LLM, recorded to disk
    uv run python scripts/run_eval.py --record eval-run.json

    # then sweep the gate as often as you like, for free
    uv run python scripts/run_eval.py --replay eval-run.json

    # extraction: ours vs the Mem0 baseline
    uv run python scripts/run_eval.py --extraction --baseline

    # just check the embedding model can tell your entities apart
    uv run python scripts/run_eval.py --preflight

    # derive the two cosine thresholds for the configured embedding model
    EMBEDDING_MODEL=all-minilm EMBEDDING_DIM=384 uv run python scripts/run_eval.py --calibrate

The split matters. Resolution needs a judge call per ambiguous case, which is the
slow and non-deterministic part; the gate sweep needs none, because the recorded
outcome holds the judge's answer from *before* the gate was applied. Run the
corpus once, then argue about the dial with arithmetic.

Requires a reachable LLM and embedding endpoint — the host's Ollama by default:

    ollama pull qwen2.5:7b-instruct && ollama pull bge-m3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from continuum.clients.llm import LLMClient
from continuum.config import get_settings
from continuum.core.logging import configure_logging
from continuum.evaluation import corpus, metrics, report
from continuum.evaluation.calibrate import calibrate, render_calibration
from continuum.evaluation.crowded import load_crowded, render_crowded, run_crowded
from continuum.evaluation.extraction import score_extraction
from continuum.evaluation.preflight import render_preflight, run_preflight
from continuum.evaluation.runner import ResolutionRunner
from continuum.evaluation.types import CaseOutcome
from continuum.services.extraction import FactExtractor, Mem0Extractor


def _rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


async def check_embedding(llm: LLMClient) -> bool:
    """Verify the embedding model can see an entity swap before measuring anything.

    Runs first because no resolution number means anything if it cannot. A model
    that scores "Postgres" against "MongoDB" inside the duplicate band makes the
    contradiction invisible to the resolver, and the harness would otherwise
    print a confident table about a pipeline that never saw the conflict.
    """
    settings = get_settings()
    _rule("PREFLIGHT — can the embedding model see an entity swap?")
    report = await run_preflight(llm, settings.duplicate_similarity_threshold)
    print(render_preflight(report))
    return report.usable


async def run_resolution(record: Path | None, only: str | None, force: bool) -> list[CaseOutcome]:
    settings = get_settings()
    cases = corpus.load_resolution_cases()
    if only:
        cases = [c for c in cases if only in c.id or only in c.tags]
        if not cases:
            sys.exit(f"No case matches {only!r}.")

    llm = LLMClient(settings)
    try:
        if not await check_embedding(llm) and not force:
            sys.exit(
                "\nStopping: the embedding model cannot distinguish the entities this\n"
                "corpus is built from, so the resolution numbers would be noise.\n"
                "Switch EMBEDDING_MODEL, or pass --force to measure anyway."
            )

        print(f"\nRunning {len(cases)} resolution cases at gate "
              f"{settings.auto_supersede_confidence:.2f} ...")
        outcomes = await ResolutionRunner(llm, settings).run(cases)
    finally:
        await llm.aclose()

    if record:
        payload = json.dumps([o.model_dump(mode="json") for o in outcomes], indent=2)
        await asyncio.to_thread(record.write_text, payload, encoding="utf-8")
        print(f"\nRecorded {len(outcomes)} outcomes to {record}")
    return outcomes


def load_recorded(path: Path) -> list[CaseOutcome]:
    if not path.exists():
        sys.exit(f"No recorded run at {path}. Produce one with --record first.")
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [CaseOutcome.model_validate(row) for row in rows]


def present(outcomes: list[CaseOutcome], *, show_cases: bool) -> None:
    _rule("RESOLUTION — as run")
    print(report.render_summary(metrics.score(outcomes)))

    _rule("CONFUSION")
    print(report.render_confusion(metrics.score(outcomes)))

    if show_cases:
        _rule("CASES")
        print(report.render_cases(outcomes))

    _rule("GATE SWEEP — no LLM calls, replayed from the recorded judgements")
    print(report.render_sweep(metrics.sweep(outcomes)))

    _rule("BY TAG")
    print(report.render_by_tag(metrics.by_tag(outcomes)))


async def run_extraction(with_baseline: bool) -> None:
    settings = get_settings()
    cases = corpus.load_extraction_cases()
    llm = LLMClient(settings)

    try:
        native = FactExtractor(llm)
        ours = [(case, await native.extract(case.text)) for case in cases]

        _rule("EXTRACTION")
        print(report.render_extraction(score_extraction(ours), label="continuum (domain-tuned)"))

        if with_baseline:
            baseline = []
            with tempfile.TemporaryDirectory(prefix="mem0-eval-") as tmp:
                extractor = Mem0Extractor.create(settings, tmp)
                try:
                    for case in cases:
                        # Empty the store first, so Mem0's own dedup pass cannot
                        # turn an extraction miss on document N into a hit
                        # against something document N-1 left behind.
                        await extractor.reset()
                        baseline.append((case, await extractor.extract(case.text)))
                finally:
                    extractor.close()

            print()
            print(report.render_extraction(score_extraction(baseline), label="mem0 (baseline)"))
            print(
                "\n  Mem0 returns flat strings, so its category and subject scores are\n"
                "  structurally zero. That is the measurement, not a bug: a fact with no\n"
                "  category cannot be routed by resolution policy, and one with no subject\n"
                "  cannot be filtered before the judge."
            )
    finally:
        await llm.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Continuum evaluation harness")
    parser.add_argument("--record", type=Path, help="Run the corpus and save outcomes here.")
    parser.add_argument("--replay", type=Path, help="Score a saved run. No LLM calls.")
    parser.add_argument("--only", help="Filter cases by id substring or tag.")
    parser.add_argument("--cases", action="store_true", help="Print the per-case table.")
    parser.add_argument("--extraction", action="store_true", help="Run the extraction corpus.")
    parser.add_argument("--baseline", action="store_true", help="Also run the Mem0 baseline.")
    parser.add_argument(
        "--preflight", action="store_true", help="Only check the embedding model, then stop."
    )
    parser.add_argument(
        "--crowded",
        action="store_true",
        help="Check the resolver judges the right memory in crowded graphs. Embeddings only.",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Derive both cosine thresholds for the configured embedding model. No LLM calls.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Run even if the embedding preflight fails."
    )
    args = parser.parse_args()

    configure_logging()

    if args.preflight:
        llm = LLMClient(get_settings())
        try:
            sys.exit(0 if await check_embedding(llm) else 1)
        finally:
            await llm.aclose()

    if args.crowded:
        llm = LLMClient(get_settings())
        try:
            _rule("CROWDED GRAPHS — is the right memory the one that gets judged?")
            print(render_crowded(await run_crowded(llm, load_crowded())))
        finally:
            await llm.aclose()
        return

    if args.calibrate:
        llm = LLMClient(get_settings())
        try:
            _rule("CALIBRATION — thresholds this embedding model needs")
            print(render_calibration(await calibrate(llm, corpus.load_resolution_cases())))
        finally:
            await llm.aclose()
        return

    if args.extraction:
        await run_extraction(args.baseline)
        return

    outcomes = (
        load_recorded(args.replay)
        if args.replay
        else await run_resolution(args.record, args.only, args.force)
    )
    present(outcomes, show_cases=args.cases)


if __name__ == "__main__":
    asyncio.run(main())
