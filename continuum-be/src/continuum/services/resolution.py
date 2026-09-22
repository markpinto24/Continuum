"""Phase 2 — the resolution layer.

This is the part that actually addresses the unsolved problem. Given a newly
extracted fact and the existing memories nearest to it, decide what happens to
the belief graph.

Design principles, in priority order:

1. **Never silently lose a belief.** Superseding writes an edge; it never
   deletes. The old memory stays queryable forever.

2. **Escalate ambiguity instead of guessing.** A second LLM call is not a
   trustworthy arbiter of a genuine contradiction. When the judge is not
   confident, the pair goes to a human inbox rather than being auto-resolved.
   `auto_supersede_confidence` is the dial.

3. **Spend LLM calls only where they can change the answer.** Category policy
   and cosine bands resolve most cases for free. The judge is invoked only for
   the narrow band where the outcome is genuinely uncertain.

4. **History is append-only.** An `event` can never be superseded — two things
   can both have happened.
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import NamedTuple, TypeVar

import structlog

from continuum.clients.llm import LLMClient, TokenLogprob
from continuum.config import Settings, get_settings
from continuum.models.memory import ExtractedFact, Memory, MemoryCategory
from continuum.services.memory_store import MemoryStore

log = structlog.get_logger(__name__)


class Verdict(StrEnum):
    """What the resolver decided to do with a candidate fact."""

    NEW = "new"                  # nothing close enough; store it
    DUPLICATE = "duplicate"      # already known; reinforce instead
    SUPERSEDES = "supersedes"    # replaces an existing memory; write the edge
    CONFLICT = "conflict"        # genuine ambiguity; escalate to a human
    INDEPENDENT = "independent"  # related but both can be true; store it


class Resolution:
    """The outcome of resolving one fact against the existing graph."""

    def __init__(
        self,
        verdict: Verdict,
        *,
        target: Memory | None = None,
        reason: str = "",
        judge_confidence: float | None = None,
        escalated_from: Verdict | None = None,
        confidence_source: str | None = None,
        self_reported_confidence: float | None = None,
        forced_escalation: bool = False,
    ) -> None:
        self.verdict = verdict
        self.target = target
        self.reason = reason
        self.judge_confidence = judge_confidence
        # Set when a "supersedes" was downgraded to "conflict" by the gate.
        self.escalated_from = escalated_from
        # "logprob" when judge_confidence is the model's probability over the
        # four relations; "self_report" when it is the number the model wrote.
        self.confidence_source = confidence_source
        self.self_reported_confidence = self_reported_confidence
        # True when policy escalated a supersede regardless of any confidence —
        # so an evaluation replay must not promote it at a lower gate.
        self.forced_escalation = forced_escalation

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Resolution {self.verdict} target={self.target.id if self.target else None}>"


JUDGE_SYSTEM_PROMPT = """You compare a NEW statement against an EXISTING memory \
and decide how they relate.

The NEW statement was recorded just now, after the EXISTING memory. It is always
the later of the two, so "which came first" is never a reason to be unsure.

Choose exactly one relation:

- "duplicate"    : they assert the same thing. Wording may differ.
- "supersedes"   : the NEW statement replaces the EXISTING one outright. They
                   cannot both be true of the same subject at once, and nothing
                   the EXISTING memory claimed survives the change.
- "conflict"     : part of the EXISTING memory may still hold. Use this when the
                   subjects might differ, or when the NEW statement narrows,
                   qualifies or carves an exception out of the EXISTING one
                   rather than replacing it.
- "independent"  : both can be true at once. Different subjects, different
                   aspects, or complementary detail.

Rules:
- Two things that happened at different times are "independent", never
  "supersedes". History does not get overwritten.
- A statement about a DIFFERENT subject is "independent", even if the wording is
  very similar.
