# CLAUDE.md — Continuum

Project brief for Claude Code. Read this before touching anything.

This file lives at the **repo root**. Continuum is one repository with two
projects under it — `continuum-be` and `continuum-fe` — and no workspace tool
binding them. They build, test and deploy independently; this brief and
`docker-compose.yml` are the only things that span both.

---

## What we are building

**Continuum is a long-term work-memory system for AI agents.** It ingests notes
and conversations, extracts durable facts about a person's work — decisions,
preferences, constraints, people, events — and maintains them as a **belief
graph** that can be queried, audited and corrected over time.

It is a backend (`continuum-be`, FastAPI) plus a planned frontend
(`continuum-fe`, React + Three.js) that visualises the graph. Both live in this
repository; neither depends on the other's build.

### The problem it exists to solve

Storing "Dave likes pizza" is easy. The hard, genuinely unsolved part is what
happens when Dave later says *"I don't like pizza anymore, I like pasta"* — and
the system must decide, correctly and repeatedly, across months of accumulating
half-contradictory facts, which belief still holds.

Existing memory layers (Mem0, ChatGPT memory, Rewind, Mem) all fail in the same
four ways:

| Failure | Consequence |
| --- | --- |
| Silent deletion | You cannot audit why the agent changed its mind |
| Over-eager overwrite | An LLM judged too confidently and a true belief is gone |
| No decay | Stale facts rank at full confidence forever and pollute retrieval |
| No provenance | The agent asserts something and cannot say where it came from |

### Continuum's four commitments

These are the design invariants. **Do not violate them, even when it would
simplify an implementation.**

1. **Nothing is ever hard-deleted.** Superseding writes a graph edge. The old
   belief stays queryable forever.
2. **Ambiguity escalates to a human.** A second LLM call is not a trustworthy
   arbiter of a real contradiction. Below `auto_supersede_confidence`, a
   "supersedes" verdict is downgraded to a human escalation.
3. **History is append-only.** An `event` can never be superseded or decayed —
   two things can both have happened.
4. **Every failure fails toward the human.** A judge timeout, an unparseable
   response, an unknown verdict — all escalate. None silently retire a memory.

---

## Stack

| Layer | Choice | Notes |
| --- | --- | --- |
| API | FastAPI + Uvicorn | async throughout |
| Packaging | **uv** | not poetry, not pip |
| Vector DB | Qdrant (Docker) | accessed directly, not via a framework facade |
| LLM | Any OpenAI-compatible endpoint | Ollama local / vLLM / Groq — `.env` switch |
| Embeddings | `nomic-embed-text`, 768-dim | same OpenAI-compatible client |
| Extraction | Mem0-inspired, domain-tuned prompt | `mem0ai` is a dependency; its `add()` is not used |
| Scheduling | APScheduler | decay sweep |
| Logging | structlog | request-scoped contextvars |
| Frontend (planned) | React, shadcn/ui, react-bits, Three.js | |

### Why we do not use Mem0's `Memory.add()`

Mem0's prompt design is the starting point for our extractor and it stays in the
dependency set for benchmarking. But storage is ours, deliberately.

Frameworks that support every vector DB and every LLM must stack wrappers to
unify `add()` / `search()`. We need payload indexes, filtered search on
lifecycle status, scroll queries for the graph view, and explicit supersede
edges — none of which survive a unified facade. Owning ~250 lines of Qdrant
access is much cheaper than discovering mid-project that the abstraction will
not let us do something.

**Do not replace the storage layer with a framework facade.**

---

## Folder structure

