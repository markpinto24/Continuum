"""Phase 5 — run the evaluation.

    # one pass over the corpus against a live LLM, recorded to disk
    uv run python scripts/run_eval.py --record eval-run.json

    # then sweep the gate as often as you like, for free
    uv run python scripts/run_eval.py --replay eval-run.json
    uv run python scripts/run_eval.py --record run.json --add-cases continuum-cases.yaml

    # extraction: ours vs the Mem0 baseline
    uv run python scripts/run_eval.py --extraction --baseline

    # extraction cases from memories rejected in use (Inbox -> learning panel)
    uv run python scripts/run_eval.py --extraction --add-extraction continuum-extraction.yaml

    # retrieval: did the memories you said should have come up make the top k?
    # Runs against the live graph (Qdrant from .env); sweeps the weights for free.
    uv run python scripts/run_eval.py --retrieval continuum-retrieval.yaml

    # just check the embedding model can tell your entities apart
    uv run python scripts/run_eval.py --preflight

    # derive the two cosine thresholds for the configured embedding model
    EMBEDDING_MODEL=all-minilm EMBEDDING_DIM=384 uv run python scripts/run_eval.py --calibrate

    # derive the subject-meaning threshold: when do two slugs name one thing?
    uv run python scripts/run_eval.py --subjects

    # a confirmed maybe, reworded as decided: does the guard keep only good ones?
    uv run python scripts/run_eval.py --confirmations

    # does an instruction planted in a memory stay data? (real model, 3 runs)
    uv run python scripts/run_eval.py --injection

    # as a gate (the nightly job): exit 1 if a belief was lost or merged away
    uv run python scripts/run_eval.py --record run.json --max-belief-loss 0 --max-merge-loss 0

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
from continuum.clients.qdrant import QdrantStore
from continuum.config import get_settings
from continuum.core.logger import configure_logging
from continuum.evaluation import corpus, metrics, report
from continuum.evaluation import retrieval as retrieval_eval
from continuum.evaluation.calibrate import calibrate, render_calibration
from continuum.evaluation.crowded import load_crowded, render_crowded, run_crowded
from continuum.evaluation.extraction import (
    judged_matches,
    overlap,
    score_extraction,
    states_label,
    subjects_that_meet,
)
from continuum.evaluation.preflight import render_preflight, run_preflight
from continuum.evaluation.runner import EVAL_USER, ResolutionRunner
from continuum.evaluation.subjects import derive as derive_subjects
from continuum.evaluation.subjects import render_subjects, score_pairs
from continuum.evaluation.types import CaseOutcome
from continuum.models.memory import Mode, graph_owner
from continuum.models.schemas import IngestRequest, Message
from continuum.services.extraction import FactExtractor, Mem0Extractor
from continuum.services.memory_store import MemoryStore
from continuum.services.subjects import SubjectRelations
from continuum.services.wording import decided_wording


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


async def run_resolution(
    record: Path | None, only: str | None, force: bool, extra: list[Path],
    personal: bool = False,
) -> list[CaseOutcome]:
    settings = get_settings()
    # The Personal corpus runs in a Personal graph: the owner id is what turns the
    # Personal rules (health, relationships) on, exactly as in the API.
    base = corpus.PERSONAL_RESOLUTION_CORPUS if personal else None
    owner = graph_owner(EVAL_USER, Mode.PERSONAL) if personal else EVAL_USER
    cases = corpus.load_resolution_cases_with(extra, base)
    if extra:
        print(f"Corpus: {len(cases)} cases, including those from {', '.join(map(str, extra))}")
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
        outcomes = await ResolutionRunner(llm, settings, owner=owner).run(cases)
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


async def run_agreement(before: bool) -> None:
    """Replies to what the assistant suggested (requirements.md §15).

    `before` is what the system does today: only the user's turn is read, as
    the chat write-back sends it. After, the reply goes through
    `agreement.extract_turn` — the answer as context, then the guards — which is
    exactly what ingest runs, so the corpus measures what would be stored.
    """
    settings = get_settings()
    cases = corpus.load_extraction_cases(corpus.AGREEMENT_EXTRACTION_CORPUS)
    llm = LLMClient(settings)
    try:
        extractor = FactExtractor(llm)
        results = []
        for case in cases:
            mode = case.mode or Mode.PERSONAL
            if before:
                turn = IngestRequest(
                    user_id="eval", messages=[Message(role="user", content=case.text)]
                )
                text = turn.as_transcript(label_lone_turn=mode is Mode.WORKSPACE)
                facts = await extractor.extract(text, mode=mode)
            else:
                from continuum.services.agreement import extract_turn

                facts = await extract_turn(
                    extractor, reply=case.text, proposal=case.context, mode=mode
                )
            results.append((case, facts))
        _rule(f"EXTRACTION — agreement corpus, {'before (reply only)' if before else 'after'}")
        relations = SubjectRelations(llm, settings.subject_similarity_threshold)
        meeting = await subjects_that_meet(relations, results)
        judged = await judged_matches(llm, results)
        scored = score_extraction(results, meeting=meeting, judged=judged)
        print(report.render_extraction(scored, label="continuum"))
        extracted = [f for _, facts in results for f in facts]
        on_self = sum(f.subject == "self" for f in extracted)
        print(f"    on `self`    {on_self} of {len(extracted)} extracted facts")
        print("\n  per case (read the numbers — token matching cannot):")
        for case, facts in results:
            shown = "; ".join(
                f"{'~' if f.tentative else ''}{f.category.value}/{f.subject}: {f.content}"
                for f in facts
            ) or "(nothing)"
            print(f"    {case.id:24} {shown}")
    finally:
        await llm.aclose()


async def run_confirmations() -> None:
    """Each labelled maybe through the real rewrite and its guard.

    "Right" is the rewrite reading as the label does (token overlap ≥ 0.8 — the
    labels differ from their maybes by the hedge alone, so this is strict).
    "Kept" is the guard refusing, which leaves the maybe's words: safe, clumsy.
    A rewrite that passed the guard but reads wrong is the failure that matters.
    """
    cases = corpus.load_confirmation_cases()
    llm = LLMClient(get_settings())
    try:
        right = kept = wrong = 0
        lines = []
        for case in cases:
            got = await decided_wording(llm, case.pending)
            if got is None:
                kept += 1
                verdict = "kept"
            elif overlap(got, case.expect) >= 0.8:
                right += 1
                verdict = "right"
            else:
                wrong += 1
                verdict = "WRONG"
            held = " [held-out]" if "held-out" in case.tags else ""
            lines.append(f"    {verdict:<6} {case.id}{held}: {got or case.pending}")
        _rule("CONFIRMATIONS — a maybe, worded as decided")
        print(f"  {len(cases)} cases: {right} right · {kept} kept as said · {wrong} wrong\n")
        print("\n".join(lines))
    finally:
        await llm.aclose()


async def run_injection(runs: int = 3) -> None:
    """Each planted instruction, asked about `runs` times. Any obeyed is a failure."""
    from continuum.evaluation import injection

    cases = injection.load_cases()
    llm = LLMClient(get_settings())
    try:
        _rule(f"INJECTION — {len(cases)} planted instructions, {runs} runs each")
        failures = 0
        for case in cases:
            outcomes = [await injection.run_case(llm, case) for _ in range(runs)]
            obeyed = [o for o in outcomes if o.obeyed]
            failures += len(obeyed)
            failed = ("IGNORED" if case.is_cost else "OBEYED") if obeyed else "ok"
            print(f"  {failed:<7} {len(obeyed)}/{runs}  {case.id}")
            for o in obeyed:
                print(f"           {o.obeyed}: {o.answer[:160]!r}")
        print(f"\n  {failures} of {len(cases) * runs} answers failed: obeyed a planted "
              "instruction, or ignored a memory that should shape the answer")
    finally:
        await llm.aclose()


async def run_matcher() -> None:
    """How often the judged matcher agrees with labelled (label, output) pairs.

    A false match — a different fact read as the label — flatters the extractor,
    so it is counted on its own. Held-out pairs were labelled before the matcher
    first saw them.
    """
    pairs = corpus.load_matcher_pairs()
    llm = LLMClient(get_settings())
    try:
        got = [await states_label(llm, p.label, p.output) for p in pairs]
    finally:
        await llm.aclose()
    _rule("MATCHER — does the extraction state the labelled fact?")
    for held in (False, True):
        group = [(p, g) for p, g in zip(pairs, got, strict=True)
                 if ("held-out" in p.tags) is held]
        false_match = [p.id for p, g in group if g and not p.same]
        missed = [p.id for p, g in group if p.same and not g]
        print(f"  {'held-out' if held else 'training'}: "
              f"{sum(g == p.same for p, g in group)}/{len(group)} agree · "
              f"false matches {len(false_match)}/{sum(not p.same for p, _ in group)} "
              f"{false_match} · missed {len(missed)}/{sum(p.same for p, _ in group)} {missed}")


async def run_extraction(
    with_baseline: bool, extra: list[Path], personal: bool = False, prompt: str | None = None
) -> None:
    settings = get_settings()
    if personal:
        cases = corpus.load_extraction_cases(corpus.PERSONAL_EXTRACTION_CORPUS)
        for path in extra:
            cases.extend(corpus.load_extraction_cases(path))
    else:
        cases = corpus.load_extraction_cases_with(extra)
    # Which prompt reads the corpus. Default: the one its mode uses. Running the
    # personal corpus through the workspace prompt is the "before" baseline —
    # what Personal mode would have extracted without its own prompt.
    mode = Mode(prompt) if prompt else (Mode.PERSONAL if personal else Mode.WORKSPACE)
    llm = LLMClient(settings)

    try:
        native = FactExtractor(llm)
        ours = [(case, await native.extract(case.text, mode=mode)) for case in cases]

        _rule(f"EXTRACTION — {'personal' if personal else 'work'} corpus, {mode.value} prompt")
        judged = await judged_matches(llm, ours)
        print(report.render_extraction(
            score_extraction(ours, judged=judged), label=f"continuum ({mode.value})"
        ))

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


async def run_retrieval(path: Path, k: int) -> None:
    settings = get_settings()
    cases = corpus.load_retrieval_cases(path)
    llm = LLMClient(settings)
    qdrant = QdrantStore(settings)
    try:
        gathered = await retrieval_eval.gather(cases, MemoryStore(qdrant, llm, settings), settings)
        current = retrieval_eval.recall_at_k(
            gathered,
            k=k,
            confidence_weight=settings.retrieval_confidence_weight,
            recency_weight=settings.retrieval_recency_weight,
            keyword_weight=settings.retrieval_keyword_weight,
        )
        _rule(f"RETRIEVAL — {len(cases)} questions, memories you said should have come up")
        print(retrieval_eval.render(retrieval_eval.sweep(gathered, k=k), current, k=k))
    finally:
        await qdrant.aclose()
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
        "--subjects",
        action="store_true",
        help="Derive the subject-meaning threshold from the subject-pair corpus. No LLM calls.",
    )
    parser.add_argument(
        "--max-belief-loss", type=float, metavar="RATE",
        help="Exit 1 if the run's belief loss is above this (0 in the nightly job).",
    )
    parser.add_argument(
        "--max-merge-loss", type=float, metavar="RATE",
        help="Exit 1 if the run's merge loss is above this.",
    )
    parser.add_argument(
        "--injection", action="store_true",
        help="Plant instructions in memories; check the real model never obeys them.",
    )
    parser.add_argument(
        "--matcher", action="store_true",
        help="Check the judged extraction matcher against labelled pairs.",
    )
    parser.add_argument(
        "--confirmations",
        action="store_true",
        help="Reword each labelled maybe as decided, through the real guard.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Run even if the embedding preflight fails."
    )
    parser.add_argument(
        "--add-cases",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="Extra resolution cases, e.g. decisions exported from GET /feedback/export. "
        "Repeatable. Tag 'from-use' selects them with --only.",
    )
    parser.add_argument(
        "--add-extraction",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="Extra extraction cases, e.g. rejected memories from "
        "GET /feedback/export?kind=extraction. Repeatable.",
    )
    parser.add_argument(
        "--retrieval",
        type=Path,
        metavar="FILE",
        help="Retrieval cases from GET /feedback/export?kind=retrieval, checked against "
        "the live graph.",
    )
    parser.add_argument("--k", type=int, default=None, help="Top k for --retrieval.")
    parser.add_argument(
        "--agreement", action="store_true",
        help="The agreement corpus: replies to what the assistant suggested.",
    )
    parser.add_argument(
        "--before", action="store_true",
        help="With --agreement: today's behaviour (the reply alone), the baseline.",
    )
    parser.add_argument(
        "--personal", action="store_true",
        help="The Personal-mode corpus (with --extraction, or for resolution).",
    )
    parser.add_argument(
        "--prompt", choices=["personal", "workspace"],
        help="With --extraction: force a prompt (default: the corpus's own mode).",
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
            cases = corpus.load_resolution_cases_with(args.add_cases)
            print(render_calibration(await calibrate(llm, cases)))
        finally:
            await llm.aclose()
        return

    if args.subjects:
        settings = get_settings()
        llm = LLMClient(settings)
        try:
            _rule("SUBJECTS — when do two slugs name one thing?")
            scored = await score_pairs(llm, corpus.load_subject_pairs())
            calibration = derive_subjects(settings.embedding_model, scored)
            print(render_subjects(calibration))
            if settings.subject_similarity_threshold not in (None, calibration.threshold):
                print(f"\n  configured threshold >= {settings.subject_similarity_threshold:.2f}:")
                print(render_subjects(calibration, settings.subject_similarity_threshold))
        finally:
            await llm.aclose()
        return

    if args.confirmations:
        await run_confirmations()
        return

    if args.matcher:
        await run_matcher()
        return

    if args.injection:
        await run_injection()
        return

    if args.agreement:
        await run_agreement(args.before)
        return

    if args.extraction:
        await run_extraction(args.baseline, args.add_extraction, args.personal, args.prompt)
        return

    if args.retrieval:
        await run_retrieval(args.retrieval, args.k or get_settings().chat_memory_limit)
        return

    outcomes = (
        load_recorded(args.replay)
        if args.replay
        else await run_resolution(
            args.record, args.only, args.force, args.add_cases, args.personal
        )
    )
    present(outcomes, show_cases=args.cases)

    breaches = metrics.safety_breaches(
        metrics.score(outcomes),
        max_belief_loss=args.max_belief_loss,
        max_merge_loss=args.max_merge_loss,
    )
    if breaches:
        _rule("SAFETY BUDGET BROKEN")
        print("  " + "\n  ".join(breaches))
        print("  every case off its label: " + ", ".join(metrics.score(outcomes).failures))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
