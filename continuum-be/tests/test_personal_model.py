"""Personal mode's memory model (requirements.md §4): its categories, how fast
they fade, and the two policy rules — health never changes silently, and a
second partner or home is asked about, not assumed. Failure modes first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from continuum.config import Settings
from continuum.models.memory import (
    ExtractedFact,
    Memory,
    MemoryCategory,
    MemoryStatus,
    Mode,
    comparable_categories,
    half_life_for,
    mode_of,
)
from continuum.services.resolution import ResolutionService, Verdict
from tests.test_resolution import ScriptedJudge

C = MemoryCategory


def resolver(relation: str = "supersedes", confidence: float = 0.99):  # noqa: ANN201
    judge = ScriptedJudge(relation, confidence)
    settings = Settings(_env_file=None, duplicate_similarity_threshold=0.94,
                        conflict_similarity_threshold=0.78, auto_supersede_confidence=0.80)
    return ResolutionService(None, judge, settings), judge  # type: ignore[arg-type]


def mem(content, category, subject="mark", owner="mark:personal"):  # noqa: ANN001, ANN201
    return Memory(user_id=owner, content=content, category=category, subject=subject)


def fact(content, category, subject="mark"):  # noqa: ANN001, ANN201
    return ExtractedFact(content=content, category=category, subject=subject)


# --- Fading ----------------------------------------------------------------------------


def test_each_mode_fades_at_its_own_pace():
    assert half_life_for(C.PERSON, Mode.PERSONAL) == 730
    assert half_life_for(C.PERSON, Mode.WORKSPACE) == 365  # Workspace untouched
    assert half_life_for(C.ROUTINE, Mode.PERSONAL) == 45


def test_health_never_fades():
    allergy = mem("Mark is allergic to penicillin", C.HEALTH)
    allergy.last_reinforced_at = datetime.now(UTC) - timedelta(days=3650)
    assert allergy.decayed_confidence(now=datetime.now(UTC), floor=0.05) == allergy.confidence


def test_a_personal_memory_reads_its_half_life_from_its_graph():
    assert mem("Chris is Mark's brother", C.PERSON).half_life_days == 730
    assert mem("Sara owns billing", C.PERSON, owner="mark").half_life_days == 365


def test_the_new_categories_are_compared_with_statements():
    assert comparable_categories(C.FACT, C.HEALTH)  # "stopped taking vitamin D"
    assert comparable_categories(C.ROUTINE, C.FACT)
    assert not comparable_categories(C.HEALTH, C.EVENT)  # history stays history


# --- Health never changes silently --------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (mem("Mark is allergic to penicillin", C.HEALTH),
         fact("Mark is no longer allergic to penicillin", C.HEALTH)),
        # Filed as a plain fact both times: the words still make it health.
        (mem("Mark takes vitamin D daily", C.FACT),
         fact("Mark stopped taking vitamin D", C.FACT)),
    ],
)
async def test_a_health_change_always_asks_even_when_the_judge_is_sure(old, new):
    service, judge = resolver("supersedes", 0.99)
    result = await service.resolve(new, [(old, 0.9)])
    assert judge.calls == 1
    assert result.verdict is Verdict.CONFLICT and result.forced_escalation


async def test_workspace_words_like_condition_do_not_trigger_the_health_rule():
    service, _ = resolver("supersedes", 0.99)
    old = mem("The contract condition is net 30", C.FACT, subject="acme", owner="mark")
    result = await service.resolve(
        fact("The contract condition is now net 60", C.FACT, subject="acme"), [(old, 0.9)]
    )
    assert result.verdict is Verdict.SUPERSEDES


# --- Relationships behave like roles in a Personal graph ---------------------------------


async def test_a_second_partner_named_without_a_change_is_asked_about():
    service, _ = resolver("supersedes", 0.99)
    old = mem("Mark is dating Priya", C.FACT)
    result = await service.resolve(fact("Mark is dating Anya", C.FACT), [(old, 0.9)])
    assert result.verdict is Verdict.CONFLICT and result.forced_escalation


async def test_a_stated_breakup_is_applied():
    service, _ = resolver("supersedes", 0.99)
    old = mem("Mark is dating Priya", C.FACT)
    result = await service.resolve(
        fact("Mark broke up with Priya and is now dating Anya", C.FACT), [(old, 0.9)]
    )
    assert result.verdict is Verdict.SUPERSEDES


async def test_relationship_words_do_not_change_workspace():
    service, _ = resolver("supersedes", 0.99)
    old = mem("Sara works at Acme", C.FACT, subject="sara", owner="mark")
    result = await service.resolve(fact("Sara works at Globex", C.FACT, subject="sara"),
                                   [(old, 0.9)])
    assert result.verdict is Verdict.SUPERSEDES


async def test_a_stated_routine_change_supersedes():
    service, _ = resolver("supersedes", 0.95)
    old = mem("Mark goes to the gym on weekday mornings", C.ROUTINE, subject="gym")
    result = await service.resolve(
        fact("Mark switched the gym to evenings instead of mornings", C.ROUTINE, subject="gym"),
        [(old, 0.9)],
    )
    assert result.verdict is Verdict.SUPERSEDES


async def test_an_event_is_never_retired_in_personal_mode_either():
    service, judge = resolver("supersedes", 0.99)
    old = mem("Mark ran a half marathon on 12 Oct", C.EVENT, subject="half-marathon")
    result = await service.resolve(
        fact("Mark ran a full marathon on 12 Oct", C.EVENT, subject="half-marathon"),
        [(old, 0.9)],
    )
    assert judge.calls == 0 and result.verdict is Verdict.NEW
    assert old.status is MemoryStatus.ACTIVE


# --- Summaries never repeat a health fact ----------------------------------------------------


async def test_a_summary_leaves_health_facts_out():
    from qdrant_client import AsyncQdrantClient

    from continuum.clients.qdrant import QdrantStore
    from continuum.services.memory_store import MemoryStore
    from continuum.services.summaries import SummaryService
    from tests.test_ingest_pipeline import DIM
    from tests.test_summaries import SummaryLLM

    settings = Settings(_env_file=None, embedding_dim=DIM, qdrant_collection="t",
                        summary_min_memories=3)
    store = QdrantStore.__new__(QdrantStore)
    store.settings, store.collection = settings, "t"
    store.client = AsyncQdrantClient(":memory:")
    await store.ensure_collection()
    llm = SummaryLLM()
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    await memories.add_many([
        mem("Mark lives in Pune", C.FACT),
        mem("Mark prefers window seats", C.PREFERENCE),
        mem("Mark drives a Honda City", C.FACT),
        mem("Mark is allergic to penicillin", C.HEALTH),
    ])
    [summary] = await SummaryService(memories, llm, settings).refresh("mark:personal")  # type: ignore[arg-type]
    allergy = next(m for m in await memories.list_all(user_id="mark:personal")
                   if m.category is C.HEALTH)
    assert allergy.id not in summary.derived_from and len(summary.derived_from) == 3
    await store.client.close()


# --- The Personal corpora (requirements.md §8) -----------------------------------------


def test_the_personal_resolution_corpus_covers_what_the_plan_asks_for():
    from continuum.evaluation.corpus import PERSONAL_RESOLUTION_CORPUS, load_resolution_cases
    from continuum.evaluation.types import Action

    cases = load_resolution_cases(PERSONAL_RESOLUTION_CORPUS)
    held_out = [c for c in cases if "held-out-personal" in c.tags]
    assert len(cases) - len(held_out) >= 20 and len(held_out) >= 6
    assert {c.expect for c in cases} == set(Action)

    def tagged(*tags: str) -> list:
        return [c for c in cases if set(tags) <= set(c.tags)]

    assert tagged("relationship", "stated-change") and tagged("relationship", "unstated-change")
    assert tagged("routine") and tagged("preference", "stated-change") and tagged("compatible")
    assert all(c.expect is Action.ESCALATE for c in tagged("health") if c.expect is not
               Action.REINFORCE)  # a health change always asks
    assert all(c.expect is not Action.RETIRE for c in cases if c.existing.category == "event")
    assert any("expected-cost" in c.tags for c in held_out)  # a rule's cost stays visible


def test_the_personal_extraction_corpus_loads():
    from continuum.evaluation.corpus import PERSONAL_EXTRACTION_CORPUS, load_extraction_cases

    cases = load_extraction_cases(PERSONAL_EXTRACTION_CORPUS)
    assert len(cases) >= 20 and all(c.why.strip() for c in cases)
    assert any(not c.expect for c in cases)  # nothing to extract is a case too


async def test_the_runner_runs_personal_cases_in_a_personal_graph():
    from continuum.evaluation.runner import ResolutionRunner, _seed_memory
    from continuum.evaluation.types import ResolutionCase

    case = ResolutionCase.model_validate({
        "id": "x", "why": "y", "expect": "escalate",
        "existing": {"content": "a", "category": "health"},
        "incoming": {"content": "b", "category": "health"},
    })
    assert mode_of(_seed_memory(case, "eval:personal").user_id) is Mode.PERSONAL
    runner = ResolutionRunner(llm=None, owner="eval:personal")  # type: ignore[arg-type]
    assert runner.owner == "eval:personal"


# --- The name-swap guard -----------------------------------------------------------------


def test_a_swapped_name_is_not_a_restatement():
    from continuum.services.resolution import swapped_names

    assert swapped_names("Sam is the user's partner", "Priya is the user's partner")
    assert swapped_names("The user lives in Porto", "The user lives in Lisbon")
    # One name added is detail, not a swap; a reworded sentence swaps nothing.
    assert not swapped_names("The user lives in Kothrud, Pune", "The user lives in Pune")
    assert not swapped_names(
        "The user is a backend engineer at Initech", "The user works at Initech as an engineer"
    )
    assert not swapped_names("Mom's birthday is June 4th", "The user's mom's birthday is 4 June")


async def test_the_judge_cannot_fold_a_new_partner_into_the_old_one():
    # The judge said exactly this, at 0.93, in the first Personal run.
    service, _ = resolver("duplicate", 0.93)
    old = mem("Priya is the user's partner", C.PERSON, subject="partner")
    result = await service.resolve(
        fact("Sam is the user's partner", C.PERSON, subject="partner"), [(old, 0.85)]
    )
    assert result.verdict is Verdict.CONFLICT and result.forced_escalation


async def test_a_reworded_duplicate_is_still_a_duplicate():
    service, _ = resolver("duplicate", 0.95)
    old = mem("The user works at Initech as an engineer", C.FACT, subject="job")
    result = await service.resolve(
        fact("The user is an engineer at Initech", C.FACT, subject="job"), [(old, 0.85)]
    )
    assert result.verdict is Verdict.DUPLICATE


async def test_two_homes_are_not_independent_but_a_detail_is():
    service, _ = resolver("independent", 1.0)
    old = mem("The user lives in Pune", C.FACT, subject="home")
    two_homes = await service.resolve(
        fact("The user lives in Bangalore", C.FACT, subject="home"), [(old, 0.85)]
    )
    detail = await service.resolve(
        fact("The user lives in Kothrud, Pune", C.FACT, subject="home"), [(old, 0.85)]
    )
    assert two_homes.verdict is Verdict.CONFLICT and two_homes.forced_escalation
    assert detail.verdict is Verdict.INDEPENDENT


async def test_workspace_independents_are_untouched_by_the_home_rule():
    service, _ = resolver("independent", 1.0)
    old = mem("Atlas runs in Frankfurt", C.FACT, subject="home", owner="mark")
    result = await service.resolve(
        fact("Atlas runs in Dublin", C.FACT, subject="home"), [(old, 0.85)]
    )
    assert result.verdict is Verdict.INDEPENDENT


# --- What the extractor reads from a chat turn --------------------------------------------


def test_a_lone_personal_chat_turn_reaches_the_extractor_as_its_own_words():
    from continuum.models.schemas import IngestRequest, Message

    turn = [Message(role="user", content="hello my name is mark")]
    # Personal: bare words ("user: hello…" extracted nothing, three in three).
    assert IngestRequest(user_id="m:personal", messages=turn).as_transcript(
        label_lone_turn=False
    ) == "hello my name is mark"
    # Work keeps the label: measured, it extracts more there.
    assert IngestRequest(user_id="m", messages=turn).as_transcript() == (
        "user: hello my name is mark"
    )
    # A real conversation keeps its speakers in either mode.
    both = [*turn, Message(role="assistant", content="Hi Mark")]
    assert IngestRequest(user_id="m:personal", messages=both).as_transcript(
        label_lone_turn=False
    ).startswith("user: ")


async def test_ingest_picks_the_transcript_by_mode():
    from continuum.models.schemas import IngestRequest, Message
    from continuum.services.ingest import IngestService

    seen: list[str] = []

    class Extractor:
        async def extract(self, transcript, *, mode):  # noqa: ANN001, ANN202
            seen.append(transcript)
            return []

    service = IngestService.__new__(IngestService)
    service.extractor = Extractor()  # type: ignore[assignment]
    turn = [Message(role="user", content="hello my name is mark")]
    await service.ingest(IngestRequest(user_id="mark:personal", messages=turn))
    await service.ingest(IngestRequest(user_id="mark", messages=turn))
    assert seen == ["hello my name is mark", "user: hello my name is mark"]
