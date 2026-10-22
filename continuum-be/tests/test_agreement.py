"""Replies to what the assistant suggested (requirements.md Part 2, Phase I).

The answer a reply follows is context, never a source of facts about the user.
These pin the guards that read the words — which facts are the user's own, which
were adopted, what an adopted fact may be — and the one ingest rule that keeps
an echo from counting as evidence.
"""

from __future__ import annotations

from continuum.models.memory import ExtractedFact, MemoryCategory, MemoryStatus, Mode
from continuum.models.schemas import ChatRequest, IngestRequest, Message
from continuum.services.agreement import (
    PROPOSAL_MAX_CHARS,
    adopted_only,
    extract_turn,
    reply_transcript,
)
from continuum.services.memory_store import MemoryStore
from tests.test_ingest_pipeline import StubLLM, build_service, settings, store  # noqa: F401

C = MemoryCategory
PPL = "The user follows a push/pull/legs split six days a week"


def fact(content: str, category: MemoryCategory, subject: str | None = None) -> ExtractedFact:
    return ExtractedFact(content=content, category=category, subject=subject,
                         source_excerpt="from the answer")


# --- Whose words ----------------------------------------------------------------------------


def test_a_choice_taken_from_the_answer_is_kept_and_marked_as_adopted():
    [kept] = adopted_only([fact(PPL, C.ROUTINE, "gym")], reply="it sounds good for me for now")
    assert kept.adopted
    # The decision was made in the reply, so that is its provenance.
    assert kept.source_excerpt == "it sounds good for me for now"


def test_what_the_answer_says_about_the_user_never_comes_back():
    facts = [fact("The user is 22 years old", C.FACT, "self"),
             fact("The user lifts regularly", C.PERSON, "self"),
             fact(PPL, C.ROUTINE, "gym")]
    kept = adopted_only(facts, reply="ok let's do that")
    assert [f.content for f in kept] == [PPL]


def test_an_ok_to_a_suggested_supplement_is_never_health():
    kept = adopted_only([fact("The user takes 5 g of creatine daily", C.HEALTH, "creatine")],
                        reply="sure, I'll do that")
    assert kept == []


def test_the_users_own_words_are_kept_whatever_their_kind():
    # Not a choice, not from the answer: what the user said, in their words.
    [kept] = adopted_only([fact("Ana lives in Porto", C.FACT, "ana")],
                          reply="by the way, my sister Ana moved to Porto last month")
    assert not kept.adopted and kept.source_excerpt == "from the answer"


def test_a_long_answer_is_cut_before_it_reaches_the_extractor():
    text = reply_transcript("ok", "word " * 5000)
    answer = text.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
    assert len(answer) <= PROPOSAL_MAX_CHARS + 2 and answer.endswith(" …")
    assert "The user replied:\nok\n" in text


async def test_a_turn_with_nothing_before_it_is_read_exactly_as_today():
    seen: list[str] = []

    class Extractor:
        async def extract(self, transcript, *, mode):  # noqa: ANN001, ANN202
            seen.append(transcript)
            return []

    for mode in (Mode.PERSONAL, Mode.WORKSPACE):
        await extract_turn(Extractor(), reply="hello my name is mark", proposal=None,  # type: ignore[arg-type]
                           mode=mode)
    assert seen == ["hello my name is mark", "user: hello my name is mark"]


# --- Ingest: an echo is not evidence --------------------------------------------------------


async def test_an_adopted_choice_already_believed_is_not_reinforced(store, settings):  # noqa: F811
    batch = [{"content": PPL, "category": "routine", "subject": "gym"}]
    llm = StubLLM([batch, batch])
    ingest = build_service(store, llm, settings)
    memories = MemoryStore(store, llm, settings)  # type: ignore[arg-type]
    first = (await ingest.ingest(IngestRequest(user_id="mark:personal", text="a"))).created[0]

    echoed = await ingest.ingest(IngestRequest(
        user_id="mark:personal", messages=[Message(role="user", content="yes")],
        proposal="You follow a push/pull/legs split six days a week. Keep it up?",
    ))

    assert echoed.reinforced == [] and echoed.created == []
    stored = await memories.get(first.id)
    assert stored.reinforcement_count == 0 and stored.status is MemoryStatus.ACTIVE


