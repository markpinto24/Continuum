"""A maybe the person said yes to, worded as decided (FINDINGS §14).

A pending item is stored as it was said: "the user will consider adding a Sunday
long run". After the yes it is a belief, and kept in those words it reads as a
plan nobody holds — in the graph, in the chat prompt, and to the judge it is now
compared against. So the confirmed item is reworded once.

This is wording, not judgement: the person already decided, and the call only
drops the hedge. It is still a model rewriting a belief, so the result must
prove it changed nothing else — every number, name and negation kept, most of
the words kept, no hedge left — or the old wording stays. A failed rewrite costs
a clumsy sentence; a wrong one would put words in the person's mouth.
"""

from __future__ import annotations

import re

from continuum.clients.llm import LLMClient
from continuum.core.logger import get_logger
from continuum.services.agreement import is_hedged

log = get_logger(__name__)

DECIDED_SYSTEM_PROMPT = """You rewrite one statement about a plan.

The person has just said yes to it: it is decided now, no longer a maybe.
Rewrite it as decided. Keep every detail exactly - what, when, how often, every
number, name and place, and anything it replaces. Change only the words that
said it was undecided.

Respond with JSON only: {"content": "<the statement, decided>"}"""

# Hedges as phrases, removed before the words are compared: what is left is what
# the rewrite must keep. "Not sure" is a hedge; a "not" outside it is a negation.
_HEDGE_PHRASES = re.compile(
    # "may" in lower case only: "in May" is a month.
    r"\b(?:not sure(?: yet)?(?: about)?|not yet|maybe|perhaps|possibly|might|(?-i:may)|"
    r"(?:is|are|will)?\s*(?:considering|consider|thinking (?:about|of)|think about|"
    r"weighing)|unsure(?: about)?)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:[.:,]\d+)?")
_NEGATION = re.compile(r"\b(?:not|no|never|none|without)\b|n't\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9][a-z0-9'/-]*")
_FILLER = frozenset(
    {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "by",
     "is", "are", "be", "will", "their", "its", "it", "this", "that", "user", "user's",
     "team", "team's", "they", "them", "about", "do", "does"}
)

#: Share of the statement's words (hedges aside) the rewrite must keep.
KEEP_SHARE = 0.7


async def decided_wording(llm: LLMClient, content: str) -> str | None:
    """`content` worded as decided, or None to keep it as it is."""
    try:
        payload = await llm.complete_json(system=DECIDED_SYSTEM_PROMPT, user=content)
    except Exception as exc:  # noqa: BLE001
        log.warning("wording.failed", error=str(exc))
        return None
    rewritten = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(rewritten, str) or not rewritten.strip():
        log.info("wording.kept", why="no content")
        return None
    rewritten = rewritten.strip().rstrip(".")
    why = refuse(content, rewritten)
    if why:
        # The reason only: both statements are user content.
        log.info("wording.kept", why=why)
        return None
    return rewritten


def refuse(original: str, rewritten: str) -> str | None:
    """Why `rewritten` may not replace `original`, or None if it may. Pure."""
    if rewritten.strip().lower() == original.strip().rstrip(".").lower():
        return "unchanged"
    if is_hedged(rewritten) or _HEDGE_PHRASES.search(rewritten):
        return "still hedged"
    if len(rewritten) > 2 * len(original) + 40:
        return "grew"
    if sorted(_NUMBER.findall(original)) != sorted(_NUMBER.findall(rewritten)):
        return "a number changed"
    plain = _HEDGE_PHRASES.sub(" ", original)
    if len(_NEGATION.findall(plain)) != len(_NEGATION.findall(rewritten)):
        return "a negation changed"
    if not _names(original) <= _names(rewritten):
        return "a name was lost"
    kept = _words(plain)
    if kept and sum(_keeps(w, _words(rewritten)) for w in kept) / len(kept) < KEEP_SHARE:
        return "words were lost"
    return None


def _names(text: str) -> set[str]:
    """Capitalised words after the first: Lisbon, Postgres, Sundays."""
    return set(re.findall(r"\b[A-Z][A-Za-z0-9]+", text.split(" ", 1)[1])) if " " in text else set()


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _FILLER}


def _keeps(word: str, words: set[str]) -> bool:
    """Kept, give or take its ending: running / run, moving / move, adding / add."""
    stem = _stem(word)
    return any(
        (s := _stem(other)).startswith(stem) or stem.startswith(s)
        for other in words
        if min(len(stem), len(_stem(other))) >= 3
    ) or word in words


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "s", "ed"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word
