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

from continuum.config import Settings, get_settings
from continuum.core.logger import get_logger
from continuum.models.memory import SHARED_SPACE, Memory, MemoryStatus, visible_owners
from continuum.models.schemas import ChatContext, Disagreement, RetrievedMemory
from continuum.services.memory_store import MemoryStore

log = get_logger(__name__)


def rank_score(
    *,
    similarity: float,
    confidence: float,
    recency: float,
    confidence_weight: float,
    recency_weight: float,
    keyword: float = 0.0,
    keyword_weight: float = 0.0,
) -> float:
    """The ranking formula. Pure, so Phase 5 can tune it against a corpus.

    Weights are exponents, so `weight = 0` disables a factor exactly (x^0 == 1)
    and `weight = 1` applies it at face value.

    Keyword match is an alternative route to relevance, not a bonus: the
    relevance term is whichever of similarity and weighted keyword is higher, so a
    memory found both ways is not counted twice.
    """
    sim = max(_clamp(similarity), _clamp(keyword) * max(0.0, keyword_weight))
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
        self,
        *,
        user_id: str,
        query: str,
        limit: int | None = None,
        as_of: datetime | None = None,
    ) -> ChatContext:
        """Rank memories for a question — as things stand, or as they stood at
        `as_of`: then every status is searched, and only what was believed at that
        moment is kept (`Memory.believed_at`), ranked for recency as of then."""
        top_k = limit or self.settings.chat_memory_limit
        now = datetime.now(UTC)
        if as_of is not None:
            as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
            as_of = None if as_of >= now else as_of
        statuses = list(MemoryStatus) if as_of else None
        if as_of:
            now = as_of

        # The user's own memories and the team's shared ones. Resolution stays
        # per graph; reading is across both.
        owners = visible_owners(user_id, shared=self.settings.shared_space_enabled)

        # Over-fetch on similarity, then re-rank. The top-k by final score is not
        # a subset of the top-k by cosine, so fetching only k would silently cap
        # what confidence and recency are able to promote.
        pool = top_k * self.settings.chat_candidate_multiplier
        candidates = await self.memories.search(
            user_id=owners,
            query=query,
            # The past is searched in full, so fetch more: later beliefs compete.
            limit=pool * (3 if as_of else 1),
            score_threshold=self.settings.chat_min_similarity,
            statuses=statuses,
        )
        # Keyword candidates join the pool even below the similarity floor — the
        # floor is exactly where a memory naming the right entity gets lost.
        keyword_hits: dict[str, float] = {}
        if self.settings.retrieval_keyword_weight > 0:
            by_keyword = await self.memories.keyword_memories(
                user_id=owners, query=query, limit=pool, statuses=statuses
            )
            keyword_hits = {m.id: score for m, score in by_keyword}
            known = {m.id for m, _ in candidates}
            candidates = candidates + [(m, 0.0) for m, _ in by_keyword if m.id not in known]

        if as_of:
            candidates = [(m, s) for m, s in candidates if m.believed_at(as_of)]

        scored: list[RetrievedMemory] = []
        for memory, similarity in candidates:
            keyword = keyword_hits.get(memory.id, 0.0)
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
                    keyword=keyword,
                    score=rank_score(
                        similarity=similarity,
                        confidence=memory.confidence,
                        recency=recency,
                        confidence_weight=self.settings.retrieval_confidence_weight,
                        recency_weight=self.settings.retrieval_recency_weight,
                        keyword=keyword,
                        keyword_weight=self.settings.retrieval_keyword_weight,
                    ),
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        selected = scored[:top_k]

        disagreements = await self._find_disagreements(
            owners=owners, selected=[item.memory for item in selected], as_of=as_of
        )

        log.info(
            "retrieval.completed",
            candidates=len(candidates),
            selected=len(selected),
            disagreements=len(disagreements),
            top_score=selected[0].score if selected else None,
        )

        return ChatContext(
            query=query, memories=selected, disagreements=disagreements, as_of=as_of
        )

    # --- Disagreement assembly ---------------------------------------------

    async def _find_disagreements(
        self, *, owners: list[str], selected: list[Memory], as_of: datetime | None = None
    ) -> list[Disagreement]:
        """Pair up every contradicted memory in the result with its counterparts.

        The other side of a contradiction often does not survive the same query —
        it is worded differently, which is usually why the two disagree in the
        first place. So it is fetched by id rather than hoped for, otherwise the
        agent would report one half of a dispute as settled fact.
        """
        contradicted = [m for m in selected if m.status is MemoryStatus.CONTRADICTED]
        # A team belief someone privately disagrees with is not flagged itself
        # (a private fact never marks team knowledge), so look for the private
        # side — only in this user's own graph.
        shared = [m.id for m in selected if m.is_shared]
        private_owner = next((o for o in owners if o != SHARED_SPACE), None)
        if shared and private_owner:
            known = {m.id for m in contradicted}
            for other in await self.memories.referencing(shared, user_id=private_owner):
                if other.id not in known and set(other.team_conflicts_with) & set(shared):
                    contradicted.append(other)
                    known.add(other.id)
        if not contradicted:
            return []

        wanted = sorted({
            cid for m in contradicted for cid in m.conflicts_with + m.team_conflicts_with
        })
        lookup = await self.memories.resolve_ids(wanted)

        groups: list[Disagreement] = []
        seen: set[frozenset[str]] = set()
        for memory in contradicted:
            others = [
                lookup[cid]
                for cid in memory.conflicts_with + memory.team_conflicts_with
                if cid in lookup
                and lookup[cid].user_id in owners
                and (as_of is None or lookup[cid].believed_at(as_of))
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