async def test_an_adopted_choice_that_is_new_is_stored(store, settings):  # noqa: F811
    llm = StubLLM([[{"content": PPL, "category": "routine", "subject": "gym"}]])
    ingest = build_service(store, llm, settings)
    result = await ingest.ingest(IngestRequest(
        user_id="mark:personal", messages=[Message(role="user", content="sounds good")],
        proposal="Try a push/pull/legs split, six days a week.",
    ))
    [created] = result.created
    assert created.content == PPL and created.source_excerpt == "sounds good"


# --- The chat write-back hands over the answer being replied to -----------------------------


def test_the_proposal_is_the_answer_just_before_the_users_turn():
    from continuum.services.chat import proposal_for

    request = ChatRequest(user_id="mark", messages=[
        Message(role="assistant", content="Good morning. What's on your mind?"),
        Message(role="user", content="best split for 6 days?"),
        Message(role="assistant", content="Try push/pull/legs."),
        Message(role="user", content="sounds good"),
    ])
    assert proposal_for(request) == "Try push/pull/legs."
    first = ChatRequest(user_id="mark", messages=[Message(role="user", content="hi")])
    assert proposal_for(first) is None


def test_a_client_cannot_hand_ingest_a_proposal():
    from continuum.models.schemas import IngestBody

    assert "proposal" not in IngestBody.model_fields


def test_a_dose_taken_from_an_answer_is_health_whatever_it_was_filed_as():
    # Filed as a decision, three runs in three (FINDINGS §12).
    leaked = fact("The user added 5 grams of creatine daily", C.DECISION, "self")
    assert adopted_only([leaked], reply="sure, I'll do that") == []
    from continuum.services.agreement import names_a_dose

    assert names_a_dose("Takes 200 mg of magnesium at night")
    assert not names_a_dose("The user follows a push/pull/legs split 6 days a week")


def test_the_reply_carries_its_own_instructions_and_the_prompts_stay_as_measured():
    from continuum.services.extraction import EXTRACTION_SYSTEM_PROMPT, PERSONAL_EXTRACTION_PROMPT

    text = reply_transcript("sounds good", "Try push/pull/legs.")
    assert "Still record, as usual, anything new the user says" in text
    for prompt in (EXTRACTION_SYSTEM_PROMPT, PERSONAL_EXTRACTION_PROMPT):
        assert "tentative" not in prompt and "assistant said" not in prompt


def test_a_hedged_reply_makes_what_it_takes_up_pending():
    # The model returned a plain decision for this, three runs in three.
    [kept] = adopted_only(
        [fact("The user will consider a Sunday long run of 10 km", C.DECISION, "running")],
        reply="hmm, I'll think about it",
    )
    assert kept.tentative and kept.adopted


def test_a_no_takes_nothing_from_the_answer_but_keeps_the_users_own_words():
    assert adopted_only([fact("The user will not wake up at 5 am", C.DECISION, "self")],
                        reply="no thanks") == []
    [own] = adopted_only([fact("The team is staying on Jenkins", C.DECISION, "ci")],
                         reply="no, we're staying on Jenkins")
    assert not own.adopted and not own.tentative


def test_a_plain_yes_is_decided():
    [kept] = adopted_only([fact(PPL, C.ROUTINE, "gym")], reply="sounds good, let's do it")
    assert not kept.tentative


# --- Found in the live check, 2026-10-16 --------------------------------------------------


async def test_a_question_is_extracted_as_today_even_after_an_answer():
    from continuum.services.agreement import is_question

    seen: list[str] = []

    class Extractor:
        async def extract(self, transcript, *, mode):  # noqa: ANN001, ANN202
            seen.append(transcript)
            return []

    await extract_turn(Extractor(), reply="should I add a Sunday long run of about 10 km?",  # type: ignore[arg-type]
                       proposal="Here is a split.", mode=Mode.PERSONAL)
    assert seen == ["should I add a Sunday long run of about 10 km?"]  # no answer as context

    class Decider:
        async def extract(self, transcript, *, mode):  # noqa: ANN001, ANN202
            return [fact("Chose Postgres over MongoDB", C.DECISION, "atlas"),
                    fact("The Atlas event store needs transactions", C.CONSTRAINT, "atlas")]

    kept = await extract_turn(Decider(), reply="should we use Postgres or MongoDB? we need "  # type: ignore[arg-type]
                              "transactions", proposal=None, mode=Mode.WORKSPACE)
    # A question makes no choice; what it states is kept.
    assert [f.content for f in kept] == ["The Atlas event store needs transactions"]
    assert is_question("what about swimming") and not is_question("sounds good")


