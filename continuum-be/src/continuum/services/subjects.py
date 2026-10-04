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
"""

from __future__ import annotations

import re
from collections.abc import Iterable

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


def _core(slug: str) -> frozenset[str]:
    tokens = [t for t in slug.lower().replace("_", "-").split("-") if t]
    meaningful = [t for t in tokens if t not in _GENERIC]
    # A slug made only of generic words ("the-team") is its own name.
    return frozenset(meaningful or tokens)
