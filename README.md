# Continuum

**Long-term work memory for AI agents — that remembers why it changed its mind.**

Continuum ingests notes and conversations, extracts durable facts about someone's
work (decisions, preferences, constraints, people, events), and maintains them as
a **belief graph** that can be queried, audited and corrected over time.

---

## The problem

Storing *"Dave likes pizza"* is easy. Every memory framework does it.

The hard part starts when Dave says *"I don't like pizza anymore, I like pasta"*
— and the system has to decide, correctly and repeatedly, across months of
accumulating half-contradictory facts, which belief still holds. That problem is
genuinely unsolved, and the existing memory layers fail it in the same four ways:

| Failure | What it costs you |
| --- | --- |
| **Silent deletion** | The old belief is gone. You cannot audit why the agent changed its mind |
| **Over-eager overwrite** | An LLM judged too confidently and a true belief is destroyed |
| **No decay** | A fact recorded once, eighteen months ago, still ranks at full confidence |
| **No provenance** | The agent asserts something and cannot say where it came from |

**Silent overwrite is the one that matters.** The others degrade answers; that
one destroys information, invisibly, with no way to notice it happened. A memory
system you cannot audit is a memory system you cannot trust — and the moment it
overwrites something true, you have no way of knowing it did.

Continuum's answer: memories are **nodes in a belief graph, not rows in a list.**
Superseding writes an edge, not a `DELETE`. A genuinely ambiguous contradiction
goes to a human instead of being guessed at by a second LLM call. Unused beliefs
decay and are archived rather than deleted.

---

## The four design invariants

Every part of the system is built around these. They are not preferences, and
they are not traded away to simplify an implementation.

1. **Nothing is ever hard-deleted.** Superseding writes a graph edge. The old
   belief stays queryable forever.
2. **Ambiguity is escalated to a human, not guessed at.** A second LLM call is
   not a trustworthy arbiter of a genuine contradiction. Below the confidence
   gate, the pair lands in an inbox.
3. **LLM calls are spent only where they can change the answer.** Category policy
   and cosine bands resolve most cases for free.
4. **History is append-only.** An `event` can never be superseded — two things
   can both have happened.

The single most consequential setting is `AUTO_SUPERSEDE_CONFIDENCE` (default
`0.80`): how sure the judge must be before retiring a belief without asking.
Raise it and you escalate more; lower it and you silently lose beliefs. It is set
against [a labelled corpus](continuum-be/src/continuum/evaluation/README.md), not
against intuition.

---

## Quickstart

