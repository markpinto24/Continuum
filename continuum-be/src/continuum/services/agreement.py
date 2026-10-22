"""Replies to what the assistant suggested (requirements.md Part 2, §14).

"It sounds good for me for now" holds no fact on its own; the decision lives in
the answer it replies to. So for a chat turn that follows an answer, the
extractor reads that answer too — as context only. It may take a choice the
user adopted from it, never a fact about the user: answers are assembled from
memory, and a fact repeated from memory is not new evidence (CLAUDE.md,
"Ingesting the assistant's reply").

What came from the reply and what came from the answer is decided by reading
the words, not by asking the model: a fact whose content words are mostly in
the user's reply is the user's own; anything else was adopted from the answer,
and only a choice — never a fact about who they are, never health — survives.
"""

from __future__ import annotations

import re

from continuum.core.logger import get_logger
from continuum.models.memory import CHOICE_CATEGORIES, ExtractedFact, Mode
from continuum.models.schemas import IngestRequest, Message
from continuum.services.extraction import FactExtractor
from continuum.services.resolution import HEALTH_WORDS

log = get_logger(__name__)

# Long answers (a seven-day plan) are cut: the choice is named early, and a
# 7B extractor given a wall of text records the wall.
PROPOSAL_MAX_CHARS = 3000

# A fact counts as the user's own when at least this share of its content words
# appear in the reply. "Ana lives in Porto" from "my sister Ana moved to Porto"
# is the user's; "follows a push/pull/legs split" from "sounds good" is not.
OWN_WORDS_SHARE = 0.5

_WORD = re.compile(r"[a-z0-9][a-z0-9'/-]*")
_FILLER = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with",
        "by", "is", "are", "was", "be", "will", "their", "its", "it", "this", "that",
        "user", "user's", "users", "team", "team's", "they", "them", "has", "have",
        "as", "from", "into", "over", "per", "about", "near",
    }
)


# How to read a reply. In the reply's own framing, not the system prompts: an
# extra rule there shifted the Work extractor on every other input (F1 0.74 ->
# 0.56-0.64, three runs each; FINDINGS §12), while a turn that replies to nothing
# never needs it. So everything else is extracted from byte-identical text.
_REPLY_GUIDE = """How to read this reply:
- The assistant's words are context only. Never record anything they say about
  the user or anyone else - that came from memory.
- If the user takes up something the assistant suggested ("sounds good", "let's
  do that", "the second one"), record that choice once, as the user's own
  decision: the choice itself with any change the user made, not every detail.
- Name the choice in full. Never write "this", "that" or "it" for what the
  assistant suggested: write out what it is, with its structure (which days, in
  what order, how often), but not every exercise or step.
- A thank-you or a no takes nothing from the assistant's words.
- Still record, as usual, anything new the user says in their own words.
- Add "tentative": true to a memory the user has not decided on ("maybe", "I'll
  think about it", "not sure yet"); otherwise "tentative": false."""


def reply_transcript(reply: str, proposal: str) -> str:
    """What the extractor reads for a reply. The labels say whose words are whose."""
    answer = proposal.strip()
    if len(answer) > PROPOSAL_MAX_CHARS:
        answer = answer[:PROPOSAL_MAX_CHARS].rsplit(" ", 1)[0] + " …"
    return (
        "The assistant had said (context only - the assistant's words, not the "
        "user's):\n"
        f"<<<\n{answer}\n>>>\n"
        "The user replied:\n"
        f"{reply.strip()}\n\n"
        f"{_REPLY_GUIDE}"
    )


def lone_turn(reply: str, mode: Mode) -> str:
    """A turn with nothing before it, exactly as the chat write-back sends one."""
    turn = IngestRequest(user_id="-", messages=[Message(role="user", content=reply)])
    return turn.as_transcript(label_lone_turn=mode is Mode.WORKSPACE)


