"""Re-check stored memories under today's resolver rules (services/recheck.py).

    uv run python scripts/recheck.py              # dry run: what it would do
    uv run python scripts/recheck.py --apply      # write edges and subjects
    uv run python scripts/recheck.py --owner mark --apply

Back up first (scripts/backup.py). Uses the settings in continuum-be/.env —
check EMBEDDING_MODEL matches the running API, or it reads the wrong collection.
"""

from __future__ import annotations

import argparse
import asyncio

from continuum.clients.labels import LabelStore
from continuum.clients.learning import LearningStore
from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import get_settings
from continuum.core.logger import configure_logging
from continuum.db.engine import make_engine
from continuum.services.feedback import FeedbackService
from continuum.services.memory_store import MemoryStore
from continuum.services.recheck import RecheckService
from continuum.services.resolution import ResolutionService


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run).")
    parser.add_argument("--owner", action="append", help="Only these graphs (repeatable).")
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    llm = LLMClient(settings)
    qdrant = QdrantStore(settings)
    engine = make_engine(settings)
    feedback = FeedbackService(LabelStore(engine), settings, learning=LearningStore(engine))
    memories = MemoryStore(qdrant, llm, settings)
    service = RecheckService(
        memories, ResolutionService(memories, llm, settings), settings,
        rules=feedback.compatible_subjects,
    )
    print(f"collection {qdrant.collection} · {'APPLYING' if args.apply else 'dry run'}")
    try:
        for owner in args.owner or await memories.distinct_user_ids():
            report = await service.run(owner, apply=args.apply)
            print(f"\n== {owner}: examined {report.examined}")
            for memory_id, subject in report.subjects_filled:
                print(f"  subject  {memory_id[:8]} -> {subject}")
            for memory_id in report.skipped:
                print(f"  skipped  {memory_id[:8]} (embedding failed; left as it was)")
            for a in report.actions:
                p = f" p={a.judge_confidence:.2f}" if a.judge_confidence is not None else ""
                print(f"  {a.verdict:<10}{p}\n    new: {a.memory}\n    old: {a.target}")
    finally:
        await qdrant.aclose()
        await llm.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
