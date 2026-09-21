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

from enum import StrEnum

import structlog

from continuum.clients.llm import LLMClient
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
    ) -> None:
        self.verdict = verdict
        self.target = target
        self.reason = reason
        self.judge_confidence = judge_confidence
        # Set when a "supersedes" was downgraded to "conflict" by the gate.
        self.escalated_from = escalated_from

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Resolution {self.verdict} target={self.target.id if self.target else None}>"


JUDGE_SYSTEM_PROMPT = """You compare a NEW statement against an EXISTING memory \
and decide how they relate.

Choose exactly one relation:

- "duplicate"    : they assert the same thing. Wording may differ.
- "supersedes"   : the NEW statement replaces the EXISTING one. Use this ONLY
                   when they cannot both be true of the same subject at the same
                   time AND the new one is clearly the later position.
- "conflict"     : they appear to disagree, but you cannot tell which holds. Use
                   this when the subjects might differ, when the timing is
                   unclear, or when the new statement might be a narrower case
                   rather than a replacement.
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
- Prefer "conflict" over "supersedes" whenever you are unsure. Escalating is
  cheap; erasing a true belief is not.

Report `confidence` as your genuine certainty in the chosen relation, 0.0 to 1.0.
Be honest — low confidence is expected and useful, and a low score routes the
decision to a human instead of applying it automatically.

Respond with JSON only:
{"relation": "conflict", "confidence": 0.55, "reason": "one short sentence"}"""


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

        if best_score >= self.settings.duplicate_similarity_threshold:
            return Resolution(
                Verdict.DUPLICATE,
                target=best,
                reason=f"near-identical to existing memory (score {best_score:.3f})",
            )

        candidate = self._pick_candidate(fact, neighbours)
        if candidate is None:
            return Resolution(
                Verdict.NEW, reason="no candidate of a comparable kind in the conflict band"
            )

        # --- Paid decision: the judge --------------------------------------

        relation, confidence, reason = await self._judge(fact, candidate)

        if relation == Verdict.DUPLICATE:
            return Resolution(
                Verdict.DUPLICATE, target=candidate, reason=reason, judge_confidence=confidence
            )

        if relation == Verdict.SUPERSEDES:
            # The confidence gate. This is the guard against silently erasing a
            # belief on a coin-flip judgement.
            if confidence >= self.settings.auto_supersede_confidence:
                return Resolution(
                    Verdict.SUPERSEDES,
                    target=candidate,
                    reason=reason,
                    judge_confidence=confidence,
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
            )

        if relation == Verdict.CONFLICT:
            return Resolution(
                Verdict.CONFLICT, target=candidate, reason=reason, judge_confidence=confidence
            )

        return Resolution(
            Verdict.INDEPENDENT, target=candidate, reason=reason, judge_confidence=confidence
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

    async def _judge(
        self, fact: ExtractedFact, existing: Memory
    ) -> tuple[Verdict, float, str]:
        user_prompt = (
            f"EXISTING memory:\n"
            f'  subject: {existing.subject or "—"}\n'
            f"  category: {existing.category}\n"
            f"  recorded: {existing.created_at.date()}\n"
            f"  content: {existing.content}\n\n"
            f"NEW statement:\n"
            f'  subject: {fact.subject or "—"}\n'
            f"  category: {fact.category}\n"
            f"  content: {fact.content}"
        )

        try:
            payload = await self.llm.complete_json(
                system=JUDGE_SYSTEM_PROMPT, user=user_prompt
            )
        except Exception as exc:  # noqa: BLE001
            # A judge failure must never destroy data. Fail toward the human.
            log.error("resolution.judge_failed", error=str(exc), target_id=existing.id)
            return Verdict.CONFLICT, 0.0, "judge unavailable; escalated for safety"

        return _coerce_verdict(payload)


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