- A statement that narrows or qualifies the existing one ("Postgres stays for
  reporting only") is "conflict", not "supersedes" — a human should decide how
  much of the original survives.
- Prefer "conflict" over "supersedes" whenever you are unsure whether any part
  of the EXISTING memory survives. Escalating is cheap; erasing a true belief
  is not.

Report `confidence` as your genuine certainty in the chosen relation, 0.0 to 1.0.
Be honest — low confidence is expected and useful, and a low score routes the
decision to a human instead of applying it automatically.

Respond with JSON only, filling in every field for THESE two statements. Write
the reason first, then choose the relation that reason supports:
{"reason": "<one sentence about these two statements>",
 "relation": "<duplicate|supersedes|conflict|independent>",
 "confidence": <number from 0.0 to 1.0>}"""

# The response template above deliberately contains no concrete answer. It used
# to be a worked example — {"relation": "conflict", "confidence": 0.55, "reason":
# "one short sentence"} — and qwen2.5:7b copied it verbatim, placeholder reason
# and all, on a third of its calls, including the clearest reversals in the
# corpus. A small model anchors on any example answer it is shown; a template
# with placeholders gives it the shape without handing it a verdict.
#
# Two more choices, both measured on the Phase 5 corpus (FINDINGS §9):
#
#   * Reason first, then relation. The relation token is then conditioned on the
#     model's own reasoning; belief loss went to zero and judge agreement rose
#     from 50% to 61%. Token probabilities did not collapse to 1.0 — the gate
#     still has something to act on.
#   * "The NEW statement is always later." It is, by construction: it is being
#     ingested now. The judge was never told, and its definition of supersede
#     required the new statement to be "clearly the later position" — so on the
#     clearest reversals it wrote "without clear indication of which is later"
#     and escalated. Conflict now means partial overlap, not unknown order.


class ResolutionService:
    def __init__(
        self,
        memories: MemoryStore,
        llm: LLMClient,
        settings: Settings | None = None,
    ) -> None:
        self.memories = memories
        self.llm = llm
        self.settings = settings or get_settings()

    # --- Entry point -------------------------------------------------------

    async def resolve(
        self,
        fact: ExtractedFact,
        neighbours: list[tuple[Memory, float]],
    ) -> Resolution:
        """Decide what to do with `fact` given its nearest existing memories."""
        if not neighbours:
            return Resolution(Verdict.NEW, reason="no similar memories")

        best, best_score = neighbours[0]

        # --- Free decisions: no LLM call needed ----------------------------

        withheld: set[str] = set()
        if best_score >= self.settings.duplicate_similarity_threshold:
            withheld = material_difference(fact.content, best.content)
            if not withheld:
                return Resolution(
                    Verdict.DUPLICATE,
                    target=best,
                    reason=f"near-identical to existing memory (score {best_score:.3f})",
                )
            # A near-identical score with a changed number, date, name or
            # negation is the shape of a contradiction, not of a duplicate.
            # DUPLICATE is the one verdict that discards the incoming fact, so
            # it is withheld and the pair is judged like any other candidate.
            # Count only: the tokens are user content and must not be logged.
            log.info(
                "resolution.duplicate_withheld",
                target_id=best.id,
                similarity=round(best_score, 4),
                differences=len(withheld),
            )

        resolution = await self._resolve_candidate(fact, neighbours)
        if withheld:
            resolution.reason = (
                f"{resolution.reason} [scored {best_score:.3f} against an existing memory, "
                f"but {', '.join(sorted(withheld))} differ]"
            ).strip()
        return resolution

    async def _resolve_candidate(
        self,
        fact: ExtractedFact,
        neighbours: list[tuple[Memory, float]],
    ) -> Resolution:
        candidate = self._pick_candidate(fact, neighbours)
        if candidate is None:
            return Resolution(
                Verdict.NEW, reason="no candidate of a comparable kind in the conflict band"
            )

        # --- Paid decision: the judge --------------------------------------

        judgement = await self._judge(fact, candidate)
        relation, confidence, reason = judgement.relation, judgement.confidence, judgement.reason
        provenance = {
            "confidence_source": judgement.source,
            "self_reported_confidence": judgement.self_reported,
        }

        if relation == Verdict.DUPLICATE:
            return Resolution(
                Verdict.DUPLICATE,
                target=candidate,
                reason=reason,
                judge_confidence=confidence,
                **provenance,
            )

        if relation == Verdict.SUPERSEDES:
            if (
                self.settings.person_supersede_requires_change_language
                and is_role_statement(fact, candidate)
                and not states_a_change(fact.content)
            ):
                return Resolution(
                    Verdict.CONFLICT,
                    target=candidate,
                    reason=(
                        f"{reason} (a new name for a role that is often shared, and nothing "
                        "says the earlier person stopped — escalated)"
                    ),
                    judge_confidence=confidence,
                    escalated_from=Verdict.SUPERSEDES,
                    forced_escalation=True,
                    **provenance,
                )

            # The confidence gate. This is the guard against silently erasing a
            # belief on a coin-flip judgement.
            if confidence >= self.settings.auto_supersede_confidence:
                return Resolution(
                    Verdict.SUPERSEDES,
                    target=candidate,
                    reason=reason,
                    judge_confidence=confidence,
                    **provenance,
                )
            log.info(
                "resolution.escalated",
                judge_confidence=confidence,
                gate=self.settings.auto_supersede_confidence,
                target_id=candidate.id,
            )
            return Resolution(
                Verdict.CONFLICT,
                target=candidate,
                reason=f"{reason} (judge confidence {confidence:.2f} below auto-resolve gate)",
                judge_confidence=confidence,
                escalated_from=Verdict.SUPERSEDES,
                **provenance,
            )

        if relation == Verdict.CONFLICT:
            return Resolution(
                Verdict.CONFLICT,
                target=candidate,
                reason=reason,
                judge_confidence=confidence,
                **provenance,
            )

        return Resolution(
            Verdict.INDEPENDENT,
            target=candidate,
            reason=reason,
            judge_confidence=confidence,
            **provenance,
        )

    # --- Candidate selection ----------------------------------------------

    def _pick_candidate(
        self, fact: ExtractedFact, neighbours: list[tuple[Memory, float]]
    ) -> Memory | None:
        """Cheap filters that decide most cases without an LLM call.

        A memory is only worth judging if it could plausibly be replaced by the
        new fact: same category, mutable, and — when both name a subject — the
        same subject.
        """
        for memory, score in neighbours:
            if score < self.settings.conflict_similarity_threshold:
                continue
            if memory.is_immutable or fact.category in {MemoryCategory.EVENT}:
                continue  # history is append-only
            if memory.category is not fact.category:
                continue  # a preference never replaces a decision
            if fact.subject and memory.subject and fact.subject != memory.subject:
                continue  # same wording, different entity
            return memory
        return None

    # --- The judge ---------------------------------------------------------

    async def _judge(self, fact: ExtractedFact, existing: Memory) -> Judgement:
        user_prompt = (
            f"EXISTING memory:\n"
            f'  subject: {existing.subject or "—"}\n'
            f"  category: {existing.category}\n"
            f"  recorded: {existing.created_at.date()}\n"
            f"  content: {existing.content}\n\n"
            f"NEW statement:\n"
            f'  subject: {fact.subject or "—"}\n'
            f"  category: {fact.category}\n"
            f"  recorded: just now, after the EXISTING memory\n"
            f"  content: {fact.content}"
        )

        try:
            payload, raw, tokens = await self.llm.complete_json_with_logprobs(
                system=JUDGE_SYSTEM_PROMPT, user=user_prompt
            )
        except Exception as exc:  # noqa: BLE001
            # A judge failure must never destroy data. Fail toward the human.
            log.error("resolution.judge_failed", error=str(exc), target_id=existing.id)
            return Judgement(Verdict.CONFLICT, 0.0, "judge unavailable; escalated for safety")

        relation, self_reported, reason = _coerce_verdict(payload)
        probabilities = relation_probabilities(raw, tokens) if tokens else None
        token_confidence = probabilities.get(relation) if probabilities else None

        if token_confidence is None:
            # Provider without logprobs, or an answer we could not locate in the
            # token stream. The written number is all there is; say so.
            return Judgement(relation, self_reported, reason, "self_report", self_reported)

        log.info(
            "resolution.judged",
            relation=relation,
            token_confidence=round(token_confidence, 4),
            self_reported=self_reported,
        )
        return Judgement(relation, token_confidence, reason, "logprob", self_reported)


class Judgement(NamedTuple):
    relation: Verdict
    confidence: float
    reason: str
    source: str = "self_report"
    self_reported: float | None = None


# First letters are unique across the four relations the judge may return, so a
# token's first letter is enough to say which relation it starts.
_RELATION_BY_INITIAL = {
    "s": Verdict.SUPERSEDES,
    "c": Verdict.CONFLICT,
    "d": Verdict.DUPLICATE,
    "i": Verdict.INDEPENDENT,
}
_RELATION_KEY = re.compile(r'"(?:relation|verdict)"\s*:\s*"')


T = TypeVar("T")


def relation_probabilities(raw: str, tokens: list[TokenLogprob]) -> dict[Verdict, float] | None:
    """The model's probability for each of the four relations. See _value_probabilities."""
    return _value_probabilities(raw, tokens, _RELATION_KEY, _RELATION_BY_INITIAL)


def _value_probabilities(
    raw: str,
    tokens: list[TokenLogprob],
    key_pattern: re.Pattern[str],
    by_initial: dict[str, T],
) -> dict[T, float] | None:
    """The model's probability for each relation, read where it names one.

    Why not trust the `confidence` it writes: qwen2.5:7b wrote a handful of
    canned values, and every single `supersedes` came back at exactly 0.95, so
    the confidence gate could not move (FINDINGS §3). The probability the model
    assigned to each relation at the moment it chose one is a real, continuous
    measurement of the same thing.

    Finds the token where the relation's *value* begins and sums the probability
    of every alternative at that position by which relation it starts. Handles a
    token that straddles the opening quote (` "sup`) by matching alternatives on
    the same prefix. Mass on tokens that start no relation (a stray language,
    whitespace) is left unassigned rather than guessed at. Returns None when the
    value cannot be located — the caller then falls back to the written number.
    """
    joined = "".join(t.token for t in tokens)
    match = key_pattern.search(joined)
    if not match:
        return None
    value_start = match.end()

    offset = 0
    for token in tokens:
        start, end = offset, offset + len(token.token)
        offset = end
        if not start <= value_start < end:
            continue

        prefix = joined[start:value_start]
        mass: dict[T, float] = {}
        alternatives = token.top or ((token.token, token.logprob),)
        for text, logprob in alternatives:
            if not text.startswith(prefix):
                continue
            rest = text[len(prefix):].lstrip().lower()
            answer = by_initial.get(rest[:1])
            if answer is not None:
                mass[answer] = mass.get(answer, 0.0) + math.exp(logprob)
        return mass or None
    return None


def _coerce_verdict(payload: object) -> tuple[Verdict, float, str]:
    """Parse judge output defensively; anything unrecognised escalates."""
    if not isinstance(payload, dict):
        return Verdict.CONFLICT, 0.0, "unparseable judge response"

    raw = str(payload.get("relation") or payload.get("verdict") or "").strip().lower()
    try:
        relation = Verdict(raw)
    except ValueError:
        return Verdict.CONFLICT, 0.0, f"unknown relation {raw!r}; escalated"

    if relation is Verdict.NEW:  # not a relation the judge may return
        relation = Verdict.INDEPENDENT

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(payload.get("reason") or "").strip()[:300]
    return relation, confidence, reason


# --- The duplicate guard -----------------------------------------------------

# Words that say a statement's truth flipped. Kept out of the stop list on
# purpose: "Acme wants weekly calls" / "Acme does not want weekly calls" differ
# only in function words, and the negation is the whole difference.
_NEGATIONS = frozenset(
    {
        "not", "no", "never", "none", "nothing", "neither", "nor", "without",
        "cannot", "can't", "don't", "doesn't", "didn't", "won't", "isn't", "aren't",
        "wasn't", "weren't", "shouldn't", "stop", "stops", "stopped", "longer",
    }
)
_CALENDAR = frozenset(
    {
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december", "jan", "feb", "mar", "apr",
        "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "monday", "tuesday",
        "wednesday", "thursday", "friday", "saturday", "sunday", "mon", "tue", "tues",
        "wed", "thu", "thur", "thurs", "fri", "sat", "sun", "q1", "q2", "q3", "q4",
    }
)
# Quantities and cadences written as words. "Ship every two weeks" -> "ship every
# week" differs by no digit at all, and without these it went straight through
# the duplicate shortcut (held-out case `release-cadence`, FINDINGS §10).
_QUANTITY = frozenset(
    {
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "twenty", "thirty", "fifty", "hundred", "thousand",
        "million", "billion", "half", "double", "twice", "once", "dozen", "single",
        "daily", "weekly", "fortnightly", "biweekly", "monthly", "quarterly", "yearly",
        "annually", "hourly", "hour", "hours", "day", "days", "week", "weeks", "month",
        "months", "year", "years", "minute", "minutes",
    }
)
# Words people write when something changed. Present on one side only, they are
# the statement saying it is not a restatement.
_CHANGE_MARKERS = frozenset(
    {"now", "instead", "anymore", "moved", "switched", "changed", "replaced", "formerly",
     "previously", "dropped", "until"}
)
# Function words that are capitalised only because they start a sentence.
_CAPITALISED_NOISE = frozenset(
    {
        "the", "a", "an", "this", "that", "these", "those", "it", "its", "we", "our",
        "they", "their", "he", "she", "his", "her", "i", "you", "your", "and", "or",
        "but", "so", "then", "also", "in", "on", "at", "for", "to", "of", "with", "by",
        "from", "as", "is", "are", "was", "were", "be", "been", "has", "have", "had",
        "will", "would", "all", "every", "each", "there", "here",
    }
)
_WORD = re.compile(r"[A-Za-z0-9$€£][\w$€£%.+#/'-]*")


def material_difference(left: str, right: str) -> set[str]:
    """Tokens that differ between two statements AND can flip what they mean.

    Embeddings are measurably bad at exactly these: nomic-embed-text scores
    "Postgres" vs "MongoDB" at a cosine of 1.0000, and all-minilm puts "capped at
    5k" vs "capped at 8k" (0.967) above a genuine paraphrase (0.939). So no
    similarity threshold can separate a duplicate from a contradiction that
    changes one number or one name (FINDINGS §1-2). This looks at the words.

    A token is material when it appears on one side only and is a number (in
    digits or words), a date or cadence word, a negation, a change marker
    ("now", "instead"), or capitalised (a name, a product, a service). An empty
    result means the statements differ only in ordinary vocabulary.

    It is deliberately over-eager: a capitalised sentence-opening verb ("Chose
    Postgres..." vs "Postgres was picked...") counts as a difference. The cost of
    that mistake is one judge call; the cost of the opposite mistake is a
    discarded contradiction. Fail toward the judge.
    """
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    differing = left_tokens.keys() ^ right_tokens.keys()
    originals = {**right_tokens, **left_tokens}
    return {originals[norm] for norm in differing if _is_material(norm, originals[norm])}


# Words with which a statement says an earlier arrangement ENDED. Checked on the
# new statement alone: for a person's role, the text has to say so.
_TRANSITIONS = frozenset(
    {
        "now", "instead", "anymore", "longer", "replaced", "replaces", "replacing",
        "took", "taken", "takes", "moved", "left", "leaves", "stepped", "handed",
        "handover", "succeeded", "succeeds", "successor", "previously", "formerly",
        "former", "new", "stopped", "stops", "ended", "transferred",
    }
)


# Words that make a statement about who holds a role. Deliberately not "runs"
# ("the billing service runs on ECS" is not a role).
_ROLES = frozenset(
    {
        "owns", "own", "owned", "owner", "owners", "ownership", "maintains", "maintain",
        "maintained", "maintainer", "maintainers", "reviews", "review", "reviewer",
        "reviewers", "leads", "lead", "led", "manages", "manage", "managed", "manager",
        "heads", "oversees", "responsible", "assigned", "contact", "contacts",
        "rotation", "on-call", "oncall", "approver", "approves", "steward", "sponsor",
        "dri", "handles", "point",
    }
)


def is_role_statement(fact: ExtractedFact, existing: Memory) -> bool:
    """Is this pair about who holds a role — whatever category it was filed under?

    Keyed on words, not on `category == person`, because the extractor files
    "Sara owns the billing service" as `fact` (its own definition of fact lists
    "ownership"), and a category-keyed rule never saw the live case at all
    (FINDINGS §10). Both statements must describe a role, so a database swap or
    a budget change is never caught by this.
    """
    if MemoryCategory.PERSON in {fact.category, existing.category}:
        return True
    return _describes_a_role(fact.content) and _describes_a_role(existing.content)


def _describes_a_role(text: str) -> bool:
    return any(token in _ROLES for token in _tokens(text))


def states_a_change(text: str) -> bool:
    """Does this statement say that something before it stopped being true?

    Why a word list and not the model: asked directly whether "Raj owns the
    billing service" leaves room for Sara still owning it, qwen2.5:7b answered
    "no, ownership can only be held by one person at a time" — at probability
    1.00, with owners explicitly listed as shareable in its instructions
    (FINDINGS §10). The genuine handovers in the corpus all say so in words:
    "has taken over from", "no longer", "now leads", "moved off".
    """
    return any(token in _TRANSITIONS for token in _tokens(text))


def _tokens(text: str) -> dict[str, str]:
    """Normalised token -> first original spelling, sentence punctuation stripped."""
    out: dict[str, str] = {}
    for raw in _WORD.findall(text):
        token = raw.rstrip(".,;:!?'")
        if token:
            out.setdefault(token.lower(), token)
    return out


def _is_material(norm: str, original: str) -> bool:
    if any(ch.isdigit() for ch in norm) or any(ch in norm for ch in "$€£%"):
        return True
    if norm in _NEGATIONS or norm in _CALENDAR or norm in _QUANTITY or norm in _CHANGE_MARKERS:
        return True
    # "two-week", "5-day": a compound carrying a quantity or unit is one.
    if "-" in norm and any(part in _QUANTITY for part in norm.split("-")):
        return True
    return any(ch.isupper() for ch in original) and norm not in _CAPITALISED_NOISE