```
Continuum/                            ← git root
├── README.md                         the project story; links to each side
├── CLAUDE.md                         this file — the engineering brief
├── .env.example                      every setting, with provider presets
├── .gitignore                        covers both projects
├── docker-compose.yml                full stack: qdrant + ollama + api
├── .github/workflows/
│   ├── backend.yml                   paths: ['continuum-be/**']
│   └── frontend.yml                  paths: ['continuum-fe/**']
├── continuum-be/
│   ├── pyproject.toml                uv project, deps, ruff + pytest config
│   ├── uv.lock
│   ├── .dockerignore
│   ├── Dockerfile                    multi-stage uv build, non-root, healthcheck
│   ├── README.md                     backend setup + architecture
│   ├── scripts/
│   │   ├── seed_demo.py              end-to-end demo incl. a reversed decision
│   │   └── run_eval.py               Phase 5 harness: record once, sweep free
│   ├── src/continuum/
│   │   ├── main.py                   app factory, lifespan, scheduler wiring
│   │   ├── config.py                 all settings (pydantic-settings)
│   │   ├── core/
│   │   │   ├── logging.py            structlog config, redaction processors
│   │   │   └── middleware.py         request id, timing, context binding
│   │   ├── clients/                  ← thin, owned I/O adapters
│   │   │   ├── llm.py                OpenAI-compatible chat + embeddings
│   │   │   └── qdrant.py             collection bootstrap, filters, scroll
│   │   ├── models/
│   │   │   ├── memory.py             ★ domain model: Memory, categories,
│   │   │   │                           statuses, half-lives, decay maths
│   │   │   └── schemas.py            HTTP request/response models
│   │   ├── services/                 ← all business logic lives here
│   │   │   ├── extraction.py         text → ExtractedFact[]
│   │   │   ├── resolution.py         ★ the judge + confidence gate
│   │   │   ├── ingest.py             orchestration; applies verdicts to graph
│   │   │   ├── memory_store.py       domain ops over Qdrant
│   │   │   ├── decay.py              confidence decay + archival sweep
│   │   │   ├── retrieval.py          ★ similarity × confidence × recency rank
│   │   │   └── chat.py               ★ prompt assembly; surfaces disputes
│   │   ├── evaluation/               ← Phase 5: the instrument
│   │   │   ├── README.md             what each metric means, how to read it
│   │   │   ├── FINDINGS.md           ★ what the first real run found
│   │   │   ├── baselines/            recorded runs, replayable at any gate
│   │   │   ├── preflight.py          ★ can the embedder see an entity swap?
│   │   │   ├── types.py              Action vocabulary, corpus + outcome models
│   │   │   ├── corpus.py             strict loading; rejects unlabelled cases
│   │   │   ├── corpus/*.yaml         ★ the labelled data
│   │   │   ├── runner.py             executes cases against the REAL resolver
│   │   │   ├── metrics.py            ★ pure scoring, replay, sweep, recommend
│   │   │   ├── extraction.py         token matching + the Mem0 baseline
│   │   │   └── report.py             plain-text rendering
│   │   └── api/
│   │       ├── deps.py               DI wiring from app.state
│   │       ├── router.py             router aggregation
│   │       └── routes/               health, ingest, memories, conflicts,
│   │                                 decay, chat
│   └── tests/
│       ├── test_core_logic.py        parsing, coercion, lifecycle (pure)
│       ├── test_resolution.py        ★ confidence gate, category policy
│       ├── test_decay.py             half-lives, idempotence, floors
│       ├── test_retrieval.py         ★ ranking formula, dispute assembly
│       ├── test_chat.py              ★ stream shape, disputed prompt, write-back
│       ├── test_evaluation.py        ★ the instrument, before it is trusted
│       └── test_ingest_pipeline.py   end-to-end vs in-memory Qdrant
└── continuum-fe/                     ← the belief graph UI
    ├── Dockerfile                    node build -> nginx, proxies /api
    ├── nginx.conf                    SPA fallback + SSE-safe proxy
    ├── vite.config.ts
    └── src/
        ├── lib/
        │   ├── types.ts              ★ the backend contract, mirrored by hand
        │   ├── api.ts                thin typed client + SSE chat stream
        │   ├── sse.ts                ★ incremental SSE framing
        │   └── memory-style.ts       ★ colour/size vocabulary, shared canvas+DOM
        ├── hooks/                    use-resource, use-element-size
        ├── components/
        │   ├── belief-graph.tsx      ★ the Three.js scene
        │   ├── memory-detail.tsx     provenance, edges, reinforce/restore
        │   ├── contradiction-inbox.tsx
        │   ├── chat-panel.tsx        ★ streaming, citations, dispute banner
        │   └── ui/                   shadcn-style primitives, owned in-repo
        └── App.tsx
```

