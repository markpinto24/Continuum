"""Tests for the pure, dependency-free logic.

Everything here runs without Qdrant or an LLM, which is deliberate: the parts
most likely to break are the ones absorbing messy model output, and those should
be testable in milliseconds.
"""

from __future__ import annotations

import pytest

from continuum.clients.llm import LLMError, parse_json
from continuum.models.memory import Memory, MemoryCategory, MemoryStatus
from continuum.services.extraction import _coerce_facts

# --- parse_json ------------------------------------------------------------


def test_parse_json_plain():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_markdown_fenced():
    raw = 'Here you go:\n```json\n{"memories": []}\n```'
    assert parse_json(raw) == {"memories": []}


def test_parse_json_with_leading_prose():
    raw = 'Sure! {"memories": [{"content": "x"}]}'
    assert parse_json(raw)["memories"][0]["content"] == "x"


def test_parse_json_raises_on_garbage():
    with pytest.raises(LLMError):
        parse_json("no json at all here")


# --- fact coercion ---------------------------------------------------------


def test_coerce_facts_full_shape():
    payload = {
        "memories": [
            {
                "content": "Chose Postgres over Mongo for relational integrity",
                "category": "decision",
                "subject": "Billing Service",
                "source_excerpt": "we went with Postgres",
            }
        ]
    }
    facts = _coerce_facts(payload)
    assert len(facts) == 1
    assert facts[0].category is MemoryCategory.DECISION
    assert facts[0].subject == "billing-service"  # normalised


def test_coerce_facts_tolerates_bare_strings():
    facts = _coerce_facts(["Sara owns the data platform"])
    assert facts[0].content == "Sara owns the data platform"
    assert facts[0].category is MemoryCategory.FACT


def test_coerce_facts_unknown_category_falls_back():
    facts = _coerce_facts({"memories": [{"content": "x", "category": "vibes"}]})
    assert facts[0].category is MemoryCategory.FACT


def test_coerce_facts_drops_empty_content():
    assert _coerce_facts({"memories": [{"content": "  "}, {"category": "fact"}]}) == []


# --- memory lifecycle ------------------------------------------------------


def test_reinforce_raises_confidence_and_caps_at_one():
    m = Memory(user_id="u", content="x", confidence=0.98)
    m.reinforce(0.05)
    assert m.confidence == 1.0
    assert m.reinforcement_count == 1


def test_supersede_keeps_the_edge_instead_of_deleting():
    old = Memory(user_id="u", content="Client prefers calls")
    new = Memory(user_id="u", content="Client prefers async updates", supersedes=[old.id])
    old.mark_superseded_by(new.id)

    assert old.status is MemoryStatus.SUPERSEDED
    assert old.superseded_by == new.id
    assert new.supersedes == [old.id]  # belief history is traversable both ways


def test_events_are_immutable():
    assert Memory(user_id="u", content="Shipped v2", category=MemoryCategory.EVENT).is_immutable
    assert not Memory(user_id="u", content="x", category=MemoryCategory.DECISION).is_immutable


# --- logging redaction -----------------------------------------------------


def test_secrets_are_redacted_in_every_environment():
    from continuum.core.logging import redact_secrets

    out = redact_secrets(None, "info", {
        "event": "llm.call",
        "api_key": "sk-live-abc123",
        "Authorization": "Bearer xyz",
        "model": "qwen2.5:7b-instruct",
    })
    assert out["api_key"] == "<redacted>"
    assert out["Authorization"] == "<redacted>"
    assert out["model"] == "qwen2.5:7b-instruct"  # non-secrets untouched


def test_user_content_is_fingerprinted_not_dropped():
    """Same text must produce the same hash, so lines stay correlatable."""
    from continuum.core.logging import redact_user_content

    a = redact_user_content(None, "info", {"content": "Acme prefers async updates"})
    b = redact_user_content(None, "info", {"content": "Acme prefers async updates"})
    assert a["content"] == b["content"]
    assert "Acme" not in a["content"]
    assert "len=26" in a["content"]
