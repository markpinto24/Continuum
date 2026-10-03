"""Keyword matching for retrieval: the words embeddings are worst at.

An embedding of "what does Atlas run on?" lands near anything about databases,
and a memory that is mostly about something else but names Atlas can fall
outside the cosine band entirely — the same blindness to names and numbers the
duplicate guard exists for (FINDINGS §1). Matching the words catches what
similarity misses.

Pure: tokenising and scoring are testable without Qdrant.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9][a-z0-9_-]*")

# Function words, plus the words questions are made of. Matching "what" or
# "does" says nothing about whether a memory is relevant.
STOPWORDS = frozenset(
    {
        "a", "about", "after", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be",
        "been", "before", "being", "but", "by", "can", "could", "did", "do", "does", "doing",
        "for", "from", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his",
        "how", "i", "if", "in", "into", "is", "it", "its", "just", "know", "me", "more", "most",
        "my", "no", "not", "now", "of", "on", "or", "our", "ours", "out", "over", "please", "she",
        "should", "so", "some", "tell", "than", "that", "the", "their", "theirs", "them", "then",
        "there", "these", "they", "this", "those", "to", "too", "under", "up", "us", "very", "was",
        "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will",
        "with", "would", "you", "your", "yours",
    }
)


def terms(text: str) -> list[str]:
    """Distinct content words in order, lowercased."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(text.lower()):
        word = word.strip("-_")
        if len(word) > 1 and word not in STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)


def keyword_score(query_terms: list[str], text: str) -> float:
    """Share of the query's content words present in `text`, 0..1.

    Longer words count for more: "postgres" matching is stronger evidence than
    "db". Weighting by length is crude, but needs no corpus statistics, which a
    per-user graph of a few hundred memories cannot supply reliably anyway.
    """
    if not query_terms:
        return 0.0
    present = set(terms(text))
    total = sum(len(t) for t in query_terms)
    hit = sum(len(t) for t in query_terms if t in present)
    return round(hit / total, 4)
