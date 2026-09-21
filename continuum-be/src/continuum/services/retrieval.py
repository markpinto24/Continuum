"""Phase 3 — retrieval ranking for memory-augmented chat.

Cosine similarity alone is the wrong ranking for a belief graph. It answers
"which memory is worded most like the question", which is not the same as "which
memory should the agent rely on". Two things it cannot see:

  * **Confidence.** A belief that decayed to 0.3 because nobody has mentioned it
    in four months should not outrank one confirmed last week just because it
    shares more vocabulary with the query.
  * **Recency.** What someone is working on now is more likely to be what they
    are asking about.

So the rank is `similarity x confidence^w x recency^w`. Multiplicative, not a
weighted sum: a memory that fails badly on any one factor should fall, and a
weak similarity should not be rescued by a high confidence.

What this layer deliberately does **not** do:

  * It does not drop `contradicted` memories. They rank normally and the
    disagreement is handed to the caller — see `_find_disagreements`. An agent
    that quietly filters out the disputed side of a belief is exactly the failure
    mode Phase 2 exists to prevent.
  * It does not reinforce what it retrieves. Being retrieved is not evidence that
    a memory is still true, and treating it as such would let a stale belief keep
    itself alive purely by being well-worded.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from continuum.config import Settings, get_settings
from continuum.models.memory import Memory, MemoryStatus
from continuum.models.schemas import ChatContext, Disagreement, RetrievedMemory
from continuum.services.memory_store import MemoryStore

log = structlog.get_logger(__name__)


def rank_score(
    *,
    similarity: float,
    confidence: float,
    recency: float,
    confidence_weight: float,
    recency_weight: float,
) -> float:
    """The ranking formula. Pure, so Phase 5 can tune it against a corpus.

    Weights are exponents, so `weight = 0` disables a factor exactly (x^0 == 1)
    and `weight = 1` applies it at face value.
    """
    sim = _clamp(similarity)
    conf = _clamp(confidence)
    rec = _clamp(recency)
    return round(sim * (conf**confidence_weight) * (rec**recency_weight), 6)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class RetrievalService:
    def __init__(self, memories: MemoryStore, settings: Settings | None = None) -> None:
        self.memories = memories
        self.settings = settings or get_settings()

    async def retrieve(
        self, *, user_id: str, query: str, limit: int | None = None
    ) -> ChatContext:
        top_k = limit or self.settings.chat_memory_limit
        now = datetime.now(UTC)

        # Over-fetch on similarity, then re-rank. The top-k by final score is not
        # a subset of the top-k by cosine, so fetching only k would silently cap
        # what confidence and recency are able to promote.
        candidates = await self.memories.search(
            user_id=user_id,
            query=query,
            limit=top_k * self.settings.chat_candidate_multiplier,
            score_threshold=self.settings.chat_min_similarity,
        )

        scored: list[RetrievedMemory] = []
        for memory, similarity in candidates:
            recency = memory.recency_weight(
                now=now,
                half_life_days=self.settings.retrieval_recency_half_life_days,
                floor=self.settings.retrieval_recency_floor,
            )
            scored.append(
                RetrievedMemory(
                    memory=memory,
                    similarity=round(similarity, 6),
                    recency=recency,
                    score=rank_score(
                        similarity=similarity,
                        confidence=memory.confidence,
                        recency=recency,
                        confidence_weight=self.settings.retrieval_confidence_weight,
                        recency_weight=self.settings.retrieval_recency_weight,
                    ),
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        selected = scored[:top_k]

        disagreements = await self._find_disagreements(
            user_id=user_id, selected=[item.memory for item in selected]
        )

        log.info(
            "retrieval.completed",
            candidates=len(candidates),
            selected=len(selected),
            disagreements=len(disagreements),
            top_score=selected[0].score if selected else None,
        )

        return ChatContext(query=query, memories=selected, disagreements=disagreements)

    # --- Disagreement assembly ---------------------------------------------

    async def _find_disagreements(
        self, *, user_id: str, selected: list[Memory]
    ) -> list[Disagreement]:
        """Pair up every contradicted memory in the result with its counterparts.

        The other side of a contradiction often does not survive the same query —
        it is worded differently, which is usually why the two disagree in the
        first place. So it is fetched by id rather than hoped for, otherwise the
        agent would report one half of a dispute as settled fact.
        """
        contradicted = [m for m in selected if m.status is MemoryStatus.CONTRADICTED]
        if not contradicted:
            return []

        wanted = sorted({cid for m in contradicted for cid in m.conflicts_with})
        lookup = await self.memories.resolve_ids(wanted)

        groups: list[Disagreement] = []
        seen: set[frozenset[str]] = set()
        for memory in contradicted:
            others = [
                lookup[cid]
                for cid in memory.conflicts_with
                if cid in lookup and lookup[cid].user_id == user_id
            ]
            if not others:
                continue
            key = frozenset({memory.id, *(o.id for o in others)})
            if key in seen:
                continue  # conflicts are symmetric; report each dispute once
            seen.add(key)
            groups.append(
                Disagreement(
                    subject=memory.subject,
                    memories=[memory, *others],
                )
            )
        return groups