def test_a_fact_hedged_in_its_own_words_is_pending():
    [kept] = adopted_only(
        [fact("The user will consider adding a Sunday long run", C.DECISION, "running")],
        reply="I'll consider adding the Sunday long run",
    )
    assert kept.tentative and not kept.adopted


def test_two_spellings_of_one_name_are_not_a_swap():
    from continuum.services.resolution import swapped_names

    assert not swapped_names(
        "Chose PostgreSQL over MongoDB for transactional integrity in the event store.",
        "Chose Postgres over MongoDB for the Atlas event store due to transactional needs.",
    )
    assert not swapped_names("Atlas uses Mongo", "Atlas uses MongoDB")
    assert swapped_names("Sam is the user's partner", "Priya is the user's partner")  # still a swap


async def test_a_first_turn_that_asks_makes_no_choice(store, settings):  # noqa: F811
    # Live: the first Work turn had no answer before it, so the question guard
    # was skipped and "should we use Postgres or MongoDB?" became a decision.
    llm = StubLLM([[{"content": "Chose Postgres over MongoDB", "category": "decision",
                     "subject": "atlas"}]])
    ingest = build_service(store, llm, settings)
    result = await ingest.ingest(IngestRequest(user_id="mark", messages=[
        Message(role="user", content="should we use Postgres or MongoDB? we need transactions"),
    ]))
    assert result.created == []


# --- Found in use, 2026-10-22: "keep this workout" -------------------------------------------


class Scripted:
    """An extractor that answers each call with the next scripted batch."""

    def __init__(self, *batches: list[ExtractedFact]) -> None:
        self.batches = list(batches)
        self.transcripts: list[str] = []

    async def extract(self, transcript, *, mode):  # noqa: ANN001, ANN201, ARG002
        self.transcripts.append(transcript)
        return self.batches.pop(0) if self.batches else []


SPLIT_ANSWER = "Day 1 Push, Day 2 Pull, Day 3 Legs, Day 4 Full body, Day 5 Push, Day 6 Pull."


async def test_a_choice_that_only_says_this_is_asked_for_once_more():
    vague = fact("The user keeps this workout as their active current workout", C.ROUTINE, "gym")
    named = fact("The user follows a 6-day push, pull, legs, full body, push, pull split",
                 C.ROUTINE, "gym")
    extractor = Scripted([vague], [named])

    [kept] = await extract_turn(extractor, reply="keep this workout as my active current workout",  # type: ignore[arg-type]
                                proposal=SPLIT_ANSWER, mode=Mode.PERSONAL)

    assert kept.content == named.content
    assert len(extractor.transcripts) == 2
    assert "this workout" in extractor.transcripts[1]  # told exactly what was missing


async def test_if_the_retry_is_still_vague_the_first_answer_stands():
    vague = fact("The user keeps this workout as their active current workout", C.ROUTINE, "gym")
    still = fact("The user will follow that plan", C.ROUTINE, "gym")
    [kept] = await extract_turn(Scripted([vague], [still]),  # type: ignore[arg-type]
                                reply="keep this workout as my active current workout",
                                proposal=SPLIT_ANSWER, mode=Mode.PERSONAL)
    assert kept.content == vague.content  # never worse than before; Undo is there


async def test_a_concrete_choice_costs_no_second_call():
    extractor = Scripted([fact(PPL, C.ROUTINE, "gym")])
    await extract_turn(extractor, reply="sounds good", proposal=SPLIT_ANSWER,  # type: ignore[arg-type]
                       mode=Mode.PERSONAL)
    assert len(extractor.transcripts) == 1
