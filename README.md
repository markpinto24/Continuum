# Continuum

**Long-term memory for AI agents — that remembers why it changed its mind.**

Continuum turns notes and conversations into durable facts — decisions,
preferences, constraints, people, routines — and keeps them as a **belief graph**:
when a fact changes, the old belief is retired by an edge, not deleted, and when
it is unclear which one holds, a person is asked instead of a model guessing.
Every claim below is measured, and every measurement says what it cost.

---

## The problem

Storing *"Dave likes pizza"* is easy. Every memory framework does it.

The hard part starts when Dave says *"I don't like pizza anymore, I like
pasta"* — and the system has to decide, correctly and repeatedly, across months of
half-contradictory facts, which belief still holds. Existing memory layers fail
it in the same four ways:

| Failure | What it costs you |
| --- | --- |
| **Silent deletion** | The old belief is gone. You cannot audit why the agent changed its mind |
| **Over-eager overwrite** | An LLM judged too confidently and a true belief is destroyed |
| **No decay** | A fact recorded once, eighteen months ago, still ranks at full confidence |
| **No provenance** | The agent asserts something and cannot say where it came from |

Silent overwrite is the one that matters: the others degrade answers, that one
destroys information invisibly. A memory you cannot audit is a memory you cannot
trust.

## The four commitments

Every part of the system is built around these, and none is traded away to make
an implementation simpler.

1. **Nothing is ever hard-deleted.** Superseding writes a graph edge; the old
   belief stays queryable forever.
2. **Ambiguity escalates to a human.** A second LLM call is not a trustworthy
   arbiter of a real contradiction. Below a confidence gate, the pair goes to an
   inbox — where *both are true* is a first-class answer.
3. **History is append-only.** An event is never superseded: two things can both
   have happened.
4. **Every failure fails toward the human.** A judge timeout, an unparseable
   answer, an unknown verdict — each one escalates. None retires a memory.

---

## What it measurably does

Measured on labelled corpora with a **local 7B model** (qwen2.5:7b) as the judge
and `bge-m3` embeddings. Labels are written before a change first runs, with
held-out cases and cases labelled *against* the change so its cost shows;
resolution is run twice and extraction three times, because one draw is noise.
Each number links to how it was found.

| | Before | Now | |
| --- | --- | --- | --- |
| **Beliefs lost** — retired when they should have lived | 8.7% | **0%** | Work (59 cases) and Personal (40), two passes · [§10](continuum-be/src/continuum/evaluation/FINDINGS.md), [§13](continuum-be/src/continuum/evaluation/FINDINGS.md) |
| **Facts merged away** as false duplicates | 10.7% | **0%** | the duplicate guard: a changed number, date, name or negation is never "the same" · [§8](continuum-be/src/continuum/evaluation/FINDINGS.md) |
| **Stale beliefs** — should have changed, didn't, nobody asked | 14.3% | **0%** | except one labelled known gap · [§8](continuum-be/src/continuum/evaluation/FINDINGS.md), [§13](continuum-be/src/continuum/evaluation/FINDINGS.md) |
| **A change filed under a synonym** (`gym` / `workout`, `home` / `flat`) | 0 of 5 caught | **4 of 5** | subjects matched by meaning, names by spelling · [§13](continuum-be/src/continuum/evaluation/FINDINGS.md) |
| **Instructions planted in memory** ("ignore previous instructions…") | 15 of 18 obeyed | **0 of 18** | a memory is data, never an order · [§19](continuum-be/src/continuum/evaluation/FINDINGS.md) |
| **Replies to suggestions** ("sounds good") remembered | recall 5.6% | **58–68%** | then 77% on the grown corpus (34 cases); a maybe kept as *pending*, never believed · [§12](continuum-be/src/continuum/evaluation/FINDINGS.md), [§14](continuum-be/src/continuum/evaluation/FINDINGS.md) |
| **Extraction vs Mem0** | — | fact F1 0.64 vs 0.62; category 71% vs 0%; provenance 100% vs 0% | a near-tie on recall, a rout on structure · [§6](continuum-be/src/continuum/evaluation/FINDINGS.md) |

**What it costs.** Safety is bought with questions: the judge asks a person
about 33% of the time in Work and about 60% in Personal, where it calls most
stated life changes a "conflict". Every one of those goes to the inbox rather
than being guessed — the safe direction, and the reason a stronger judge is next
on the list. The full record, including what was tried and dropped, is
[`FINDINGS.md`](continuum-be/src/continuum/evaluation/FINDINGS.md).

---

## See it

Start the stack (below), create an API key in the UI, then feed it a short arc of
work notes in which a decision is later reversed:

```bash
cd continuum-be && CONTINUUM_API_KEY=ck_... uv run python scripts/seed_demo.py
```