★ = the files carrying the novel logic. Change these carefully.

**Repo-level concerns live at the root; project-level ones do not.** The brief,
the compose file, the ignore rules and the env template describe the whole
system, so they sit above both projects. The `Dockerfile` and `.dockerignore`
stay inside `continuum-be/` because they describe how *that* project builds —
and the compose build context is `./continuum-be`, so they still resolve.

**There is deliberately no workspace tool.** No Nx, no Turborepo, no uv
workspace. Two independent projects under one root is the design: the backend is
a uv project and knows nothing about the frontend, and CI runs each only when its
own directory changes. Adding a workspace layer would couple two things whose
only real relationship is an HTTP contract.

### Layering rules

```
api/routes  →  services  →  clients  →  external
                   ↘  models  ↙
```

- **Routes** validate and delegate. No business logic, no direct Qdrant access.
- **Services** hold all logic. They depend on clients, never on FastAPI.
- **Clients** are thin I/O adapters. No policy decisions.
- **Models** are pure — no I/O. Decay maths lives on `Memory` precisely so it is
  testable without a database.
- Dependencies are built once in `lifespan`, stashed on `app.state`, exposed via
  `api/deps.py`. Never construct a client inside a route.

---

## Data model

`Memory` (in `models/memory.py`) is the centre of the system.

| Field | Why it exists |
| --- | --- |
| `category` | Resolution and decay policy are per-category. A `decision` can be superseded; an `event` cannot. A `constraint` decays in 60 days; a `person` in 365 |
| `subject` | Normalised entity slug. Narrows conflict candidates with a cheap payload filter *before* spending an LLM call |
| `confidence` | Decays exponentially when unreinforced; rises on re-observation |
| `status` | `active` / `superseded` / `contradicted` / `archived`. Never deleted |
| `supersedes` / `superseded_by` | The belief graph edges. The differentiator |
| `conflicts_with` | Unresolved conflicts awaiting a human |
| `source_id` / `source_excerpt` | Traceability from any assertion back to its origin text |

`RETRIEVABLE_STATUSES = {active, contradicted}` — **contradicted memories are
deliberately still retrieved.** When two beliefs disagree, the honest behaviour
is to surface both and say so, not to silently pick one or go quiet.

---

## The resolution pipeline

```
ingest (note / transcript)
   │
   ▼  FactExtractor — one LLM call per batch
{content, category, subject, source_excerpt}[]
   │
   ▼  embed whole batch in ONE call; reuse each vector for lookup AND write
   │
   ▼  ResolutionService.resolve(fact, neighbours)
   │
   ├─ score ≥ 0.94 ..................... DUPLICATE     → reinforce, no write
   ├─ different category ............... NEW           → free, no LLM call
   ├─ different subject ................ NEW           → free, no LLM call
   ├─ either side is an event .......... NEW           → free, no LLM call
   └─ score ≥ 0.78, same kind ......... judge (LLM call)
         ├─ duplicate ................... reinforce
         ├─ supersedes, conf ≥ 0.80 ..... SUPERSEDES   → write edges, retire old
         ├─ supersedes, conf < 0.80 ..... CONFLICT     → escalate to human ★
         ├─ conflict .................... CONFLICT     → escalate to human
         └─ independent ................. store, no edges
```

**`auto_supersede_confidence` (default 0.80) is the single most consequential
setting in the project.** Raise it and you escalate more; lower it and you
silently lose beliefs. Treat changes to it as a product decision.

Cheap filters run before the judge so LLM calls are spent only where the outcome
is genuinely uncertain. Keep it that way.

---

## Status: what is built, what is not

### ✅ Phase 1 — Ingest & storage (done)
- FastAPI app, uv packaging, Docker Qdrant
- OpenAI-compatible LLM + embedding client with defensive JSON parsing
- Domain-tuned extraction producing categories, subjects, excerpts
- Qdrant layer: payload indexes, filtered search, scroll, distinct users
- Batch embedding — one call per ingest, vectors reused for lookup and write

### ✅ Phase 2 — Resolution & decay (done, this release)
- `ResolutionService`: LLM judge + **confidence gate**
- Verdicts applied to the graph: supersede edges, symmetric conflict flags
- Conflict inbox — `GET /conflicts`, `POST /conflicts/resolve`
  (`keep_both` is a first-class outcome)
