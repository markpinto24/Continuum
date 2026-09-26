# CLAUDE.md — Continuum

Project brief for Claude Code. Read this before touching anything.

This file lives at the **repo root**. Continuum is one repository with two
projects under it — `continuum-be` and `continuum-fe` — and no workspace tool
binding them. They build, test and deploy independently; this brief, the
ignore rules and CI are the only things that span both.

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
| Embeddings | `bge-m3`, 1024-dim | same client. Was nomic until it proved blind to entity swaps (FINDINGS §1, §8) |
| Extraction | Mem0-inspired, domain-tuned prompt | `mem0ai` is a dependency; its `add()` is not used |
| Scheduling | APScheduler | decay sweep |
| Logging | `core/logger.py`: line/JSON formatters over structlog | redaction + request context underneath |
| Relational DB | Postgres 17, SQLAlchemy 2 (async, asyncpg), Alembic | accounts only — memories stay in Qdrant |
| Auth | sessions + API keys | argon2id passwords; SHA-256-hashed tokens; no JWT |
| Dictation | faster-whisper (local Whisper, CPU, int8) | speech to text on the server; read-aloud is the browser's |
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
├── .env.example                      template for continuum-be/.env (uv run)
├── .gitignore                        covers both projects
├── .github/workflows/
│   ├── backend.yml                   paths: ['continuum-be/**']
│   └── frontend.yml                  paths: ['continuum-fe/**']
├── continuum-be/
│   ├── pyproject.toml                uv project, deps, ruff + pytest config
│   ├── local.yml                     ★ the Docker stack: qdrant, postgres, api
│   ├── .envs/.local/.api · .postgres its settings (tracked local defaults;
│   │                                 personal ones in gitignored *.override)
│   ├── alembic.ini                   migration CLI; DB comes from DATABASE_URL
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
│   │   │   ├── logger.py             ★ get_logger(); line/JSON output; redaction
│   │   │   └── middleware.py         request id, timing, context binding
│   │   ├── clients/                  ← thin, owned I/O adapters
│   │   │   ├── authdb.py             ★ users, sessions, API keys (SQLAlchemy)
│   │   │   ├── llm.py                OpenAI-compatible chat + embeddings
│   │   │   └── qdrant.py             collection bootstrap, filters, scroll
│   │   ├── db/                       ← relational storage (accounts only)
│   │   │   ├── models.py             ★ tables; what migrations are checked against
│   │   │   ├── engine.py             engine + migrate() run at startup
│   │   │   └── migrations/           Alembic env + versions/, shipped in the image
│   │   ├── models/
│   │   │   ├── memory.py             ★ domain model: Memory, categories,
│   │   │   │                           statuses, half-lives, decay maths
│   │   │   ├── auth.py               User, ApiKey, Principal
│   │   │   └── schemas.py            HTTP request/response models
│   │   ├── services/                 ← all business logic lives here
│   │   │   ├── auth.py               ★ sign-in, sessions, keys, lockout
│   │   │   ├── limits.py             sliding windows: lockout + rate limit
│   │   │   ├── extraction.py         text → ExtractedFact[]
│   │   │   ├── resolution.py         ★ the judge + confidence gate
│   │   │   ├── ingest.py             orchestration; applies verdicts to graph
│   │   │   ├── memory_store.py       domain ops over Qdrant
│   │   │   ├── decay.py              confidence decay + archival sweep
│   │   │   ├── retrieval.py          ★ similarity × confidence × recency rank
│   │   │   ├── speech.py             dictation: local Whisper, audio never kept
│   │   │   ├── chat.py               ★ prompt assembly; surfaces disputes
│   │   │   ├── subjects.py           one entity, one slug (atlas = atlas-project)
│   │   │   └── reindex.py            ★ re-embed on model change; crash-safe
│   │   ├── evaluation/               ← Phase 5: the instrument
│   │   │   ├── README.md             what each metric means, how to read it
│   │   │   ├── FINDINGS.md           ★ what the first real run found
│   │   │   ├── baselines/            recorded runs, replayable at any gate
│   │   │   ├── preflight.py          ★ can the embedder see an entity swap?
│   │   │   ├── calibrate.py          ★ derive both cosine bands for a model
│   │   │   ├── types.py              Action vocabulary, corpus + outcome models
│   │   │   ├── corpus.py             strict loading; rejects unlabelled cases
│   │   │   ├── corpus/*.yaml         ★ the labelled data
│   │   │   ├── runner.py             executes cases against the REAL resolver
│   │   │   ├── metrics.py            ★ pure scoring, replay, sweep, recommend
│   │   │   ├── extraction.py         token matching + the Mem0 baseline
│   │   │   └── report.py             plain-text rendering
│   │   └── api/
│   │       ├── deps.py               ★ DI wiring; get_principal = identity
│   │       ├── router.py             router aggregation
│   │       └── routes/               health, auth, admin, ingest, memories,
│   │                                 conflicts, decay, chat
│   └── tests/
│       ├── test_core_logic.py        parsing, coercion, lifecycle (pure)
│       ├── test_resolution.py        ★ confidence gate, category policy
│       ├── test_decay.py             half-lives, idempotence, floors
│       ├── test_retrieval.py         ★ ranking formula, dispute assembly
│       ├── test_chat.py              ★ stream shape, disputed prompt, write-back
│       ├── test_evaluation.py        ★ the instrument, before it is trusted
│       ├── test_auth.py              ★ every route guarded; isolation; refusals
│       └── test_ingest_pipeline.py   end-to-end vs in-memory Qdrant
└── continuum-fe/                     ← the belief graph UI
    ├── package.json · yarn.lock      yarn 1; `resolutions` pins one vite
    ├── vite.config.ts                dev server + /api proxy (no container)
    └── src/
        ├── lib/
        │   ├── types.ts              ★ the backend contract, mirrored by hand
        │   ├── api.ts                thin typed client + SSE chat stream
        │   ├── sse.ts                ★ incremental SSE framing
        │   └── memory-style.ts       ★ colour/size vocabulary, shared canvas+DOM
        ├── hooks/                    use-resource, use-recorder, use-dictation,
        │                             use-speech-synthesis, use-element-size
        ├── components/
        │   ├── auth-screen.tsx       sign-in + first-run admin setup
        │   ├── account-dialog.tsx    API keys, password, users (admins)
        │   ├── belief-graph.tsx      ★ the Three.js scene
        │   ├── memory-detail.tsx     provenance, edges, reinforce/restore
        │   ├── contradiction-inbox.tsx
        │   ├── chat-panel.tsx        ★ streaming, citations, dispute banner
        │   └── ui/                   shadcn-style primitives, owned in-repo
        └── App.tsx                   ★ the auth gate, then the workspace
```

★ = the files carrying the novel logic. Change these carefully.

**Repo-level concerns live at the root; project-level ones do not.** The brief,
the ignore rules and CI describe the whole repository, so they sit above both
projects. Everything Docker is backend-only now that the UI runs on the Vite dev
server, so `local.yml`, its `.envs/`, the `Dockerfile` and `.dockerignore` all
live in `continuum-be/`.

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

## Authentication

**The memory owner comes from the credential and nowhere else.** `get_principal`
in `api/deps.py` resolves a `Principal` from `Authorization: Bearer ck_...` (an
agent's API key) or the session cookie (a person in the web UI). Every route
that touches memories takes `principal: CurrentUser` and passes
`principal.user_id` to the services. Request bodies have no `user_id`; the
`ClientBody` base refuses one with a message saying why.

- **New route? Take `CurrentUser`.** `test_every_route_but_the_public_ones_…`
  walks the OpenAPI schema and fails on any endpoint that answers an anonymous
  request. The public list is five endpoints; adding to it is a decision, not a
  convenience.
- **Fetching by id? Check ownership and answer 404.** `_owned()` in
  `routes/memories.py`. Qdrant ids are just strings; a lookup by id is the one
  place a filter by user does not happen for free.
- **Services stay user-agnostic.** They take a `user_id` argument, as before.
  `IngestRequest`/`ChatRequest` are the internal commands (body + owner);
  `IngestBody`/`ChatBody` are what a client may send.
- **API keys cannot manage credentials** (`SessionUser`, `AdminUser`): a leaked
  agent key stays a leaked key, not an account takeover.
- **Cookie writes need the `x-continuum-client` header** (CSRF, on top of
  SameSite=Strict). The frontend sends it on every request; agents use Bearer
  and are exempt.
- Users, sessions and keys are in Postgres (`clients/authdb.py` over the tables
  in `db/models.py`), not Qdrant: they need unique constraints and an atomic
  "create only if no account exists". Nothing replayable is stored — argon2id for
  passwords, SHA-256 for 256-bit tokens.
- **"Only the first" needs the table lock.** At READ COMMITTED, two racing setups
  both see an empty `users` table; without `LOCK TABLE` in `insert_user`, eight
  concurrent setups created 6-8 admins. SQLite hid this — it has one writer —
  which is why the race test runs against real Postgres.

### Database and migrations

- **Alembic owns the schema.** The API runs `migrate()` on every start, under a
  Postgres advisory lock so replicas starting together migrate once. There is no
  separate migrate step, and never `Base.metadata.create_all()`.
- **Changing a table:** edit `db/models.py`, then from `continuum-be/`:
  `uv run alembic revision --autogenerate -m "what changed"` (compose Postgres
  up), read the generated file, commit both. `test_migrations_match_the_models`
  fails if a model changes without a migration.
- **Migrations are frozen history.** They never import application code;
  `env.py` renders `UTCDateTime` as `sa.DateTime(timezone=True)` for this reason.
  Every migration needs a working `downgrade()` (tested).
- `UTCDateTime` makes every timestamp timezone-aware UTC in Python. Postgres
  stores `timestamptz`; SQLite (the tests) stores naive values. Without it,
  expiry comparisons would work in one database and raise in the other.
- Tests run the real migrations against in-memory SQLite, so they need no
  services. Postgres-only behaviour (the setup race, the full flow) runs with
  `TEST_DATABASE_URL` pointing at a database that may be wiped.
- `User.id` is the memory owner id and is **never derived from the email**.
  Random by default; set explicitly (setup, `ADMIN_USER_ID`, admin create) only
  to adopt memories stored before authentication existed.
- Lockout and rate limits count in process memory. Correct for one uvicorn
  process; with several workers, move them to Redis rather than raising limits.
- Deployment: Qdrant and the api bind to 127.0.0.1 in compose. The UI reaches
  the api through the Vite dev server's proxy; agents call `:8000` directly
  (change the port mapping in `local.yml` for remote ones). There is no reverse proxy, so
  `TRUST_PROXY_HEADERS` stays off and the per-address lockout sees real peers.

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
   ▼  canonicalise subjects onto slugs the graph already uses (subjects.py)
   │
   ▼  ResolutionService.resolve(fact, neighbours)
   │
   ├─ score ≥ dup band ................. DUPLICATE     → reinforce, no write
   │     └─ unless a number, date, name or negation differs → judged instead ★
   ├─ different category ............... NEW           → free, no LLM call
   ├─ different subject ................ NEW           → free, no LLM call
   ├─ either side is an event .......... NEW           → free, no LLM call
   └─ score ≥ conflict band, same kind . judge (LLM call, reason first)
         ├─ duplicate ................... reinforce
         ├─ supersedes, p ≥ 0.80 ........ SUPERSEDES   → write edges, retire old
         │     └─ a role (owns, maintains, reviews, leads…) and the new
         │        statement never says the old holder stopped → CONFLICT ★
         ├─ supersedes, p < 0.80 ........ CONFLICT     → escalate to human ★
         ├─ conflict .................... CONFLICT     → escalate to human
         └─ independent ................. store, no edges
```

**The bands belong to the embedding model** — bge-m3: duplicate 0.86, conflict
0.45 — and come from `CALIBRATED_THRESHOLDS` in `config.py`, derived by
`run_eval.py --calibrate`. Never set them by feel.

**`p` is a probability, not a number the judge wrote.** It is the model's token
probability for the relation it chose, read from logprobs. A 7B judge's written
confidence is canned (every supersede came back at exactly 0.95), which left the
gate inert. The written value is kept for audit and as a fallback for providers
without logprobs.

**The duplicate guard exists because DUPLICATE is the only verdict that discards
the incoming fact.** Embeddings are measurably blind to exactly the changes that
make a contradiction — one number, one name — so near-identical scores are not
trusted when the words say otherwise.

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
- Assistant answers render as Markdown — highlighted, language-labelled code
  blocks with copy, lists, bold — from untrusted model output, so no
  `rehype-raw`: HTML the model emits is shown as text, never injected
- The sidebar is resizable by dragging its edge (pointer capture, so the drag
  survives crossing the WebGL canvas); width is remembered per viewer
- Each chat turn says what it did to the graph, including "nothing stored" —
  silence used to look identical to memory being switched off
- 40 tests (vitest + jsdom), eslint clean, no backend needed

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

**The first run found three broken mechanisms; Priority 1 fixed most of them —
see `FINDINGS.md` §8–§9.** On the same corpus and judge: merge loss 10.7% → 0%,
stale belief 14.3% → 0%, band misses 25% → 7.1%, accuracy 46.4% → 75.0%, retire
recall 25% → 62.5%, judge agreement 44% → 78%.

- **Duplicate guard** — a number, date, name or negation that differs withholds
  the duplicate shortcut. This alone took merge loss to zero.
- **bge-m3**, thresholds calibrated per model, and a **crash-safe re-embedding
  migration** — tested for real when the disk filled mid-copy nine times.
- **Subject canonicalisation** — `atlas` and `atlas-project` are one entity.
- **The judge was copying its prompt's worked example** on a third of its calls.
  Now: a placeholder template, reason before relation, confidence from token
  probabilities, and the judge is told the NEW statement is always the later one
  — the gap that made it escalate clear reversals.

### ✅ Phase 6 — Authentication (done, this release)
- Accounts, web sessions and per-agent API keys; identity only from the
  credential, never a request field — before this, any caller could read or
  write any graph by typing a name, and any memory id opened any memory
- First admin from a web setup screen (prefilled with the pre-auth user id, so an
  existing graph is adopted) or from `ADMIN_EMAIL` / `ADMIN_PASSWORD`
- Account panel: create / revoke keys (secret shown once, with a curl line),
  change password (signs out other browsers), and user admin — disabling a user
  ends their sessions and keys but deletes nothing
- Refusals tested one by one: CSRF, login CSRF, lockout by email and by address,
  expired sessions, revoked keys, disabled users, last-admin protection, keys
  that try to manage keys, cross-user reads by id, a body naming another user
- Account store on Postgres via SQLAlchemy 2 + Alembic, migrated on startup
- Qdrant and the api moved to loopback; 50k-char input
  cap; 30 LLM requests per user per minute
- Verified live end to end: two users, a real LLM ingest by key, zero leakage in
  graph, lookup by id or retrieval
- 240 backend tests, 51 frontend tests

### ✅ Phase 7 — Voice (done, this release)
- **Dictation:** a microphone button in the chat box. The browser records
  (MediaRecorder: webm/opus in Chrome and Brave, ogg in Firefox, mp4 in Safari);
  the API transcribes with a **local** Whisper model (`faster-whisper`, `base`,
  int8 on CPU) — about 1 s for 11 s of speech. The text lands in the box for
  review; it is never sent, or remembered, on its own
- Not the browser's `SpeechRecognition`: it streams audio to Google, and Brave
  switches it off entirely, so for this user it would silently do nothing
- Audio is transcribed in memory and dropped; logs record duration and length,
  never the words. Capped at 120 s and 10 MB, checked before any CPU is spent
- The model preloads in the background at startup, cached on a volume
- **Read aloud:** a button on each answer, using the browser's speech synthesis
  (OS voices: speech-dispatcher on Linux). Markdown, code and `[n]` citations are
  stripped first, and text is queued in sentence-sized chunks — Chromium stops a
  single long utterance after ~15 s without an error

**The last lost belief was a class, and it is closed (§10).** Held-out cases,
written before any fix ran, showed a second owner / maintainer / reviewer /
rotation member superseded at ≈1.0 every time. Asking the model a narrower
question did nothing — it agreed with itself at 1.00. What works reads the
words: a supersede about a role (owns, maintains, reviews, leads — keyed on the
words, not the category, since the extractor files ownership as `fact`) applies
only if the new statement says the old holder stopped. **Belief loss 8.7% → 0% on 46 cases**, including a held-out
set written to test that rule. `auto_supersede_confidence` stays at 0.80: after
the rule, all 25 gate-eligible supersedes were correct in every band, and the
0.50–0.80 band holds only 3 — no evidence to move it.

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
- `from continuum.core.logger import get_logger`, then `log = get_logger(__name__)`
  at module level. Event names as `noun.verb` — `ingest.resolved`,
  `decay.archived`.
- Values as kwargs, never f-strings: `log.info("x", memory_id=id)`. They render
  as `| memory_id=…` after the message, or as JSON fields with `LOG_FORMAT=json`.
- Output is one line per event: `LEVEL: Timestamp | Module | Function | Message`.
  Module and Function are the caller's, found automatically — do not repeat them.
- Bind correlation ids with `core.logger.bind()`; the middleware binds
  `request_id`, the auth dependency `user_id`.
- New noisy third-party logger? Lower it in the list in `Logger.__init__`. Check
  the real logger name first — the "HTTP Request" lines came from `httpx2`, not
  `httpx`.
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
- Every request goes through `lib/api.ts`, which sends the CSRF header and the
  session cookie and reports a 401 on a data call to `onUnauthorized` — that is
  how an expired session lands on the sign-in screen instead of on a panel of
  errors. A 401 from sign-in itself is a wrong password, not a lapsed session.
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
- **Exactly one copy of three.js.** `3d-force-graph` needs `three >= 0.179`;
  pinning the direct dependency below that made the package manager install a
  second copy, and the renderer threw `intersectsFrustum is not a function` every
  frame — a blank canvas with the legend drawn over it. `vite.config.ts` dedupes
  `three`; when bumping it, check `yarn why three` shows a single copy
  (`@types/three` is only the type definitions).

**Config**
- Every tunable goes in `continuum-be/src/continuum/config.py` with a comment on
  what it trades off, in the root `.env.example`, and in
  `continuum-be/.envs/.local/.api` if the container should set it. No magic numbers in
  services.
- **Two sets of backend settings, never mixed.** `continuum-be/.env`
  (gitignored, from the root `.env.example`) is for `uv run uvicorn` on the host:
  every URL is `localhost`. `continuum-be/.envs/.local/.api` (tracked) is for the
  container: compose-network and `host.docker.internal` URLs. `local.yml`
  interpolates nothing, so Compose's automatic read of the `.env` beside it has
  no effect — otherwise it would have pushed localhost URLs and a stale embedding
  model into the container, silently.

---

## Commands

### Running it — one command for the backend, one for the UI

Compose defines the backend: Qdrant, a one-shot `models` job and the API. **The
UI is not a container** — it runs on the Vite dev server with yarn, whose proxy
makes `/api` same-origin. **Ollama is not a container either** — it runs on the host, and the
containers reach it as `host.docker.internal`. The `models` job asks it to pull
the configured models (from an 8 MB alpine image, via Ollama's HTTP API). The API
waits for Qdrant and the models, then creates or re-embeds its collection in the
FastAPI lifespan hook and starts the decay scheduler.

```bash
cd continuum-be && docker-compose -f local.yml up -d --build   # the backend
cd continuum-fe && yarn && yarn dev                           # the UI at :5173
```

There is deliberately **no** separate migrate, init or model-pull step. If you
find yourself adding a startup command that has to be run by hand, put it in the
lifespan hook or the compose graph instead.

```bash
docker-compose -f local.yml logs -f api
docker-compose -f local.yml down        # keeps volumes
docker-compose -f local.yml down -v     # wipes memories
```

Container settings are in `continuum-be/.envs/.local/.api`. Personal ones — a
Groq key, a different model — go in `.envs/.local/.api.override`, which is
gitignored and wins over `.api`. `local.yml` pins `name: continuum`: the folder is
`continuum-be`, and a derived project name would create new, empty volumes.
It needs Compose v2 (`docker-compose` on this machine is a link to it).

### Developing against it

Both run from `continuum-be/`: Docker for the dependencies, `uv` for the API.

```bash
docker-compose -f local.yml up -d qdrant postgres   # Ollama is on the host
uv sync --extra dev
uv run uvicorn continuum.main:app --reload
```

```bash
# all from continuum-be/
uv run pytest                          # 270 tests, no services needed
TEST_DATABASE_URL=postgresql+asyncpg://continuum:continuum@localhost:5432/continuum_test \
  uv run pytest -k postgres            # the Postgres-only tests (DB is wiped)
uv run alembic revision --autogenerate -m "..."   # after changing db/models.py
uv run alembic current                 # which revision the database is at
uv run ruff check . --fix
CONTINUUM_API_KEY=ck_... uv run python scripts/seed_demo.py   # end-to-end, live

# Phase 5: check the embedder, then one slow pass, then sweep the gate for free
uv run python scripts/run_eval.py --preflight
uv run python scripts/run_eval.py --crowded     # right memory judged in a crowded graph?
uv run python scripts/run_eval.py --record eval-run.json
uv run python scripts/run_eval.py --replay eval-run.json
```

---

## Things that look like improvements but are not

- **`Base.metadata.create_all()` "to skip Alembic in development".** Then the
  schema the tests and developers use is not the one the migrations build, and
  the first deploy finds out. Every environment migrates.
- **Removing the `LOCK TABLE` in `insert_user` because the tests pass without
  it.** The SQLite tests always pass without it. The Postgres race test does not.
- **Moving memories into Postgres "now that we have a database".** The belief
  graph's access patterns are vector search plus payload filters; that is what
  Qdrant is for. Postgres holds accounts.
- **Accepting a `user_id` in a request "for admin tools" or "for testing".**
  That is exactly the hole authentication closed. An admin acting on someone
  else's graph needs its own audited endpoint, not a trusted field.
- **Swapping sessions for JWTs "to be stateless".** A session row can be deleted
  — sign-out, password change, disabling a user all take effect on the next
  request. A JWT stays valid until it expires, and revocation then needs the
  very table JWTs were meant to avoid.
- **Switching dictation to the browser's `SpeechRecognition` "to drop the
  model".** It sends every word to Google, and in Brave it does not work at all.
- **Logging or storing a transcript.** It is user content that the user has not
  yet chosen to send. Log its length.
- **Sending a dictated transcript straight to chat.** Whisper mishears names and
  numbers — the very tokens the resolver cares most about — and a sent message
  is remembered. The user reviews it first.
- **Letting API keys manage keys, passwords or users.** Agents run in places a
  person does not watch; a key that can mint keys turns one leak into permanent
  access.
- **Publishing Qdrant or the api port on all interfaces again.** Qdrant has no
  auth of its own by default; anyone reaching it reads every memory and never
  meets the API's checks.
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
- **Running the stack with Compose v1 (`docker-compose` 1.29).** It cannot parse
  `local.yml` (`name:`, optional env file), and it crashes with
  `KeyError: 'ContainerConfig'` on Docker Engine 25+ when recreating a container,
  leaving the stack stopped with renamed containers. `docker-compose` must be v2
  (`docker-compose --version`); if a terminal still shows 1.29, run `rehash`.
- **Adding `${VAR}` interpolation back into `local.yml`.** Compose would fill it
  from `continuum-be/.env` — the host-run settings, with localhost URLs. Add the
  setting to `.envs/.local/.api` instead.
- **Removing `name: continuum` from `local.yml`.** The project name would become
  `continuum-be` and the stack would start on new, empty volumes.
- **Putting Ollama back in a container "to be self-contained".** It keeps a
  private copy of every model — 11.6 GB of pure duplication on a machine that
  already runs Ollama, which is what filled this disk. The `models` job keeps the
  one-command contract without it.
- **Adding a manual setup step to the README instead of the compose graph.**
  `up -d --build` is the whole backend contract (and `yarn dev` the UI's). Bootstrap belongs in the lifespan hook
  or in `depends_on`.
- **Drawing an arrowhead on a `conflicts_with` edge, or sorting disputes so the
  newer belief reads first.** Both are the UI quietly answering the question the
  resolver refused to answer.
- **Hiding the keep-both verdict behind a menu.** It is the correct answer often
  enough that burying it pushes people toward picking a side they do not believe.
- **Setting `VITE_API_URL` to call the API cross-origin.** The session cookie is
  SameSite=Strict and cookie writes need the CSRF header, so sign-in only works
  same-origin. Keep going through the dev server's `/api` proxy.
- **Switching the frontend back to npm, or dropping `resolutions.vite`.** Yarn is
  the package manager (`packageManager` in package.json, `yarn.lock`, CI). Without
  the resolution, yarn 1 nests a second vite under vitest.
- **Naming a category in an extraction-prompt example, or editing the category
  list to add guidance.** Both measurably skew a 7B extractor (FINDINGS §7). Add
  guidance as a rule, and re-run the extraction corpus three times before and
  after — one draw is noise.
- **Dropping a memory's disputed counterpart because it did not make top-k.**
  Then the agent reports one half of an open disagreement as settled fact.
- **Reinforcing every memory that retrieval returns.** Being retrieved is not
  confirmation; it would let a stale belief keep itself alive by being
  well-worded. Confirmation goes through ingest's resolver.
- **Ingesting the assistant's reply along with the user's turn.** The reply is
  assembled from memory, so it would re-enter the graph as independent evidence
  for what it was derived from.
- **Logging with `print` or `logging.getLogger()`.** Use
  `core.logger.get_logger(__name__)`. A plain stdlib logger rejects kwargs, and
  its records skip nothing — but a `print` skips the redaction entirely. Request id and user id are bound by
  middleware and inherited automatically; re-passing them as kwargs is noise.
- **Adding a corpus case without a rationale, or "fixing" the corpus when a
  number looks bad.** The corpus is the instrument. Relabelling a case to make a
  gate look good is how an evaluation stops measuring anything.
- **Showing the judge a worked example answer.** A 7B model copies it —
  relation, confidence and placeholder reason — on a third of its calls. Show a
  template with placeholders.
- **Trusting the confidence number an LLM writes.** Use token probabilities; the
  written value is canned.
- **Setting the cosine bands by hand, or keeping them when changing embedding
  model.** They belong to the model. `run_eval.py --calibrate`, then add the pair
  to `CALIBRATED_THRESHOLDS`.
- **Asking the model again to catch an error it is confident about.** A second
  call shares the first one's prior: the replacement check agreed with the judge
  at 1.00 that ownership is exclusive. When the model is certain and wrong, read
  the words instead (the duplicate guard, `states_a_change`).
- **Relaxing the person-role rule because the judge is confident.** It was
  confident — at 1.0 — on every belief it lost. Confidence is not the signal for
  this class; the words are.
- **Keying a resolution rule on the extracted category.** The extractor files
  "Sara owns billing" as `fact`; a `person`-only rule passed every corpus test and
  failed the first live one. The corpus skips extraction, so check live.
- **Validating a fix on the cases that inspired it.** Write held-out cases first,
  label them before running, and include one labelled against the fix so its cost
  is visible.
- **Adding one more judge rule to fix the last failing case.** Tried for the
  shared-ownership case: it fixed that and broke another. Check the whole sweep,
  twice, before keeping a prompt change.
- **Reporting one blended accuracy number for the resolver.** Belief loss and
  stale beliefs trade against each other as the gate moves; a single figure hides
  exactly the thing you are trying to tune.
- **Skipping the cheap category/subject filters and judging everything.** Turns a
  cheap operation into an expensive one for no accuracy gain.
