"""Continuum's answers as text for a model to read — shared by the MCP server
and any agent that builds its own prompt (see examples/agent.py).

The shape is the point: beliefs carry how sure the record is and when each was
recorded, a dispute is marked as a dispute with both sides, and a maybe is marked
as a maybe — so a model reading it never states one side of an open
disagreement, or something undecided, as settled fact.
"""

from __future__ import annotations

from continuum_sdk.client import Memory, Recall, Remembered


def describe_recall(recall: Recall) -> str:
    """A recall as the model should read it: evidence, with its doubts marked."""
    if not (recall.memories or recall.disputes or recall.pending):
        return "Nothing remembered about this."
    lines: list[str] = []
    disputed = {m.id for d in recall.disputes for m in d.memories}
    beliefs = [r for r in recall.memories if r.memory.id not in disputed]
    if beliefs:
        lines.append("Beliefs:")
        lines += [f"- {belief(r.memory)}" for r in beliefs]
    for dispute in recall.disputes:
        about = f" about {dispute.subject}" if dispute.subject else ""
        lines.append(f"DISPUTED{about} — the record disagrees; give both sides, pick none:")
        lines += [f"- {belief(m)}" for m in dispute.memories]
    if recall.pending:
        lines.append("UNDECIDED — said as a maybe; ask about it, never state it as fact:")
        lines += [f"- {belief(m)}" for m in recall.pending]
    return "\n".join(lines)


def describe_remembered(remembered: Remembered) -> str:
    if remembered.extracted == 0:
        return "Nothing durable to remember in that."
    parts = []
    stored = len(remembered.created) - len(remembered.pending)
    if stored:
        parts.append(f"stored {stored} new")
    if remembered.pending:
        parts.append(f"{len(remembered.pending)} undecided (will be asked about)")
    if remembered.confirmed:
        parts.append(f"settled {len(remembered.confirmed)} they were deciding on")
    if remembered.reinforced:
        parts.append(f"confirmed {len(remembered.reinforced)} already known")
    if remembered.superseded:
        parts.append(f"replaced {len(remembered.superseded)} older belief(s)")
    if remembered.disputes_raised:
        parts.append(
            f"raised {len(remembered.disputes_raised)} dispute(s) for the person to decide"
        )
    created = [f"- {quote(m)}" for m in remembered.created]
    return "Remembered: " + ("; ".join(parts) or "nothing changed") + (
        "\n" + "\n".join(created) if created else ""
    )


def belief(memory: Memory) -> str:
    about = f" · {memory.subject}" if memory.subject else ""
    return (
        f"{memory.content} [{memory.category}{about} · confidence {memory.confidence:.2f} · "
        f"{memory.created_at[:10]} · id {memory.id}]"
    )


def quote(memory: Memory) -> str:
    return f"“{memory.content}”"