Needs [Ollama](https://ollama.com) running on the host — the stack uses it
directly rather than shipping its own copy.

```bash
git clone <your-remote> Continuum && cd Continuum
cd continuum-be && docker-compose -f local.yml up -d --build && cd ..   # the backend
cd continuum-fe && yarn && yarn dev          # the UI at http://localhost:5173
```

Docker runs the backend: Qdrant, Postgres, a one-shot job that asks the host's
Ollama for the configured models, and the API — which waits for all three, then
upgrades the Postgres schema (Alembic), creates its Qdrant collection and starts
the decay scheduler from its own FastAPI lifespan hook. There is no separate migrate, init or model-pull step. The UI is not a
container: it runs on the Vite dev server with yarn, which proxies `/api` to the
API on `:8000`.

**Belief graph: <http://localhost:5173>** — the first visit asks you to create
the admin account. If you used Continuum before sign-in existed, put your old
user id in "Keep memories stored under" and your graph carries over.

API docs: <http://localhost:8000/docs> · Qdrant dashboard:
<http://localhost:6333/dashboard> (both loopback-only)

**Connecting an agent.** In the UI, Account → API keys → create one, then:

```bash
curl -H "Authorization: Bearer ck_..." -H 'content-type: application/json' \
  -d '{"text":"We moved the event store to Mongo."}' http://localhost:8000/api/v1/ingest
```

Each key writes to its owner's graph, can be revoked at any time, and cannot
manage accounts or mint more keys. The API listens on localhost only; set
the port mapping in `continuum-be/local.yml` to `"8000:8000"` for agents on other machines.

> The first start pulls ~6 GB of models into your Ollama if they are not there
> already. `docker-compose -f local.yml logs -f models` (in `continuum-be/`) shows progress.

Then watch it handle a reversed decision end to end:

```bash
cd continuum-be && CONTINUUM_API_KEY=ck_... uv run python scripts/seed_demo.py
```

To point the LLM at Groq or a hosted vLLM instead of your local Ollama, put
`LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in
`continuum-be/.envs/.local/.api.override` (gitignored; it overrides the tracked
defaults in `.envs/.local/.api`).

---

## Repository layout

One repository, two independent projects, no workspace tool. The backend is a
self-contained `uv` project and knows nothing about the frontend; their only
relationship is an HTTP contract. CI runs each only when its own directory
changes.

```
Continuum/
├── continuum-be/local.yml   the backend stack: qdrant + postgres + api
├── .env.example             every setting, with provider presets
├── CLAUDE.md                the engineering brief
├── continuum-be/            FastAPI + Qdrant + the resolution layer
└── continuum-fe/            React + Three.js — the belief graph UI
```

| | |
| --- | --- |
| [`continuum-be/README.md`](continuum-be/README.md) | Backend setup, architecture, the full endpoint list |
| [`continuum-fe/README.md`](continuum-fe/README.md) | The graph UI: what each visual channel encodes, and why 3D |
| [`CLAUDE.md`](CLAUDE.md) | Engineering brief: layering rules, conventions, what not to "improve" |

---

## How it decides

```
ingest (note / transcript)
   │
   ▼  extract → {content, category, subject, source_excerpt}
   │
   ▼  embed the batch once; reuse each vector for lookup AND write
   │
   ├─ near-identical score ............. duplicate     → reinforce, write nothing
   │     └─ unless a number, date, name or negation changed → judged instead
   ├─ different category / subject ..... new           → free, no LLM call
   ├─ either side is an event .......... new           → free, no LLM call
   └─ related, same kind ............... judge (one LLM call)
         ├─ supersedes, p ≥ 0.80 ....... SUPERSEDES    → write edges, retire old
         ├─ supersedes, p < 0.80 ....... CONFLICT      → escalate to a human ★
         ├─ conflict ................... CONFLICT      → escalate to a human
         └─ independent ................ store, no edges
```

Every failure path escalates. A judge timeout, an unparseable response, an
unknown verdict — none of them can retire a memory. `p` is the model's own token
probability for its verdict, not a number it wrote; the similarity bands are
calibrated per embedding model against the evaluation corpus.

Retrieval then ranks by `similarity × confidence × recency`, and **contradicted
memories are deliberately still retrieved**. When two beliefs disagree, the
honest behaviour is to surface both and say so, not to quietly pick one.

## How it looks

The graph encodes the lifecycle directly: **colour is status** (amber means
disputed, and nothing else is loud), **size is confidence**, **arrows are
`supersedes`** drawn new → old so a belief's history reads in one direction. A
conflict edge gets no arrowhead — it is symmetric, and drawing a direction would
be the UI deciding the thing the resolver deliberately escalated to you.

Click any node for the verbatim text it was extracted from. Unresolved disputes
land in an inbox with three verdicts, one of which is *both are true*.

---

## Status

| Phase | | |
| --- | --- | --- |
| **1 — Ingest & storage** | ✅ | Domain-tuned extraction, Qdrant with payload indexes, duplicate detection, per-user scoping |
| **2 — Resolution & decay** | ✅ | The judge + confidence gate, supersede edges, contradiction inbox, per-category exponential decay with archival |
| **3 — Memory-augmented chat** | ✅ | SSE streaming, retrieval ranked by similarity × confidence × recency, disagreement surfaced rather than resolved |
| **4 — Belief graph UI** | ✅ | `continuum-fe`: Three.js force-directed graph off `/memories/graph`, provenance panel, contradiction inbox, streaming chat |
| **5 — Evaluation** | ✅ | 28-case labelled corpus; belief-loss, stale-belief and merge-loss measured separately; a gate sweep that replays one recorded run at every threshold for free |

Backend: 270 tests. Frontend: 76. Neither needs a running service.

---

## Stack

**Backend** — FastAPI · uv · Qdrant · Postgres (SQLAlchemy 2 + Alembic, accounts
only) · any OpenAI-compatible LLM (Ollama / vLLM / Groq) · `bge-m3` embeddings ·
structlog · APScheduler.

**Frontend** — React 19 · TypeScript · Vite · Tailwind v4 · shadcn-style
primitives · Three.js via `react-force-graph-3d` · vitest.