- Per-category exponential decay, idempotent, with archival at 0.25
- APScheduler sweep every 6h, plus on-demand `dry_run`
- `GET /memories/graph` — nodes + edges, the exact shape Phase 4 consumes
- Reinforce / reactivate endpoints (nothing is a one-way door)
- structlog overhaul: request ids, timing middleware, prod redaction
- 48 tests passing, ruff clean

### ✅ Phase 3 — Memory-augmented chat (done, this release)
- `POST /chat` — SSE, events `context` / `delta` / `done` / `error`.
  The `context` event is sent **before the first token** so the UI can show which
  memories are in play, and flag a dispute, while the answer is still streaming
- `POST /chat/context` — retrieval preview, no generation. Cheap enough to call
  from the graph UI, and the hook Phase 5 needs to score retrieval on its own
- `RetrievalService`: rank = `similarity × confidence^w × recency^w`.
  Multiplicative, so failing any one factor drops a memory; weights are exponents
  so `0` disables a factor exactly
- Over-fetch then re-rank — top-k by final score is not a subset of top-k by
  cosine, so fetching only k would cap what confidence and recency can promote
- **Disputes are surfaced, never resolved at answer time.** Both sides go into
  the prompt under `DISPUTED`, the counterpart is fetched by id if the query
  missed it, and the model is told not to pick the newer or more confident side
- Answers cite memories as `[n]`; `done` maps the citations back to memory ids
- Confirmation flows back through **ingest**, not a new judge — a DUPLICATE
  verdict *is* the confirmation, and a contradicting turn escalates like any
  other input. Only the user's turn is remembered; ingesting the reply would let
  a paraphrase re-enter the graph as independent evidence
- Retrieval never reinforces what it returns. Being retrieved is not evidence a
  belief is still true
- 86 tests passing, ruff clean

### ✅ Phase 4 — Belief graph UI (done, this release)
- React 19 + TypeScript + Vite + Tailwind v4, shadcn-style primitives owned
  in-repo. Strict mode, no `any` in application code, ESLint config in `.ts`
- Three.js force-directed graph off `GET /memories/graph` via
  `react-force-graph-3d` — node colour = status, size = confidence (cubed, since
  `nodeVal` is volume), `supersedes` arrows directed new → old
- **`conflicts_with` edges are drawn without an arrowhead.** A conflict is
  symmetric; giving it a direction would be the UI asserting the very thing the
  resolver escalated to a human
- Selecting a node lights its one-hop neighbourhood and dims the rest
- Memory detail: the verbatim `source_excerpt`, decay half-life, every edge as a
  click-through, plus reinforce and restore — neither a one-way door
- Contradiction inbox with three verdicts; **keep-both is a first-class button**,
  not a skip
- Chat over SSE: the `context` frame renders the memories in play, with the
  `similarity × confidence × recency` breakdown, before the first token; a
  disagreement banner sits above the answer; a turn that changes the graph
  refreshes it
- `three` + `react-force-graph-3d` split into their own chunk — ~85% of the
  bundle, and they change only when bumped
- 23 tests (vitest + jsdom), eslint clean, no backend needed

### ✅ Phase 5 — Evaluation (done, this release)
- **28-case labelled contradiction corpus**, every case carrying a `why`. The
  loader rejects an unlabelled case and rejects duplicate ids — a shadowed case
  vanishes from the denominator without anyone noticing
- Labels are **actions** (`retire` / `escalate` / `reinforce` / `store`), not
  verdicts. `NEW` and `INDEPENDENT` are two routes to the same behaviour, and
  grading them apart would mark the resolver down for being right cheaply
- **Three metrics, not one.** `belief_loss_rate` (retired something that should
  have lived), `stale_belief_rate` (should have retired, did not, did not ask),
  `merge_loss_rate` (folded a distinct fact in as a duplicate). Averaging them
  makes the gate untunable, because the dial trades one for the other
- **Record once, sweep free.** `CaseOutcome` stores the judge's relation and
  confidence *before* the gate, so every other gate value replays as arithmetic.
  A sweep over eight gates costs zero LLM calls
