"""Subject canonicalisation: one entity, one slug.

The subject filter in `ResolutionService._pick_candidate` is what keeps LLM calls
cheap: two memories about different subjects are never judged against each
other. That is only safe if one entity always gets the same slug. The extractor
does not guarantee it — the same project comes back as `atlas` in one note and
`atlas-project` in the next — and then the filter treats them as two entities,
and a contradiction between them is waved through as NEW without ever being
judged.

So a new subject is snapped onto an existing one when they are recognisably the
same name. The rule is deliberately lenient, because the two ways to be wrong
are not equally bad:

  * **Merging** two subjects that are really different costs one judge call:
    the pair now reaches the judge, which says "independent".
  * **Splitting** one subject into two silently switches off contradiction
    detection between them. Nothing is escalated; the stale belief just stays.

A null subject is filled from the words (`infer_subject`) when they name exactly
one subject the graph already knows. The extractor leaves the subject out often
— 8 of one user's 12 memories — and a subject-less memory was never preferred
as the candidate for its real subject: "Atlas is using MongoDB" was judged
against an unrelated subject-less memory instead of the Atlas decision. Only an
unambiguous match counts; naming two known subjects, or none, leaves it null.

Spelling is not enough: the same workout split came back as `gym` in one run and
`workout` in the next (FINDINGS §12), and `home` and `flat` share no letters.
`SubjectRelations` lets two *words* meet when their slugs mean the same thing —
embedding similarity, calibrated on `corpus/subjects.yaml` (FINDINGS §13). It
never renames: slug similarity measures relatedness, not identity (frontend /
backend scores like a synonym), so a pair that meets is only *compared*, judged on
its words exactly as a subject-less pair is. Names never meet by meaning — an
embedder cannot tell Sara from Sarah K (FINDINGS §1) — so a slug written with a
capital in the memory's own words, and any two slugs that look alike, match by
spelling alone, as before.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from continuum.core.logger import get_logger

if TYPE_CHECKING:
    from continuum.clients.llm import LLMClient
    from continuum.models.memory import ExtractedFact, Memory

log = get_logger(__name__)

# Tokens that describe the *kind* of thing rather than which thing it is.
# "acme" and "acme-corp" name the same client; "atlas" and "atlas-project" the
# same project.
_GENERIC = frozenset(
    {
        "the", "project", "proj", "corp", "corporation", "inc", "ltd", "llc", "co",
        "company", "team", "app", "application", "service", "svc", "platform",
        "system", "client", "customer", "repo", "codebase",
    }
)


def canonical_subject(candidate: str | None, known: Iterable[str]) -> str | None:
    """The existing slug `candidate` refers to, or `candidate` itself if none does.

    First-seen wins: the graph keeps the spelling it already has, so earlier
    memories never need rewriting.
    """
    if candidate is None:
        return None
    known_set = set(known)
    if candidate in known_set:
        return candidate

    core = _core(candidate)
    if not core:
        # The empty set is a subset of every set: without this, a slug that
        # reduced to nothing would "match" every subject in the graph.
        return candidate
    matches: list[tuple[tuple[int, int, int], str]] = []
    for existing in known_set:
        other = _core(existing)
        if not other:
            continue
        if core == other:
            strength = 2  # the same name once generic words are stripped
        elif core <= other or other <= core:
            strength = 1  # one is a qualified form of the other: sara / sara-chen
        else:
            continue
        # Strongest match, then most shared tokens, then the shorter slug — so
        # the choice is deterministic whatever order `known` arrives in.
        matches.append(((strength, len(core & other), -len(existing)), existing))

    if not matches:
        return candidate
    return max(matches)[1]


_WORD = re.compile(r"[a-z0-9]+")

# What a model writes when it means "no subject". Stored as a slug, "null" made
# every subject-less memory one entity — found live on Acme's budget.
PLACEHOLDER_SUBJECTS = frozenset(
    {"null", "none", "nil", "n-a", "na", "unknown", "general", "misc", "-", "--", "—"}
)


def clean_subject(value: str | None) -> str | None:
    """A subject slug, or None when it is empty or a placeholder for nothing."""
    if not value or value.strip().lower() in PLACEHOLDER_SUBJECTS:
        return None
    return value


def infer_subject(content: str, known: Iterable[str]) -> str | None:
    """The one known subject `content` names, or None.

    A subject is named when every meaningful token of its slug appears as a word
    ("atlas-project" by "Atlas"). Single short tokens ("ui", "db") are too common
    to count. Two different subjects named means it is about both, or about
    neither: no guess.
    """
    words = set(_WORD.findall(content.lower()))
    named = set()
    for slug in known:
        core = _core(slug)
        if len(core) == 1 and len(next(iter(core))) < 3:
            continue
        if core and core <= words:
            named.add(slug)
    # Drop names contained in a longer one ("sara" inside "sara-chen"), then
    # spellings of one entity ("atlas", "atlas-project") count once.
    widest = [s for s in named if not any(_core(s) < _core(o) for o in named)]
    if len({_core(s) for s in widest}) != 1:
        return None
    # Same tie-break as canonical_subject: the shorter slug.
    return min(widest, key=lambda s: (len(s), s))


def _tokens(slug: str) -> frozenset[str]:
    return frozenset(t for t in slug.lower().replace("_", "-").split("-") if t)


def _core(slug: str) -> frozenset[str]:
    tokens = [t for t in slug.lower().replace("_", "-").split("-") if t]
    meaningful = [t for t in tokens if t not in _GENERIC]
    # A slug made only of generic words ("the-team") is its own name.
    return frozenset(meaningful or tokens)


# --- Meaning: subjects that are one thing under two words -----------------

# The person themself. Every subject-less fact about the user lands here, so
# letting it meet `gym` or `sleep` by meaning would make every fact about the
# user comparable with every topic — the false disputes it already causes.
SELF_SUBJECTS = frozenset({"self", "user", "me", "myself"})

# Endings that make one word out of two spellings: allergy / allergies,
# run / running, deploy / deployment. Anything else that differs by a letter or
# two is a different name of the same shape: sara / sarah, john / jon.
_INFLECTIONS = ("ies", "es", "s", "ing", "ment", "ed", "er")


def subject_text(slug: str) -> str:
    """What is embedded for a slug. "my gym" against "my workout" separated the
    labelled pairs better than the bare slug, "the …" or "topic: …" (FINDINGS §13)."""
    return "my " + " ".join(t for t in re.split(r"[-_\s]+", slug.lower()) if t)


def looks_alike(a: str, b: str) -> bool:
    """Two slugs of the same shape naming different things.

    Either they share a word and each has one the other lacks (vitamin-d /
    vitamin-c, q3-roadmap / q4-roadmap, billing-service / search-service), or a
    word of one is a near-spelling of a word of the other that is not the same
    word inflected (sara / sarah-k, anna / ana, alice / alicia). These score
    highest of all on meaning — anna / ana at 0.95 — and are different people.
    """
    # Every word counts for a swap — "service" is generic, but billing-service
    # and search-service are still two services.
    left, right = _tokens(a), _tokens(b)
    if left & right and left - right and right - left:
        return True
    left, right = _core(a), _core(b)
    return any(_near_spelling(x, y) for x in left - right for y in right - left)


def reads_as_name(slug: str, content: str) -> bool:
    """Whether the memory's own words write this subject as a name.

    "Atlas stores events in Postgres", "Sarah K owns billing": a capital. "The
    user goes to the gym": a word. A capital at the start of a sentence counts
    too ("Gym membership renews in May"), which is wrong for that memory — and
    harmless, since a name simply matches by spelling, as every subject did
    before meaning was read.
    """
    for token in _core(slug):
        for match in re.finditer(rf"\b{re.escape(token)}\b", content, flags=re.IGNORECASE):
            if match.group(0)[0].isupper():
                return True
    return False


class SubjectRelations:
    """Which neighbours' subjects mean what the new fact's subject means.

    Slug vectors are cached for the life of the process: a graph's subjects
    change rarely, and each is embedded once rather than per ingest.
    """

    #: Slugs kept. A graph has tens to hundreds of subjects; this is ample.
    CACHE_SIZE = 4096

    def __init__(self, llm: LLMClient, threshold: float | None) -> None:
        self.llm = llm
        # None: no calibration for this embedding model, so meaning is not read
        # at all — an uncalibrated threshold is a guess (FINDINGS §2).
        self.threshold = threshold
        self._vectors: dict[str, list[float]] = {}

    async def related(self, fact: ExtractedFact, memories: Sequence[Memory]) -> frozenset[str]:
        """Ids of the memories whose different subject means the fact's subject."""
        if self.threshold is None or not fact.subject or fact.subject in SELF_SUBJECTS:
            return frozenset()
        if reads_as_name(fact.subject, fact.content):
            return frozenset()
        candidates = [
            m for m in memories
            if m.subject
            and m.subject != fact.subject
            and m.subject not in SELF_SUBJECTS
            and not looks_alike(fact.subject, m.subject)
            and not reads_as_name(m.subject, m.content)
        ]
        if not candidates:
            return frozenset()

        try:
            vectors = await self._embed([fact.subject, *{m.subject for m in candidates}])
        except Exception as exc:  # noqa: BLE001
            # Fail toward comparing: a pair compared costs a judge call; a pair
            # split never reaches anyone, and the stale belief just stays.
            log.warning("subjects.embed_failed", error=str(exc), candidates=len(candidates))
            return frozenset(m.id for m in candidates)

        own = vectors[fact.subject]
        return frozenset(
            m.id for m in candidates if _cosine(own, vectors[m.subject]) >= self.threshold
        )

    async def _embed(self, slugs: list[str]) -> dict[str, list[float]]:
        missing = [s for s in dict.fromkeys(slugs) if s not in self._vectors]
        if missing:
            for slug, vector in zip(
                missing, await self.llm.embed([subject_text(s) for s in missing]), strict=True
            ):
                self._vectors[slug] = vector
            while len(self._vectors) > self.CACHE_SIZE:
                del self._vectors[next(iter(self._vectors))]
        return {s: self._vectors[s] for s in slugs}


def _stem(word: str) -> str:
    for suffix in _INFLECTIONS:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)] + ("y" if suffix == "ies" else "")
            # running -> runn -> run
            if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiouls":
                stem = stem[:-1]
            return stem
    return word


def _near_spelling(x: str, y: str) -> bool:
    if x == y or _stem(x) == _stem(y):
        return False
    if x.startswith(y) or y.startswith(x):
        return True  # sara / sarah, sam / samuel
    distance = _edits(x, y)
    # One edit is a near-spelling (john / jon, mark / marc); two only when the
    # names start alike (alice / alicia) — home / house share two letters and
    # are one place.
    return distance <= 1 or (distance == 2 and x[:3] == y[:3])


def _edits(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 0.0 if norm == 0 else dot / norm