async def extract_turn(
    extractor: FactExtractor, *, reply: str, proposal: str | None, mode: Mode
) -> list[ExtractedFact]:
    """The facts one chat turn contributes. Shared by ingest and the evaluation,
    so the corpus measures exactly what would be stored."""
    if is_question(reply):
        # A question adopts nothing, and read beside an answer it came back as a
        # decision ("should I add a Sunday long run?" -> "the user considered
        # adding one", live). Extracted with no context, and it makes no choice:
        # the Work extractor stored "should we use Postgres or MongoDB?" as
        # "chose Postgres", three runs in three. What it states is kept.
        facts = await extractor.extract(lone_turn(reply, mode), mode=mode)
        kept = [f for f in facts if f.category not in CHOICE_CATEGORIES]
        if len(kept) < len(facts):
            log.info("agreement.question_made_no_choice", count=len(facts) - len(kept))
        return kept
    if not proposal or not proposal.strip():
        return await extractor.extract(lone_turn(reply, mode), mode=mode)
    transcript = reply_transcript(reply, proposal)
    facts = await extractor.extract(transcript, mode=mode)
    vague = [f for f in facts if points_at_something(f.content)]
    if vague:
        # "Keep this workout as my active workout" came back as "the user keeps
        # this workout as their active workout", three runs in three: true, and
        # unable ever to say what the workout is. One more try, told exactly what
        # is missing — extraction, not a judgement, so a second call is fine.
        log.info("agreement.vague_reference", count=len(vague))
        retry = await extractor.extract(transcript + _name_it(vague), mode=mode)
        if retry and not any(points_at_something(f.content) for f in retry):
            facts = retry
    return adopted_only(facts, reply=reply)


# "this workout", "that plan", "it as my plan": a choice that names nothing.
_POINTS_AT = re.compile(
    r"\b(?:this|that|these|those|the same|said)\s+(?!is\b|was\b|are\b|week\b|year\b|"
    r"month\b|morning\b|evening\b|time\b)[a-z]+|\b(?:make|keep|use|follow|do|try|take)s?\s+it\b"
    r"|\bgo(?:es)? with (?:it|that|this)\b",
    re.IGNORECASE,
)


def points_at_something(content: str) -> bool:
    """Does the statement refer to the answer instead of saying what it was?"""
    return bool(_POINTS_AT.search(content))


def _name_it(vague: list[ExtractedFact]) -> str:
    said = "; ".join(f'"{f.content}"' for f in vague)
    return (
        f"\n\nYour last answer recorded {said}. That says \"this\" or \"that\" instead "
        "of what it is. Answer again, writing out what the assistant suggested that the "
        "user took up."
    )


def adopted_only(facts: list[ExtractedFact], *, reply: str) -> list[ExtractedFact]:
    """Keep the user's own words, and only choices from the answer.

    An adopted fact is marked, so ingest never lets it reinforce what is already
    believed — an echo is not evidence — and its provenance becomes the user's
    reply, which is where the decision was made.
    """
    said = _words(reply)
    refused, hedged = is_refusal(reply), is_hedged(reply)
    kept: list[ExtractedFact] = []
    dropped = 0
    for fact in facts:
        # Hedged in its own words ("will consider", "might") it is not decided.
        fact.tentative = fact.tentative or is_hedged(fact.content)
        if _share_in(fact.content, said) >= OWN_WORDS_SHARE:
            kept.append(fact)
            continue
        if refused:
            # "No thanks" came back as "the user will not wake up at 5 am", three
            # runs in three. A no takes nothing from the answer.
            dropped += 1
            continue
        if fact.category not in CHOICE_CATEGORIES or names_a_dose(fact.content):
            # A dose or a medicine taken from an answer is health, whatever the
            # extractor filed it as: "added 5 g of creatine daily" came back as a
            # decision, three runs in three. Health is told in the user's words.
            dropped += 1
            continue
        fact.adopted = True
        fact.source_excerpt = reply.strip()
        # A hedged reply makes what it takes up pending, whatever the model said:
        # "I'll think about it" came back as a plain decision, three runs in three,
        # and a maybe stored as decided is a belief nobody holds.
        fact.tentative = fact.tentative or hedged
        kept.append(fact)
    if dropped:
        # Counts only: the facts are user content.
        log.info("agreement.dropped_from_answer", count=dropped)
    return kept