- `recommend_gate` applies the safety constraint first — cheapest gate inside the
  belief-loss budget — rather than maximising F1. F1 treats a destroyed belief
  and a wasted minute as the same size of mistake
- `band_miss_rate` separates "the judge was wrong" from "the fact never got near
  the belief it contradicts". Different problems, different fixes
- Per-tag scoring, so a regression localises to a kind of case
- Three cases tagged `known-gap` are expected to fail today and are kept. A
  corpus of only passes measures nothing
- `Mem0Extractor` implemented, running against an isolated embedded-Qdrant store
  reset between documents so Mem0's own update pass cannot turn an extraction
  miss into a hit. **The benchmark is a near-tie on fact F1 (0.64 vs 0.62) and a
  rout on structure** (category 71% vs 0%, provenance 100% vs 0%). It justifies
  the native extractor on structure and one-fact-per-memory, not on fact recall
  — Mem0 joined two unrelated facts with "and" into one unsupersedable memory,
  and recorded a question as a belief
- `preflight.py` refuses to report resolution numbers when the embedding model
  cannot distinguish an entity swap — added because the first real run produced a
  confident, well-formatted, completely meaningless table
- 40 evaluation tests; the instrument is tested before it is trusted

**The first run found three broken mechanisms — see `FINDINGS.md`.** In short:
`nomic-embed-text` returns byte-identical vectors for `Postgres` vs `MongoDB`, so
contradictions were classified duplicates and silently discarded; the cosine
thresholds are model-specific constants that predate having a model to calibrate
them against, and the duplicate band cannot be made sound by any threshold; and
`auto_supersede_confidence` is inert, because `qwen2.5:7b-instruct` returns every
`supersedes` at exactly 0.95. Belief loss stayed at 3.6% throughout — the safety
bias works. These are open issues, not fixed ones.

---

## Conventions

**Style**
- `ruff check .` must pass. Line length 100. `from __future__ import annotations`
  at the top of every module.
- Type-hint everything. Modern syntax: `str | None`, not `Optional[str]`.
- Async throughout. Never block the event loop.
- Comments explain *why*, not *what*. If a decision looks arbitrary, the comment
  says what it is protecting against.

**Logging**
- `structlog.get_logger(__name__)`, event names as `noun.verb` —
  `ingest.resolved`, `decay.archived`.
- Values as kwargs, never f-strings: `log.info("x", memory_id=id)`.
- Bind correlation ids with `core.logging.bind()`; the middleware handles
  `request_id`.
- Never log memory content outside a `SENSITIVE_KEYS`-covered field. Redaction is
  automatic in non-local environments — do not route around it.

**Testing**
- New logic needs a test. Resolution and decay changes need tests for the
  *failure* modes, not just the happy path.
- Integration tests use `AsyncQdrantClient(":memory:")` — real indexing and real
  cosine scores, no Docker.
- The stub embedder in `test_ingest_pipeline.py` deliberately models subject
  clustering (identical → 1.0, same subject → ~0.89, different subject → ~0.13).
  A pure hash makes everything orthogonal and the resolver is never exercised.
  **If you change the embedding text format, re-check that geometry.**

**Frontend**
- `src/lib/types.ts` mirrors `models/schemas.py` by hand. Small surface, rare
  changes, and a hand-written mirror turns a breaking backend change into a type
  error rather than a runtime `undefined`. **Change both in the same commit.**
- No data-fetching framework. `useResource` is ~40 lines and handles the one
  thing that bites: a stale response landing after a newer one.
- `memory-style.ts` is the single source of the visual vocabulary. Status colour
  is defined once and used by both the WebGL scene and the DOM, which is what
  stops a node reading amber in the canvas and grey in the sidebar.
- Node size uses `confidence ** 3` because `nodeVal` is a sphere *volume*. Linear
  confidence makes a 0.9 belief look barely larger than a 0.3 one.

**Config**
- Every tunable goes in `continuum-be/src/continuum/config.py` with a comment on
  what it trades off, and in the root `.env.example`. No magic numbers in
  services.
