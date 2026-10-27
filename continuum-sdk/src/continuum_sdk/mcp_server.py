"""Continuum as an MCP server: long-term memory for any MCP client.

    CONTINUUM_API_KEY=ck_... continuum-mcp          # stdio; CONTINUUM_URL optional

Four tools — remember, recall, list_disputes, settle — each one call to the API
through `AsyncContinuum`. What they return is text written for the model that
reads it: beliefs with their confidence and date, disputes marked as disputes,
undecided items marked as undecided, so an agent never states one side of an
open disagreement, or a maybe, as settled fact.

The API key decides everything an agent can reach: its account, and its mode —
a Personal key sees the person's life, a Work key their work, never both. A key
cannot manage keys or accounts, and Continuum refuses a settle without the
person's own words.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from continuum_sdk.client import AsyncContinuum, ContinuumError
from continuum_sdk.prompt import belief, describe_recall, describe_remembered, quote

INSTRUCTIONS = """Continuum is this person's long-term memory: what they decided, \
prefer, do and know, kept as beliefs that change over time.

- Before answering anything about the person, their plans or their work, call
  `recall`. Cite what it returns; do not invent memories.
- When the person tells you something durable — a decision, a preference, a
  change, a fact about their life or work — call `remember` with their words.
  Never remember your own answers: they are made from memory already.
- A DISPUTED result means the record disagrees with itself. Say so and give both
  sides; never pick one yourself.
- Only call `settle` when the person has told you which side holds, and pass
  their exact words. If they have not decided, leave it for them."""


def build_server(client: AsyncContinuum) -> MCPServer:
    """The server, around a client — split out so the tools are testable."""
    server = MCPServer("continuum", instructions=INSTRUCTIONS)

    @server.tool(
        description="Store something the person said that is worth remembering: a "
        "decision, preference, change of mind, or fact about their life or work. Pass "
        "their own words. Continuum extracts the facts, confirms what it already "
        "knew, retires what a stated change replaces, and asks the person about "
        "anything it is unsure of — nothing is ever deleted.",
    )
    async def remember(text: str) -> str:
        try:
            return describe_remembered(await client.remember(text))
        except ContinuumError as exc:
            return f"Not remembered: {exc.detail}"

    @server.tool(
        description="Look up what Continuum believes about a question before answering "
        "it. Returns the relevant beliefs with how sure the record is and when each "
        "was recorded, any DISPUTED beliefs (the record disagrees: present both "
        "sides, pick none), and UNDECIDED items (said as a maybe: ask, never assert).",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    async def recall(query: str, limit: int = 8) -> str:
        try:
            return describe_recall(await client.recall(query, limit=limit))
        except ContinuumError as exc:
            return f"Could not recall: {exc.detail}"

    @server.tool(
        description="List the disagreements waiting for the person to decide, with the "
        "ids `settle` needs. Show them to the person; do not decide them yourself.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    async def list_disputes() -> str:
        try:
            disputes = await client.disputes()
        except ContinuumError as exc:
            return f"Could not list disputes: {exc.detail}"
        if not disputes:
            return "No open disputes."
        lines = [f"{len(disputes)} open dispute(s):"]
        for number, dispute in enumerate(disputes, start=1):
            sides = [dispute.memory, *dispute.conflicting]
            lines.append(f"\n{number}." + (" (yours vs the team's — settle it in Continuum's "
                                             "inbox)" if dispute.team else ""))
            lines += [f"   - {belief(m)}" for m in sides]
        return "\n".join(lines)

    @server.tool(
        description="Apply the PERSON's decision on a dispute. Call this only after the "
        "person has explicitly said which side holds, or that both are true — never on "
        "your own judgement. `said` must be their own words; it is recorded with the "
        "decision. `winner_id` is the belief that holds; with keep_both=true, both "
        "stay. The other side is retired by an edge, never deleted.",
        annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True),
    )
    async def settle(
        winner_id: str, said: str, loser_ids: list[str] | None = None, keep_both: bool = False
    ) -> str:
        try:
            settled = await client.settle(
                winner_id, said=said, loser_ids=loser_ids, keep_both=keep_both
            )
        except (ContinuumError, ValueError) as exc:
            return f"Not settled: {getattr(exc, 'detail', exc)}"
        if settled.action == "kept_both":
            return f"Settled: both hold — {quote(settled.winner)} and " + ", ".join(
                quote(m) for m in settled.losers
            )
        return f"Settled: {quote(settled.winner)} holds; retired " + ", ".join(
            quote(m) for m in settled.losers
        )

    return server


def main() -> None:
    build_server(AsyncContinuum()).run("stdio")


if __name__ == "__main__":
    main()
