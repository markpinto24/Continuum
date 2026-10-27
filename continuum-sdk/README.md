# continuum-sdk — long-term memory for your agent in 5 minutes

Continuum remembers what a person decided, prefers and does — and, unlike a
plain memory store, what happens when that changes: a stated change retires the
old belief with an edge (never a delete), and a real contradiction goes to the
person instead of being guessed. This package gives your agent that memory, two
ways:

- **An MCP server** (`continuum-mcp`) for Claude Code, Claude Desktop or any MCP
  client: four tools, nothing to write.
- **A Python client** (`Continuum`, `AsyncContinuum`) for an agent you write
  yourself.

Both talk to a running Continuum over its HTTP API, with an API key.

## 1. A key (1 minute)

Run Continuum (`continuum-be/`: `docker-compose -f local.yml up -d --build`),
sign in, and open **Settings → API keys**. Name the key after the agent, pick
its mode — **Personal** (the person's life) or **Work** — and copy the secret
(`ck_…`). The mode is fixed for the key's life: a Work key never sees Personal
memories. A key cannot manage keys, passwords or users.

## 2a. Claude Code or Claude Desktop (2 minutes)

```bash
cd continuum-sdk && uv sync --extra mcp

claude mcp add continuum \
  -e CONTINUUM_URL=http://localhost:8000 -e CONTINUUM_API_KEY=ck_... \
  -- uv --directory "$PWD" run continuum-mcp
```

For Claude Desktop, the same in its config file:

```json
{
  "mcpServers": {
    "continuum": {
      "command": "uv",
      "args": ["--directory", "/path/to/Continuum/continuum-sdk", "run", "continuum-mcp"],
      "env": { "CONTINUUM_URL": "http://localhost:8000", "CONTINUUM_API_KEY": "ck_..." }
    }
  }
}
```

The agent gets four tools, and instructions on when to use them:

| tool | what it does |
| --- | --- |
| `recall` | What Continuum believes about a question: each belief with its confidence and date; **DISPUTED** beliefs, both sides, to present and never pick between; **UNDECIDED** maybes, to ask about and never assert. Read-only |
| `remember` | Store what the person said. Continuum extracts the facts, confirms what it knew, retires what a stated change replaces, and raises a dispute when it is unsure |
| `list_disputes` | The disagreements waiting for the person, with their ids. Read-only |
| `settle` | Apply **the person's** decision on a dispute — only after they have said which side holds, with their words in `said` |

## 2b. Your own agent (2 minutes)

```bash
# not on PyPI: install it from this repository
uv add --editable /path/to/Continuum/continuum-sdk
```

```python
from continuum_sdk import Continuum
from continuum_sdk.prompt import describe_recall

memory = Continuum()   # reads CONTINUUM_URL and CONTINUUM_API_KEY

# Before answering: what does the record say? Put this in your prompt.
context = describe_recall(memory.recall("what database does Atlas use?"))

# After the person speaks: remember their words — never your own answer, which
# was made from memory and would count as evidence for itself.
memory.remember(messages=[{"role": "user", "content": "we moved Atlas to MongoDB"}])
```

`examples/agent.py` is the whole loop — recall, answer with any OpenAI-compatible
model, remember — in one short script:

```bash
CONTINUUM_API_KEY=ck_... uv run python examples/agent.py
```

`AsyncContinuum` has the same calls with `await`. `chat()` returns Continuum's
own memory-augmented answer, and what the turn did to the graph.

## 3. See it change its mind (1 minute)

```python
memory.remember("We chose Postgres for the Atlas event store for transactions.")
memory.remember("Atlas is moving its event store to MongoDB.")

found = memory.recall("what does Atlas use?")
found.disputes          # if Continuum was unsure: both beliefs, for the person
memory.disputes()       # the inbox, as data
```

A confident, stated change retires the old belief — still there, linked by
`superseded_by`, never deleted. One Continuum is not sure about becomes a
dispute, and stays one until the person decides.

## Settling: the person decides, the agent passes it on

```python
memory.settle(winner_id, said="Mongo now — we moved last week", loser_ids=[loser_id])
memory.settle(winner_id, said="both are true", keep_both=True)
```

`said` is required. Continuum refuses an agent that settles a dispute without
the person's own words (422), because a model deciding a real contradiction on
its own judgement is exactly the failure Continuum exists to prevent. The words
are stored with the decision, so every agent-made change can be traced to what
the person said — and forgetting a memory forgets them too.

## Errors

A refused or failed call raises `ContinuumError` with the API's own reason in
`.detail` — a Work key asking for Personal memories, a revoked key, a dispute
settled without `said`. The MCP tools return the reason as text instead of
failing, so the agent can tell the person.

## Develop

```bash
uv sync --extra mcp --extra dev
uv run pytest        # the client against a mock transport; the MCP tools through MCPServer
uv run ruff check .
```

No dependency on the backend's code: the HTTP API is the whole contract.