- Two `.env` locations, both gitignored, because the two entry points have
  different working directories: the repo root for `docker compose`, and
  `continuum-be/.env` for a local `uvicorn` run (pydantic-settings resolves
  `env_file=".env"` against the CWD).

---

## Commands

### Running it — one command

The whole stack is defined in compose: Qdrant, Ollama, a one-shot job that pulls
the models, and the API. The API waits for Qdrant to be healthy and for the model
pull to complete, then creates its own Qdrant collection in the FastAPI lifespan
hook and starts the decay scheduler.

```bash
docker compose up -d --build        # from the repo root
```

There is deliberately **no** separate migrate, init or model-pull step. If you
find yourself adding a startup command that has to be run by hand, put it in the
lifespan hook or the compose graph instead.

```bash
docker compose logs -f api
docker compose down        # keeps volumes
docker compose down -v     # wipes memories
```

A local `.env` at the repo root overrides the compose defaults — that is how you
point at Groq or a remote vLLM instead of the bundled Ollama.

### Developing against it

Compose runs from the repo root; `uv` runs from `continuum-be/`. Nothing binds
the two, which is the point — the backend is a self-contained uv project.

```bash
docker compose up -d qdrant ollama ollama-init   # repo root

cd continuum-be
uv sync --extra dev
uv run uvicorn continuum.main:app --reload
```

```bash
# all from continuum-be/
uv run pytest                          # 126 tests, no services needed
uv run ruff check . --fix
uv run python scripts/seed_demo.py     # end-to-end against a running stack

# Phase 5: one slow pass, then sweep the gate for free
uv run python scripts/run_eval.py --record eval-run.json
uv run python scripts/run_eval.py --replay eval-run.json
```

---

## Things that look like improvements but are not

- **Hard-deleting superseded memories to keep the collection small.** Breaks
  commitment 1. Archive instead.
- **Auto-resolving every conflict with a second LLM call to raise throughput.**
  Breaks commitment 2. That is precisely the failure mode the project exists to
  fix.
- **Dropping `contradicted` out of retrieval so answers look cleaner.** Makes the
  agent silently confident about a disputed fact.
- **Replacing the Qdrant layer with a framework facade to "reduce code".** See
  above.
- **Adding a workspace tool to "tie the monorepo together".** Nx, Turborepo and
  uv workspaces all solve shared-dependency problems these two projects do not
  have. Their only relationship is an HTTP contract.
- **Adding a manual setup step to the README instead of the compose graph.**
  `up -d --build` is the whole contract. Bootstrap belongs in the lifespan hook
  or in `depends_on`.
- **Drawing an arrowhead on a `conflicts_with` edge, or sorting disputes so the
  newer belief reads first.** Both are the UI quietly answering the question the
  resolver refused to answer.
- **Hiding the keep-both verdict behind a menu.** It is the correct answer often
  enough that burying it pushes people toward picking a side they do not believe.
- **Baking `VITE_API_URL` into the Docker image.** Vite inlines env vars at build
  time, so it could not be overridden at run time anyway; nginx proxies
  same-origin `/api` instead.
- **Dropping a memory's disputed counterpart because it did not make top-k.**
  Then the agent reports one half of an open disagreement as settled fact.
- **Reinforcing every memory that retrieval returns.** Being retrieved is not
  confirmation; it would let a stale belief keep itself alive by being
  well-worded. Confirmation goes through ingest's resolver.
- **Ingesting the assistant's reply along with the user's turn.** The reply is
  assembled from memory, so it would re-enter the graph as independent evidence
  for what it was derived from.
- **Logging with `print` or the stdlib logger.** Use the module-level
  `structlog.get_logger(__name__)`. Request id and user id are bound by
  middleware and inherited automatically; re-passing them as kwargs is noise.
- **Adding a corpus case without a rationale, or "fixing" the corpus when a
  number looks bad.** The corpus is the instrument. Relabelling a case to make a
  gate look good is how an evaluation stops measuring anything.
- **Reporting one blended accuracy number for the resolver.** Belief loss and
  stale beliefs trade against each other as the gate moves; a single figure hides
  exactly the thing you are trying to tune.
- **Skipping the cheap category/subject filters and judging everything.** Turns a
  cheap operation into an expensive one for no accuracy gain.