_REFUSALS = frozenset({"no", "nah", "nope", "pass"})
_HEDGES = re.compile(
    r"\b(?:maybe|perhaps|might|not sure|unsure|think about it|think it over|"
    r"we'?ll see|let me think|consider(?:s|ed|ing)?|thinking about|possibly|not yet)\b",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(
    r"^(?:what|how|why|when|where|which|who|whom|whose|should|shall|can|could|would|"
    r"will|do|does|did|is|are|was|were|am|have|has)\b",
    re.IGNORECASE,
)


def is_question(reply: str) -> bool:
    """Asked, not answered: "should I add a Sunday run?", "what about swimming".

    Any question mark counts, not only a final one: "for the event store, should
    we use Postgres or MongoDB? we need transactions" is a question. The cost is
    a turn like "sounds good! what next?", whose yes is then not taken up — the
    safe direction: nothing is stored that the person did not choose.
    """
    text = reply.strip()
    return "?" in text or bool(_QUESTION_START.match(text))


def is_refusal(reply: str) -> bool:
    """The reply opens with a no ("no thanks", "nah, too early")."""
    first = _WORD.findall(reply.lower()[:20])
    return bool(first) and first[0] in _REFUSALS


_ACCEPTS = re.compile(
    r"\b(?:yes|yeah|yep|yup|ok|okay|sure|sounds good|looks good|great|perfect|deal|"
    r"agreed|works for me|let'?s do (?:it|that|this)|let'?s go with|go with|keep (?:it|this|that)|"
    r"make (?:it|this|that)|i'?ll take|i'?ll do|i'?ll go with|use (?:it|this|that)|"
    r"the (?:first|second|third|last) one)\b",
    re.IGNORECASE,
)


def reply_kind(reply: str) -> str | None:
    """How a turn answers the answer before it: "accepted", "undecided",
    "declined", or None when it is not a reply to it at all (a question, a new
    subject). Read from the words, so the chat can keep its reply short."""
    if is_question(reply):
        return None
    if is_refusal(reply):
        return "declined"
    if is_hedged(reply):
        return "undecided"
    if _ACCEPTS.search(reply):
        return "accepted"
    return None


def is_hedged(reply: str) -> bool:
    return bool(_HEDGES.search(reply))


_DOSE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|µg|g|grams?|milligrams?|iu|ml|units?|tablets?|"
    r"pills?|capsules?|drops?)\b",
    re.IGNORECASE,
)
_SUPPLEMENTS = frozenset(
    {"creatine", "melatonin", "ashwagandha", "caffeine", "omega-3", "magnesium",
     "zinc", "iron", "probiotic", "probiotics", "collagen", "multivitamin"}
)


def names_a_dose(content: str) -> bool:
    """A number with a dose unit, a supplement, or a health word — read from the
    words, since the category is exactly what the extractor gets wrong."""
    if _DOSE.search(content):
        return True
    words = set(_WORD.findall(content.lower()))
    return bool(words & (_SUPPLEMENTS | HEALTH_WORDS))


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _FILLER}


def _share_in(content: str, said: set[str]) -> float:
    words = _words(content)
    if not words:
        return 0.0
    return sum(_stem(w) in {_stem(s) for s in said} for w in words) / len(words)


def _stem(word: str) -> str:
    """Crude on purpose: "trains"/"train", "lives"/"live", "staying"/"stay"."""
    for suffix in ("ing", "es", "s", "ed"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word
