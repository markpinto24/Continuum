"""How long a chat turn takes — p50 / p95 — against the real models.

    uv run python scripts/chat_latency.py              # 2 passes of the script below
    uv run python scripts/chat_latency.py --passes 3

The real ChatService, retrieval, extractor and resolver against the configured
LLM and embedder, in an isolated in-memory Qdrant — nothing touches a real
graph. One scripted Personal conversation per pass, each pass on a fresh graph.

Three times per turn, from the moment the question is sent:

  first token  the answer starts to appear
  done         the answer is complete: the input is free again
  remembered   the turn has landed in memory — which is when `done` used to be
               sent, before the write moved to the background (FINDINGS §16)

Turns run one after another, each after the previous one's memory has landed,
so a measurement is never waiting on another turn's write.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time

from qdrant_client import AsyncQdrantClient

from continuum.clients.llm import LLMClient
from continuum.clients.qdrant import QdrantStore
from continuum.config import get_settings
from continuum.core.logger import configure_logging
from continuum.models.memory import Mode, graph_owner
from continuum.models.schemas import ChatRequest, Message
from continuum.services.chat import ChatService
from continuum.services.extraction import FactExtractor
from continuum.services.ingest import IngestService
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService
from continuum.services.retrieval import RetrievalService

# A week of ordinary use, in order: introductions, a routine, questions about it,
# a suggestion taken up, a maybe, its confirmation, a change, a goodbye.
CONVERSATION = [
    "hi, I'm Sam and I'm 30",
    "I go to the gym four days a week",
    "what do you know about my training?",
    "can you suggest a better split for four days?",
    "sounds good, let's do that",
    "maybe I'll start running on Sundays",
    "what's my plan for the week?",
    "I'll run 5 km every Sunday",
    "my sister Ana lives in Porto",
    "where does Ana live?",
    "I stopped going to the gym on Fridays",
    "thanks, that's all for today",
]


async def run_pass(llm: LLMClient, number: int) -> list[dict[str, float | None]]:
    settings = get_settings()
    store = QdrantStore.__new__(QdrantStore)
    store.settings = settings
    store.collection = f"latency_{number}"
    store.client = AsyncQdrantClient(":memory:")
    await store.ensure_collection()
    try:
        memories = MemoryStore(store, llm, settings)
        ingest = IngestService(
            FactExtractor(llm), memories, ResolutionService(memories, llm, settings), llm,
            settings,
        )
        chat = ChatService(RetrievalService(memories, settings), ingest, llm, settings)
        owner = graph_owner("latency", Mode.PERSONAL)

        history: list[Message] = []
        timings = []
        for text in CONVERSATION:
            history.append(Message(role="user", content=text))
            start = time.perf_counter()
            first = done = remembered = None
            answer: list[str] = []
            async for event in chat.stream(ChatRequest(user_id=owner, messages=history)):
                now = time.perf_counter() - start
                if event.event == "delta":
                    first = first if first is not None else now
                    answer.append(event.data["text"])
                elif event.event == "done":
                    done = now
                elif event.event == "remembered":
                    remembered = now
            history.append(Message(role="assistant", content="".join(answer)))
            timings.append({"first": first, "done": done, "remembered": remembered})
            print(f"  pass {number} · {text[:40]:<40} first {first:5.1f}s  done {done:5.1f}s  "
                  f"remembered {remembered or 0:5.1f}s")
        return timings
    finally:
        await store.client.close()


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank: the value at or below which p% of the turns fall."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--passes", type=int, default=2)
    args = parser.parse_args()
    configure_logging()

    llm = LLMClient(get_settings())
    try:
        turns = []
        for number in range(1, args.passes + 1):
            turns += await run_pass(llm, number)
    finally:
        await llm.aclose()

    print(f"\n  {len(turns)} turns ({args.passes} passes of {len(CONVERSATION)})")
    print("                 p50      p95")
    for key, label in (("first", "first token"), ("done", "done"),
                       ("remembered", "remembered")):
        values = [t[key] for t in turns if t[key] is not None]
        print(f"  {label:<12} {percentile(values, 50):6.1f}s  {percentile(values, 95):6.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