Watch three things: the reversal is retired with an edge or sent to the inbox —
never silently overwritten; the narrowing *"Postgres stays for reporting only"*
lands in the inbox instead of being guessed at; and the chat answer surfaces the
disagreement rather than picking a side.

In the graph, **colour is status** (amber means disputed, and nothing else is
loud), **size is confidence**, and **arrows are supersedes**, drawn new → old. A
dispute edge has no arrowhead: it is symmetric, and a direction would be the UI
deciding what the resolver deliberately asked you. Click any node for the exact
words it came from.

---

## Quickstart

Needs Docker and [Ollama](https://ollama.com) on the host — the stack uses it
directly rather than shipping its own copy of every model.

```bash
git clone <your-remote> Continuum && cd Continuum
cd continuum-be && docker-compose -f local.yml up -d --build && cd ..   # the backend
cd continuum-fe && yarn && yarn dev          # the UI at http://localhost:5173
```

The first visit asks you to create the admin account. Docker runs Qdrant,
Postgres, a one-shot job that asks your Ollama for the models (~6 GB the first
time), and the API, which migrates its database and creates its collection on
start — there is no separate setup step.

To use Groq or a hosted vLLM instead of local Ollama, set `LLM_BASE_URL`,
`LLM_API_KEY` and `LLM_MODEL` in `continuum-be/.envs/.local/.api.override`.
For a public deployment, [`DEPLOY.md`](DEPLOY.md) puts the whole stack on one
free Oracle Cloud VM with HTTPS.

## Two memories, never mixed

- **Personal** — your life: people, health, routines, goals, home and dates.
  Health never fades and never changes without asking you.
- **Work** — decisions, preferences, constraints, owners and events, with an
  optional shared team space whose own resolver decides what the team believes.

They are separate graphs. Switch with **Personal | Work** in the header; an API
key belongs to one of them for life.

## Give your agent this memory

[`continuum-sdk`](continuum-sdk/README.md) — five minutes to long-term memory for
your own agent:

- an **MCP server** for Claude Code, Claude Desktop or any MCP client:
  `remember`, `recall`, `list_disputes`, `settle`;
- a **Python client**, sync and async, and an example agent that works with any
  OpenAI-compatible model.

An agent can settle a dispute only by passing on the person's own words; the
API refuses it otherwise, and keeps the words with the decision.

---

## How it decides

```
ingest (note / transcript)
   │
   ▼  extract → {content, category, subject, source_excerpt}
   │
   ▼  embed the batch once; reuse each vector for lookup AND write
   │
   ├─ near-identical ................... duplicate   → reinforce, write nothing
   │     └─ unless a number, date, name or negation changed → judged instead
   ├─ different subject, unrelated ..... new         → free, no LLM call
   │     (two words for one thing — gym, workout — are still compared)
   ├─ either side is an event .......... new         → free, no LLM call
   └─ related, comparable kind ......... judge (one LLM call)
         ├─ supersedes, p ≥ 0.80 ....... retire the old belief, write the edge
         │     └─ health, or a role whose holder never "stopped" → ask instead
         ├─ supersedes, p < 0.80 ....... ask a person
         ├─ conflict ................... ask a person
         └─ independent ................ store both
```

`p` is the model's own token probability for its verdict, not a number it
wrote — a 7B judge's written confidence came back at exactly 0.95 every time.
The similarity bands are calibrated per embedding model, never set by feel.
Retrieval ranks by `similarity × confidence × recency`, and **disputed beliefs
are still retrieved**: when two beliefs disagree, the honest answer gives both.

---

## Repository

Three independent projects, no workspace tool — their only relationship is an
HTTP contract, and CI runs each only when its own directory changes.

| | |
| --- | --- |
| [`continuum-be/`](continuum-be/README.md) | FastAPI, Qdrant, Postgres (accounts only): extraction, the resolver, decay, chat, voice, the evaluation harness |
| [`continuum-fe/`](continuum-fe/README.md) | React and Three.js: the belief graph, chat, the inbox, the Lumen voice mode |
| [`continuum-sdk/`](continuum-sdk/README.md) | Memory for other agents: the Python client and the MCP server |
| [`FINDINGS.md`](continuum-be/src/continuum/evaluation/FINDINGS.md) | Everything measured, including what was tried and dropped |
| [`CLAUDE.md`](CLAUDE.md) | The engineering brief: layering rules, conventions, what not to "improve" |
| [`TODO.md`](TODO.md) | What is next, in iterations |

**Stack** — FastAPI · uv · Qdrant · Postgres (SQLAlchemy 2, Alembic) · any
OpenAI-compatible LLM (Ollama, vLLM, Groq) · `bge-m3` · local Whisper and Piper
for voice · React 19 · TypeScript · Vite · Tailwind v4 · Three.js.

Backend: 620 tests. Frontend: 207. SDK: 14. None needs a running service; a
nightly job runs the resolution corpora on a real model and fails if a single
belief is lost.
