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

```bash
git clone <your-remote> Continuum && cd Continuum
docker compose up -d --build
```

That is the whole contract. Compose brings up Qdrant, Ollama, a one-shot job that
pulls the models, the API — which waits for both, then creates its Qdrant
collection and starts the decay scheduler from its own FastAPI lifespan hook —
and the web UI. There is no separate migrate, init or model-pull step.

**Belief graph: <http://localhost:3000>**

API docs: <http://localhost:8000/docs> · Qdrant dashboard:
<http://localhost:6333/dashboard>

> First start pulls a ~5 GB model. `docker compose logs -f ollama-init` shows
> progress.

Then watch it handle a reversed decision end to end:

```bash
cd continuum-be && uv run python scripts/seed_demo.py
```

To point at Groq or a hosted vLLM instead of the bundled Ollama, copy
[`.env.example`](.env.example) to `.env` at this root — compose reads it and
overrides its defaults.

---

## Repository layout

One repository, two independent projects, no workspace tool. The backend is a
self-contained `uv` project and knows nothing about the frontend; their only
relationship is an HTTP contract. CI runs each only when its own directory
changes.

```
Continuum/
├── docker-compose.yml       full stack — the only thing that spans both
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
   ├─ score ≥ 0.94 ..................... duplicate     → reinforce, write nothing
   ├─ different category / subject ..... new           → free, no LLM call
   ├─ either side is an event .......... new           → free, no LLM call
   └─ score ≥ 0.78, same kind .......... judge (one LLM call)
         ├─ supersedes, conf ≥ 0.80 .... SUPERSEDES    → write edges, retire old
         ├─ supersedes, conf < 0.80 .... CONFLICT      → escalate to a human ★
         ├─ conflict ................... CONFLICT      → escalate to a human
         └─ independent ................ store, no edges
```

Every failure path escalates. A judge timeout, an unparseable response, an
unknown verdict — none of them can retire a memory.

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

Backend: 126 tests. Frontend: 23. Neither needs a running service.

---

## Stack

**Backend** — FastAPI · uv · Qdrant · any OpenAI-compatible LLM (Ollama / vLLM /
Groq) · `nomic-embed-text` embeddings · structlog · APScheduler.

**Frontend** — React 19 · TypeScript · Vite · Tailwind v4 · shadcn-style
primitives · Three.js via `react-force-graph-3d` · vitest.
