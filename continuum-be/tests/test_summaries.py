"""Summaries: written only when a subject is busy and changed, never evidence.

Real in-memory Qdrant; a scripted LLM writes the summary text.
"""

from __future__ import annotations

from continuum.models.memory import (
    ExtractedFact,
    Memory,
    MemoryCategory,
    MemoryStatus,
)
from continuum.services.corrections import CorrectionService
from continuum.services.decay import DecayService
from continuum.services.memory_store import MemoryStore
from continuum.services.resolution import ResolutionService, Verdict
from continuum.services.summaries import SummaryService
from tests.test_ingest_pipeline import StubLLM, settings, store  # noqa: F401


class SummaryLLM(StubLLM):
    def __init__(self, text: str = "Atlas runs on Postgres, is owned by Sara, ships in May.",
                 fail: bool = False) -> None:
        super().__init__([[]])
        self.text, self.fail, self.summary_calls = text, fail, 0

    async def complete_json(self, *, system: str, user: str, **kwargs):  # noqa: ANN003, ANN201
        if "condense recorded beliefs" in system:
            self.summary_calls += 1
            if self.fail:
                raise RuntimeError("model down")
            return {"summary": self.text}
        return await super().complete_json(system=system, user=user, **kwargs)


def atlas_facts(n: int) -> list[Memory]:
    return [Memory(user_id="mark", content=f"Atlas fact number {i}",
                   category=MemoryCategory.FACT, subject="atlas") for i in range(n)]


async def setup(store, settings, llm=None, n: int = 5):  # noqa: ANN001, ANN201, F811
    settings.summary_min_memories = 5
    llm = llm or SummaryLLM()
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    facts = atlas_facts(n)
    await memories.add_many(facts)
    return memories, SummaryService(memories, llm, settings), llm, facts  # type: ignore[arg-type]


async def test_a_busy_subject_gets_one_summary_listing_its_sources(store, settings):  # noqa: F811
    memories, service, llm, facts = await setup(store, settings)
    [summary] = await service.refresh("mark")
    assert summary.kind == "summary" and summary.subject == "atlas"
    assert set(summary.derived_from) == {f.id for f in facts}
    assert (await memories.get(summary.id)).content.startswith("Atlas runs on Postgres")


async def test_a_quiet_subject_is_not_summarised(store, settings):  # noqa: F811
    _, service, llm, _ = await setup(store, settings, n=4)
    assert await service.refresh("mark") == [] and llm.summary_calls == 0


async def test_nothing_changed_means_no_llm_call_and_reinforcement_is_not_a_change(
    store, settings  # noqa: F811
):
    memories, service, llm, facts = await setup(store, settings)
    await service.refresh("mark")
    await memories.reinforce(facts[0])  # confirmed again: not news
    assert await service.refresh("mark") == [] and llm.summary_calls == 1


async def test_a_new_dispute_rewrites_the_summary_and_supersedes_the_old(store, settings):  # noqa: F811
    memories, service, llm, facts = await setup(store, settings)
    [first] = await service.refresh("mark")
    facts[0].mark_contradicted(facts[1].id)
    await memories.save(facts[0])
    [second] = await service.refresh("mark")
    old = await memories.get(first.id)
    assert old.status is MemoryStatus.SUPERSEDED and old.superseded_by == second.id


async def test_a_failed_generation_writes_nothing(store, settings):  # noqa: F811
    memories, service, _, _ = await setup(store, settings, llm=SummaryLLM(fail=True))
    assert await service.refresh("mark") == []
    assert all(m.kind == "fact" for m in await memories.list_all(user_id="mark"))


async def test_a_summary_is_never_judged_against_a_new_fact(store, settings):  # noqa: F811
    memories, service, llm, _ = await setup(
        store, settings, llm=SummaryLLM(text="Atlas fact number 1"), n=5
    )
    [summary] = await service.refresh("mark")
    vector = await llm.embed_one("[atlas] Atlas fact number 1")
    neighbours = await memories.search_by_vector(user_id="mark", vector=vector, limit=20)
    assert summary.id not in {m.id for m, _ in neighbours}

    resolver = ResolutionService(memories, llm, settings)  # type: ignore[arg-type]
    fact = ExtractedFact(content="Atlas fact number 1", category=MemoryCategory.FACT,
                         subject="atlas")
    result = await resolver.resolve(fact, neighbours)
    assert result.verdict is Verdict.DUPLICATE and result.target.kind == "fact"


async def test_summaries_do_not_decay(store, settings):  # noqa: F811
    memories, service, _, _ = await setup(store, settings)
    [summary] = await service.refresh("mark")
    report = await DecayService(memories, settings).sweep(user_id="mark", dry_run=True)
    assert report.scanned == 5  # the five facts, not the summary


async def test_forgetting_a_source_forgets_the_summary(store, settings):  # noqa: F811
    memories, service, _, facts = await setup(store, settings)
    [summary] = await service.refresh("mark")
    await CorrectionService(memories).forget(facts[2])
    stored = await memories.get(summary.id)
    assert stored.content == "[forgotten]" and stored.redacted_at is not None


async def test_a_summary_is_marked_in_the_chat_prompt():
    from continuum.models.schemas import ChatContext, RetrievedMemory
    from continuum.services.chat import build_system_prompt

    summary = Memory(user_id="mark", content="Atlas in brief", category=MemoryCategory.FACT,
                     subject="atlas", kind="summary", derived_from=["a", "b", "c"])
    prompt = build_system_prompt(ChatContext(query="q", memories=[
        RetrievedMemory(memory=summary, similarity=0.8, recency=1.0, score=0.5)
    ]))
    assert "[SUMMARY of 3 memories] Atlas in brief" in prompt

