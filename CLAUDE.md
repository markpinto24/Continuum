# CLAUDE.md — Continuum

Project brief for Claude Code. Read this before touching anything.

This file lives at the **repo root**. Continuum is one repository with three
projects under it — `continuum-be`, `continuum-fe` and `continuum-sdk` (the
agent client and MCP server) — and no workspace tool binding them. They build,
test and deploy independently; this brief, the ignore rules and CI are the only
things that span them.

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
| Dictation | faster-whisper (local Whisper, CPU, int8) | speech to text on the server |
| Spoken answers | Piper (local neural voice, ONNX, CPU) | read-aloud and autopilot; browser voice only as fallback |
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
├── requirements.md                   Personal (life) mode beside Workspace — the plan, now built
├── TODO.md                           the improvement backlog, in iterations
├── DEPLOY.md                         free public deployment: one Oracle VM, Caddy, HTTPS
├── .env.example                      template for continuum-be/.env (uv run)
├── .gitignore                        covers every project
├── .github/workflows/
│   ├── backend.yml                   paths: ['continuum-be/**']
│   ├── frontend.yml                  paths: ['continuum-fe/**']
│   ├── sdk.yml                       paths: ['continuum-sdk/**']
│   └── nightly-eval.yml              the resolution corpora on real models; gates belief loss
├── continuum-sdk/                    ← memory for other agents (HTTP only)
│   ├── src/continuum_sdk/client.py   Continuum / AsyncContinuum: remember, recall,
│   │                                 disputes, settle (the person's words), chat
│   ├── src/continuum_sdk/mcp_server.py ★ `continuum-mcp`: four tools over stdio
│   ├── src/continuum_sdk/prompt.py   recall as text a model reads: disputes, maybes marked
│   ├── examples/agent.py             any OpenAI-compatible model + Continuum, one loop
│   └── README.md                     long-term memory for your agent in 5 minutes
├── continuum-be/
│   ├── pyproject.toml                uv project, deps, ruff + pytest config
│   ├── local.yml                     ★ the Docker stack: qdrant, postgres, api
│   ├── production.yml                ★ the public stack: + ollama, web (Caddy); only web exposed
│   ├── .envs/.local/.api · .postgres its settings (tracked local defaults;
│   │                                 personal ones in gitignored *.override)
│   ├── .envs/.production/*.example   production templates; the real files are gitignored
│   ├── alembic.ini                   migration CLI; DB comes from DATABASE_URL
│   ├── uv.lock
│   ├── .dockerignore
│   ├── Dockerfile                    multi-stage uv build, non-root, healthcheck
│   ├── README.md                     backend setup + architecture
│   ├── scripts/
│   │   ├── seed_demo.py              end-to-end demo incl. a reversed decision
│   │   ├── run_eval.py               Phase 5 harness: record once, sweep free
│   │   ├── backup.py · restore.py    Qdrant snapshots + pg_dump, and back
│   │   ├── backup-production.sh      the same for production.yml, cron-ready
│   │   └── recheck.py                re-check stored memories under today's rules
│   ├── src/continuum/
│   │   ├── main.py                   app factory, lifespan, scheduler wiring
│   │   ├── config.py                 all settings (pydantic-settings)
│   │   ├── core/
│   │   │   ├── logger.py             ★ get_logger(); line/JSON output; redaction
│   │   │   └── middleware.py         request id, timing, context binding
│   │   ├── clients/                  ← thin, owned I/O adapters
│   │   │   ├── authdb.py             ★ users, sessions, API keys (SQLAlchemy)
│   │   │   ├── labels.py             resolution labels (SQLAlchemy)
│   │   │   ├── learning.py           rejections, answer ratings, approved rules
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
│   │   │   ├── feedback.py           Decision → ExpectedAction, labels, evidence
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
│   │   │   ├── voice.py              spoken answers: local Piper voice
│   │   │   ├── sharing.py            ★ private memory → shared space, via resolver
│   │   │   ├── agreement.py          ★ replies to suggestions: whose words, guards
│   │   │   ├── pending.py            pending ("maybe") items: confirm/decline/unsure
│   │   │   ├── feedback.py           ★ decisions → labels, gate evidence, export,
│   │   │   │                           calibration curve, rule suggestions
│   │   │   ├── corrections.py        ★ reject a misreading; forget (redaction)
│   │   │   ├── summaries.py          per-subject summaries: derived, never evidence
│   │   │   ├── keywords.py           keyword terms + score for retrieval
│   │   │   ├── chat.py               ★ prompt assembly; surfaces disputes
│   │   │   ├── subjects.py           one entity, one slug (atlas = atlas-project);
│   │   │   │                           a missing subject from the words; two words
│   │   │   │                           for one thing meet (gym ~ workout) ★
│   │   │   ├── recheck.py            backfill: stored memories through the resolver
│   │   │   └── reindex.py            ★ re-embed on model change; crash-safe
│   │   ├── evaluation/               ← Phase 5: the instrument
│   │   │   ├── README.md             what each metric means, how to read it
│   │   │   ├── FINDINGS.md           ★ what the first real run found
│   │   │   ├── baselines/            recorded runs, replayable at any gate
│   │   │   ├── preflight.py          ★ can the embedder see an entity swap?
│   │   │   ├── calibrate.py          ★ derive both cosine bands for a model
│   │   │   ├── subjects.py           ★ when do two slugs name one thing? threshold
│   │   │   ├── types.py              Action vocabulary, corpus + outcome models
│   │   │   ├── corpus.py             strict loading; rejects unlabelled cases
│   │   │   ├── corpus/*.yaml         ★ the labelled data (+ *-personal.yaml)
│   │   │   ├── runner.py             executes cases against the REAL resolver
│   │   │   ├── metrics.py            ★ pure scoring, replay, sweep, recommend
│   │   │   ├── extraction.py         token matching + the Mem0 baseline
│   │   │   ├── retrieval.py          recall@k from "should have come up"; weight sweep
│   │   │   └── report.py             plain-text rendering
│   │   └── api/
│   │       ├── deps.py               ★ DI wiring; get_principal = identity
│   │       ├── router.py             router aggregation
│   │       └── routes/               health, auth, admin, ingest, memories,
│   │                                 conflicts, decay, chat, speech, feedback
│   │                                 (memories: + /share /reject /forget /export
│   │                                 /summaries/refresh; conflicts: + /resolve-team;
│   │                                 feedback: + /rules /calibration /answer)
│   └── tests/
│       ├── test_core_logic.py        parsing, coercion, lifecycle (pure)
│       ├── test_resolution.py        ★ confidence gate, category policy
│       ├── test_decay.py             half-lives, idempotence, floors
│       ├── test_retrieval.py         ★ ranking formula, dispute assembly
│       ├── test_chat.py              ★ stream shape, disputed prompt, write-back
│       ├── test_evaluation.py        ★ the instrument, before it is trusted
│       ├── test_auth.py              ★ every route guarded; isolation; refusals
│       ├── test_feedback.py          ★ decision→label mapping; gate evidence
│       ├── test_sharing.py           ★ sharing via the resolver; visibility
│       ├── test_voice.py             synthesis refusals, its own rate limit
│       ├── test_learning.py          ★ calibration curve + gate; rules; team evidence
│       ├── test_corrections.py       ★ reject restores; forget leaves no copy
│       ├── test_team_check.py        ★ private vs team, never touching the team
│       ├── test_summaries.py         derived, never a neighbour, forgotten with sources
│       ├── test_recheck.py           backfill: dry run writes nothing; never merges
│       ├── test_modes.py             ★ two graphs per person: header, keys, by id, resolver
│       ├── test_personal_model.py    ★ health, relationships, name swaps, Personal decay
│       ├── test_personal_privacy.py  health never logged, never exported by default
│       ├── test_pending.py           ★ a maybe never retires or is retired; fades
│       ├── test_agreement.py         ★ echoes, health, questions, refusals, hedges
│       └── test_ingest_pipeline.py   end-to-end vs in-memory Qdrant
└── continuum-fe/                     ← the belief graph UI
    ├── package.json · yarn.lock      yarn 1; `resolutions` pins one vite
    ├── vite.config.ts                dev server + /api proxy (no container)
    ├── Dockerfile · Caddyfile        production only: built UI + /api proxy + HTTPS
    └── src/
        ├── lib/
        │   ├── types.ts              ★ the backend contract, mirrored by hand
        │   ├── api.ts                thin typed client + SSE chat stream
        │   ├── sse.ts                ★ incremental SSE framing
        │   ├── vad.ts                ★ speech start/end; the 2-second silence rule
        │   ├── voice-player.ts       ★ ordered, prefetched spoken chunks
        │   ├── speakable.ts          markdown → speech; finished sentences
        │   ├── greeting.ts           the session's opening line
        │   ├── pending.ts            answering a pending item (chat, inbox, Lumen)
        │   └── memory-style.ts       ★ colour/size vocabulary, shared canvas+DOM
        ├── hooks/                    use-resource, use-recorder, use-dictation,
        │                             use-voice, use-autopilot ★, use-element-size,
        │                             use-mode ★ (Personal | Work, set before any fetch)
        ├── components/
        │   ├── brand.tsx             the mark + wordmark (currentColor)
        │   ├── auth-screen.tsx       sign-in + first-run admin setup
        │   ├── account-dialog.tsx    API keys, password, users (admins)
        │   ├── belief-graph.tsx      ★ the Three.js scene
        │   ├── memory-detail.tsx     provenance, edges, reinforce/restore
        │   ├── contradiction-inbox.tsx   + why it escalated, learning panel
        │   ├── learning-panel.tsx    what your decisions say about the gate
        │   ├── chat-panel.tsx        ★ streaming, citations, disputes, autopilot
        │   ├── turn-outcome.tsx      "Remembered: …" + Undo; questions with options
        │   ├── autopilot-overlay.tsx ★ Lumen's full-screen HUD: reactor, waveform
        │   ├── voice-settings.tsx    Settings → Voice: pick, hear, speed
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
workspace. Independent projects under one root is the design: the backend is
a uv project and knows nothing about the frontend or the SDK, and CI runs each
only when its own directory changes. Adding a workspace layer would couple
things whose only real relationship is an HTTP contract. The SDK imports
nothing from the backend — not even its models — for the same reason.

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
- **Mode picks one of the person's OWN graphs, never another person's.** The
  web UI names it on every request (`x-continuum-mode`; `?mode=` on a GET, for
  download links). An API key carries its mode for life (`api_keys.mode`); a
  header asking for the other one is a 403, an unknown mode a 400 — never a
  default. Routes use `principal.owner` / `principal.owners()`, not `user_id`, for
  anything that reads or writes memories; labels and rules stay keyed on the
  person. Where an account opens is `users.default_mode` (`PATCH /auth/mode`,
  session only: a key cannot move it).
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
- Lockout and rate limits count in process memory by default — correct for one
  uvicorn process. `REDIS_URL` moves them to Redis (`RedisWindow`: one Lua
  script trims, counts and adds, so two workers cannot both slip under a limit),
  shared by every worker; `production.yml --profile scale` adds the Redis. An
  unreachable Redis fails open, logged. Not yet multi-worker safe: the chat's
  per-graph write lock, the decay scheduler and the startup re-embedding are
  all per process (TODO).
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
| `escalations` | What the resolver thought when it escalated: judge relation, probability, similarity, gate. Graded against the person's decision later |
| `shared_by` / `shared_by_email` | On a memory in the shared space (`user_id == "_shared"`): who put it there |
| `shared_as` | On a private memory that was shared: the shared memory that now carries it. Retired by that edge, never deleted |
| `reminders` / `decided_at` | On a `pending` memory: how often it was put to the person, and when they answered |

**Each person has two graphs.** Workspace is owned by the account id
(unchanged, so every memory stored before modes stays put); Personal by
`{id}:personal` — a `:` no account id can contain. `graph_owner()` builds it,
`mode_of()` reads it back, and everything that differs per mode keys on the
owner: decay (`PERSONAL_HALF_LIFE_DAYS` — a sister is a sister in two years;
health never fades), the health and relationship rules, the chat prompt, the
extraction prompt. `visible_owners()` gives a Personal graph only itself: it
never sees `_shared`, and a Personal memory cannot be shared. Personal adds three
categories — `health` (never decays, never changes silently, out of summaries,
logs and default exports), `routine` (45 d) and `goal` (120 d); a Workspace
extraction that names one is filed as `fact`.

**The shared team space is just another graph**, owned by `SHARED_SPACE =
"_shared"` — an id no account can take (account ids start with a letter or
digit). Resolution, disputes, decay and labels therefore work on it unchanged.
Reading is across graphs (`visible_owners()` = your id + `_shared`); writing and
resolution stay inside one graph, so a decision never reaches from the shared
space into anyone's private memories.
| `source_id` / `source_excerpt` | Traceability from any assertion back to its origin text |

**Pending is said, not decided.** `pending` ("maybe", "I'll think about it")
is kept and never believed: it is never a resolution neighbour, never retires
anything, is never retired, never decays, and is retrieved only into its own
unnumbered **PENDING** prompt section. The person settles it with a button or a
spoken answer — `confirm` (it becomes a belief and is judged against the graph
only now), `decline` (status `declined`: out of the way, kept for audit), or
`unsure` (stays pending, counts as a reminder). Asked 3 times, or after 30 days,
it fades to archived. Restating it in your own words confirms it.

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
   ▼  canonicalise subjects onto slugs the graph already uses (subjects.py);
   │  a missing subject is taken from the words when they name exactly one
   │
   ▼  ResolutionService.resolve(fact, neighbours)
   │
   ├─ score ≥ dup band ................. DUPLICATE     → reinforce, no write
   │     └─ unless a number, date, name or negation differs → judged instead ★
   ├─ incomparable category ............ NEW           → free, no LLM call
   │     (decision, fact, constraint, person compare with each other ★;
   │      preference only with preference)
   ├─ different subject ................ NEW           → free, no LLM call
   │     └─ unless two words mean one thing (gym ~ workout, home ~ flat):
   │        compared, never renamed, and judged without either slug ★
   │        — never for names, look-alikes (sara ~ sarah-k) or `self`
   ├─ either side is an event .......... NEW           → free, no LLM call
   └─ score ≥ conflict band, same kind . judge (LLM call, reason first)
         (one candidate: the same named subject first, then one that means
          the same, then the closest)
         ├─ duplicate ................... reinforce
         │     └─ each side names someone the other does not → CONFLICT ★
         ├─ supersedes, p ≥ 0.80 ........ SUPERSEDES   → write edges, retire old
         │     ├─ health on either side (category; words in Personal) → CONFLICT ★
         │     └─ a role (owns, maintains, reviews, leads… and in Personal
         │        partner, lives, works…) and the new statement never says the
         │        old holder stopped → CONFLICT ★
         ├─ supersedes, p < 0.80 ........ CONFLICT     → escalate to human ★
         ├─ conflict .................... CONFLICT     → escalate to human
         └─ independent ................. store, no edges
               └─ Personal partner/home/job subject, two different names → CONFLICT ★
```

**The bands belong to the embedding model** — bge-m3: duplicate 0.86, conflict
0.45 — and come from `CALIBRATED_THRESHOLDS` in `config.py`, derived by
`run_eval.py --calibrate`. Never set them by feel. So does the subject band
(bge-m3: 0.67, `CALIBRATED_SUBJECT_THRESHOLDS`, from `run_eval.py --subjects`
on `corpus/subjects.yaml`); a model without one matches subjects by spelling.

**Subjects meet by meaning, names by spelling.** Slug similarity measures
relatedness, not identity — frontend / backend scores like a synonym, anna /
ana higher than any — so a pair that meets is only *compared*, never renamed,
and the judge is shown neither slug (its "different subject is independent"
rule would otherwise undo the match). A slug written with a capital in the
memory's own words is a name and matches by spelling only, as do look-alikes
(`looks_alike`) and `self`. A sentence-initial capital reads as a name too:
"Billing runs through Stripe" stays apart from `payments` (FINDINGS §13).

**`p` is a probability, not a number the judge wrote.** It is the model's token
probability for the relation it chose, read from logprobs. A 7B judge's written
confidence is canned (every supersede came back at exactly 0.95), which left the
gate inert. The written value is kept for audit and as a fallback for providers
without logprobs.

**The duplicate guard exists because DUPLICATE is the only verdict that discards
the incoming fact.** Embeddings are measurably blind to exactly the changes that
make a contradiction — one number, one name — so near-identical scores are not
trusted when the words say otherwise.

**The same guard reads the judge's DUPLICATE.** In the first Personal run the
judge folded "Sam is the user's partner" into "Priya is the user's partner" —
"a different name for the same relationship" — at 0.93. `swapped_names` is
narrower than `material_difference` on purpose: it overrides the judge, so it
fires only when each statement names something the other lacks ("lives in
Kothrud, Pune" adds detail; "Sam" for "Priya" is a swap).

**A reply to an answer reads the answer — as context, never as a source of
facts** (`services/agreement.py`). The chat write-back hands ingest the answer
the turn replies to (`IngestRequest.proposal`, never client-settable). Whose
words a fact is, is read from the words: at least half its content words in the
reply makes it the user's own; anything else was *adopted*, and only a choice
survives — never a fact about the user, never anything naming a dose, a
supplement or a health word. An adopted fact never reinforces (an echo is not
evidence). A hedged reply makes what it adopts pending; a reply opening with a
no adopts nothing; a question makes no choice at all. The instructions for this
live in the reply's own framing, not the system prompts (FINDINGS §12).

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

### ✅ Phase 8 — Learning from decisions (done, this release)
- **Every decision is a labelled example.** Ingest records on the incoming memory
  what the resolver thought (`Memory.escalations`); settling the conflict — in
  the inbox or the chat — stores a `resolution_labels` row (Postgres, migration
  0002) pairing that with what the person said. Older→newer, like the corpus:
  newer holds → `retire`, both hold → `store`, older holds → `escalate` (asking
  was right; auto-retiring would have destroyed a true belief)
- **Gate evidence** (`GET /feedback/evidence`): only escalated `supersedes`
  verdicts below the gate count — exactly what a lower gate would have
  auto-applied. One refutation rules out lowering it; zero in n bounds the error
  below 3/n; below `FEEDBACK_MIN_GATE_EVIDENCE` (15) it says keep the gate.
  Role-rule escalations and over-escalated conflicts are counted separately
- **Export** (`GET /feedback/export`) as Phase 5 corpus YAML, each case validated
  against the schema; `run_eval.py --add-cases FILE` scores the resolver on
  real decisions. Live: a kept-both second owner came back as
  `want store, got escalate` — over-escalation that used to leave no trace
- **Disputes are settled where they come up.** The chat's dispute card has
  "This holds" per side and "Both are true"; the inbox explains why each pair is
  there, from the recorded escalation, and shows the learning panel
- Nothing retunes itself. The report recommends; the gate stays a product
  decision

### ✅ Phase 9 — Spoken answers, autopilot, greeting (done, this release)
- **Read-aloud now speaks with the server's local Piper voice** (`services/voice.py`,
  `POST /speech/synthesize` → WAV). The browser's speechSynthesis failed silently
  for this user: on Linux it needs speech-dispatcher (not running), and Brave
  limits voices to resist fingerprinting. Piper: ~20x real time on a CPU, 63 MB
  voice, preloaded at startup, verified by round-tripping its audio through
  Whisper. The browser voice is kept only as a fallback
- `VoicePlayer` synthesises each chunk as it is queued and plays strictly in
  order, so the next sentence is ready while the current one plays
- **Autopilot:** hands-free conversation. `vad.ts` learns the room's noise floor,
  starts an utterance after 120 ms of voice and **ends it after 2 s of continuous
  silence** (a shorter pause mid-sentence does not end it); coughs under 300 ms
  are dropped. The clip goes to local Whisper, the text through the normal chat
  path, and the answer is spoken **sentence by sentence as it streams**. The
  microphone is ignored while the answer plays, so it never hears itself. "Stop
  speaking" interrupts; saying "stop autopilot" ends it. Turns are written into
  the chat underneath the overlay, so ending autopilot reveals the transcript
- **Greeting:** every session opens with a local, instant greeting — time of
  day, name when the user id reads as one, open disputes. Written in chat; spoken
  when autopilot starts. A remembered autopilot asks for one tap first: browsers
  allow neither the microphone loop nor audio without a gesture
- Synthesis has its own rate limit (240/min): autopilot asks once per sentence

### ✅ Phase 11 — Lumen, commands mid-answer, voices (done, this release)
- **Autopilot is Lumen** — a unisex name, since any of the 12 voices may speak
  it. "Talk to Lumen" starts it; it greets as itself
- **Commands while it speaks (barge-in).** During thinking and speaking a second
  listener runs: thresholds 2.5x the learned room level (its own voice comes back
  through the speakers), and 0.7 s of quiet ends a command. Only commands are
  acted on, and only with its name attached — "stop, Lumen" interrupts (answer
  aborted, it listens again), "thank you, Lumen" ends with a spoken goodbye.
  Ordinary speech over its answer is ignored, and a command phrase that is in
  its own current answer is never taken as one. The name was chosen for the
  microphone: across four voices Whisper wrote "Lumen" 11 times in 12 (once
  "Lumin", accepted). "Avery" was rejected because it collides with "every"
- **Voices in Settings.** 12 single-speaker Piper voices (US/British, female/male),
  each verified to exist; "Hear it" previews before choosing; speed 0.8–1.5x.
  Saved to the account (`users.tts_voice` / `tts_speed`, migration 0003), so it
  follows the person to any browser. The UI sends the choice with each
  synthesis; the server accepts only catalogued voices — it never downloads a
  name a request makes up. Three voices stay loaded (LRU), ~60 MB each on disk
- API keys panel now says what a key is for, in plain words

### ✅ Phase 23 — The interface at scale (done, this release)

TODO Iteration 8, checked in headless Brave on 600 synthetic memories.

- **What the graph shows** (`lib/graph-view.ts`, pure): filter by status (the
  legend's rows are toggles), kind and age (`GraphViewBar`), and group by subject
  — on by itself above 300 nodes, a cluster disputed if anything in it is,
  clicking one opens it. 600 nodes: 32 fps grouped against 8 flat (software
  rendering). **A filter never shows half a dispute**: the hidden side of a
  visible disagreement comes back, counted in the bar
- The UI asks for up to 2000 memories and the API now says when there were
  more (`GraphResponse.truncated`) — it used to cut at 500 without a word
- **Phones**: below 640 px the panel is a full-width sheet with no resize handle,
  the header shows icons with their names for screen readers, and its controls
  scroll inside it
- **Keyboard and screen readers**: `GraphList` is the canvas as a list — hidden
  until focused, disputes first, one tab stop, arrows and Enter; every
  icon-only button has a name

### ✅ Phase 22 — Production readiness, in part (done, this release)

TODO Iteration 7 (FINDINGS §19–§20). Off-machine backups and the VM deploy wait
on the person.

- **A memory is data, never an instruction.** Measured first on the real model
  (`corpus/injection.yaml`, `run_eval.py --injection`, 3 runs a case): with only
  the old rule at the top it answered "PWNED", spoke as a pirate, appended an
  exfiltration link and — once in three — declared a real dispute settled "as
  instructed". Now each memory is quoted on one line (no forged entries or
  headers), the rule is repeated after the memories, and a memory written as
  orders to the assistant (`addresses_the_assistant`, 0 false positives on 250
  corpus statements) reaches the model only as a withheld note. 0 of 18 attacks
  obeyed. Cost cases: a recorded reply-language or answer-length preference is
  not honoured — but it was not before either (3/3 both ways)
- **Limits in Redis** when `REDIS_URL` is set: a contract test runs both
  backends (Redis with `TEST_REDIS_URL`, in CI); ten simultaneous requests
  through two clients let exactly five through
- **Activity** (`GET /feedback/activity`, the Inbox's Activity panel): per day,
  memories taken in, times it asked, beliefs it retired on its own (no person's
  decision behind them), decisions (and how many via an agent), and the near
  misses — supersedes the gate held for a person, their confidence against the
  gate. Small multiples in the accent, titled, a shared hover readout, a table
  view; checked in headless Brave on a seeded throwaway instance

### ✅ Phase 21 — Memory for other agents (done, this release)

TODO Iteration 6 (FINDINGS §18).

- **`continuum-sdk/`**, a third independent project: `Continuum` and
  `AsyncContinuum` over the HTTP API (remember, recall, disputes, settle, chat)
  and **`continuum-mcp`**, an MCP server (mcp 2.x `MCPServer`) with four tools.
  Recall reads as text for a model: confidence and date per belief, DISPUTED
  with both sides, UNDECIDED marked — `prompt.py`, shared with the example agent
- **An agent settles only on the person's words.** `/conflicts/resolve` and
  `/resolve-team` take `said`; an API key without it is a 422. The words and
  `settled_via` (session | api_key) are kept on the label (migration 0006), and
  Forget blanks them with the rest
- Verified end to end on a throwaway instance (its own Qdrant, SQLite accounts,
  real Ollama): SDK, a real MCP client over stdio, and `examples/agent.py`
- **bge-m3 is pinned to the CPU on this machine** (FINDINGS §17): on the RTX
  4050 it returned NaN for any text over ~90 characters. `EMBEDDING_MODEL=
  bge-m3-cpu` in the gitignored override; the `models` job creates the alias;
  it shares bge-m3's calibration and collection (`embedding_weights`)

### ✅ Phase 20 — The answer does not wait for memory (done, this release)

TODO Iteration 5 (FINDINGS §16).

- `done` is sent when the answer is complete, with `remembering: true`; the turn
  is written in a shielded background task and reported in a new **`remembered`**
  SSE event. The task starts *before* `done` is sent — a client leaving at
  `done` would otherwise close the stream before the write began
- One graph's writes run one at a time, in order (`ChatService._writes`);
  pending reminders are saved under the same lock, from a fresh read
- The UI frees the input at `done`, shows "Remembering…", and patches each turn
  by its own index, so a late write-back never lands on a newer turn; a
  question the turn itself settled is dropped. Lumen speaks its last sentence
  at `done` and never asks about an item the turn just confirmed
- `scripts/chat_latency.py`: done p50 12.4 s / p95 24.6 s vs remembered 21.5 s /
  40.9 s — what `done` used to wait for

### ✅ Phase 19 — An instrument you can quote (done, this release)

TODO Iteration 4, in part (FINDINGS §15); benchmarks and the head-to-head are
deferred.

- **"+ judged" beside the token matcher**: what the tokens leave unpaired is
  read by a model ("does it state the labelled fact?") behind a number and
  negation guard. Embedding similarity was tried first and matched tea to
  coffee on held-out pairs. `corpus/matcher.yaml` (26 pairs, 12 held out),
  `run_eval.py --matcher`; held out 11/12
- **Nightly gate**: `.github/workflows/nightly-eval.yml` runs both resolution
  corpora on Ollama on the runner and fails on belief or merge loss
  (`--max-belief-loss 0 --max-merge-loss 0`)

### ✅ Phase 18 — Agreement and pending polish (done, this release)

TODO Iteration 3 (FINDINGS §14). Each change measured three runs each side on
cases labelled first.

- **An unprompted maybe is pending**: a hedged lone turn the extractor took
  nothing from is read once more by its own small prompt (`_MAYBE_PROMPTS` in
  `agreement.py`) — framing inside the turn could not beat the system prompts'
  musing rule. 4/4 pending, musing still nothing; agreement F1 → 0.80 ×3
- **A confirmed maybe reads as decided** (`services/wording.py`): one guarded
  rewrite — a changed number, name or negation, a kept hedge, or lost words
  keep the old wording. A restatement takes the person's own words. The maybe's
  wording stays in `tentative_wording`
- **Adopted choices fade at one rate**: `Memory.adopted` is stored;
  `ADOPTED_CHOICE_HALF_LIFE_DAYS` = 90 whatever the category (mirrored in
  `memory-style.ts`)
- Long plans already keep their shape (12/12 runs); cases kept as a check

### ✅ Phase 17 — Subjects by meaning (done, this release)

TODO Iteration 1. The same split came back as `gym` in one run and `workout` in
the next, and the subject filter never compared them (FINDINGS §13).

- **Two words for one thing meet** (`SubjectRelations`): different slugs whose
  "my {slug}" embeddings score ≥ 0.67 (bge-m3, `CALIBRATED_SUBJECT_THRESHOLDS`)
  are compared — never renamed — and judged with neither slug shown. Names
  (a capital in the memory's own words), look-alikes and `self` match by
  spelling only. An embedding failure compares (fails toward the judge)
- **Measured first**: 95 labelled slug pairs (`corpus/subjects.yaml`, 17 held
  out), `run_eval.py --subjects`. Slug similarity is relatedness, not identity
  (AUC 0.74); held out, 9/9 must-meet pairs meet, sara / sarah-k stays apart
- **Held-out resolution cases** (11, labelled first): before, all 5 changes
  filed under a synonym were silently stale; after, 4 retired or asked, the
  fifth a labelled known gap (a sentence-initial capital reads as a name). All
  5 cost cases judged independent — no false disputes. Both corpora, two
  passes: belief loss and merge loss 0%; stale 0% outside that known gap
- **The reverted rule is back**: an agreed choice gets a real subject, never
  `self` — 11 of 18 facts on `self` → 0 of 18, subjects that meet the label's
  15–21% → 100%, precision and recall unchanged, three runs in three
- 545 backend tests

### ✅ Phase 16 — Agreements, pending memories, questions with options (done, this release)

Found in use: "it sounds good for me for now", after the assistant proposed a
workout split, stored nothing. Planned in `requirements.md` Part 2, decisions
asked first; both modes.

- **Agreements are remembered**: the answer a turn replies to is read as
  context, the choice the user took up is stored in their name with their reply
  as its source, and the turn says so — "Remembered: …" with **Undo** (= *this
  isn't a real fact*)
- **A maybe is pending**: stored, never believed (see Data model). Raised when
  its topic comes up — the answer gives its own one-line view, and a **question
  card** offers *Yes / Still unsure / No*, fixed server actions. The inbox has
  **Disputes | Undecided**; the graph draws pending nodes with a dashed ring.
  **Lumen** asks aloud after its answer and takes a spoken yes / not sure / no by
  word match, only right after the question
- **Measured** (FINDINGS §12): agreement corpus (25 cases, both modes, labelled
  first) recall 5.6% → 58–68%, precision 25% → 73–81%, every matched maybe
  pending, 0 forbidden statements (echoes, health, refusals, questions). Both
  system prompts byte-identical, so the Work and Personal corpora cannot move —
  the first version put the rules there and cost Work F1 0.74 → 0.56–0.64
- **Live check** (throwaway instance, real Ollama, both modes) found three bugs
  the corpus could not: a question replying to an answer was stored as a
  decision; the first turn of a session skipped the question guard; and
  `swapped_names` read "Postgres"/"PostgreSQL" as a swap. All fixed, re-run
  live: every turn as designed
- **Not achieved**: an unprompted "maybe I'll start running on Sundays" stores
  nothing (decided 2026-10-16 that it should be pending) — the rule that would
  do it caught 0 of its cases and cost Work. A confirmed maybe keeps its hedged
  wording ("will think about adding…"). An adopted choice is filed as decision
  or routine run to run
- 506 backend tests, 184 frontend tests

### ✅ Phase 15 — Personal mode: a memory for your life (done, this release)

Continuum is a personal assistant first, with Work as the second mode — built
from `requirements.md`, on its defaults for §10 (strictly separate modes, one
Lumen, finance later, "Personal / Work", existing accounts stay on Work with a
one-time hint).

- **Two graphs per person, never mixed.** Personal is owned by
  `{id}:personal`; Work keeps the account id, so nothing stored before moved.
  The mode is a header the UI sends (`?mode=` on download links), or the key's
  own mode — a Work key asking for Personal is a 403, a typo a 400. A memory of
  the other mode is a 404 by id; Personal never reads `_shared` and cannot share.
  Migration 0005: `users.default_mode`, `api_keys.mode`, `graph_owner` on the
  feedback tables (labels, rejections, answers are counted per graph)
- **Personal memory model.** `health` (never decays; any supersede is a forced
  conflict, on the category or — in Personal only — the words), `routine` (45 d),
  `goal` (120 d); people kept 730 d. Relationship words (partner, lives,
  works…) join the role rule in Personal, with life's own change words
  (broke up, divorced, relocated, quit…). Health is out of summaries, logs and
  exports unless the person ticks the box
- **The name-swap guard** (found by the first Personal run): the judge called
  a new partner a duplicate of the old one at 0.93, discarding the new fact. A
  judge DUPLICATE where each side names someone the other lacks now escalates,
  in both modes; in Personal, a partner/home/job named two ways is not
  "independent" either
- **Extraction:** a Personal prompt (9 categories, the subject is the entity
  other than the user, "partner"/"home"/"job" for one-at-a-time relationships,
  job details left to work memory). Work extraction is unchanged and coerces any
  Personal-only category to `fact`
- **The assistant:** a warm Personal system prompt (no diagnosis or treatment
  advice), a greeting per mode, "Only you" in place of the share toggle
- **Interface:** Personal | Work in the header (remembered per browser, the
  account's default on first load), one workspace per mode keyed on it, a rose
  accent and warm sky for Personal, the first-run "What will you use Continuum
  for?", Settings → Modes, a mode per API key, per-mode export with a health
  opt-in, a per-kind explanation of every forced escalation
- **Measured** — see FINDINGS §11. Extraction (3 runs each, Personal corpus,
  20 cases): category 41.7% → 84.6–92.3%, subject 0% → 30.8–40.0%, forbidden
  20% → 0%, F1 0.67 → 0.65–0.77. Resolution (33 cases, two passes): belief loss
  0%, merge loss 0% (3.5% before the guard), stale 0%. The cost: escalation
  ~68% — the judge calls most stated life changes "conflict". Work corpus, two
  passes: 0% loss, accuracy 80.0% / 76.4% vs the baseline's 80.0%, the guard
  never fired, the moved cases were judge noise
- Verified in a real browser on a throwaway instance (SQLite accounts,
  in-memory Qdrant, real Ollama): first-run "Both" opens Personal; life and work
  notes land in their own graphs; a stated allergy change went to the Personal
  inbox and nowhere else; a Personal key read 5 memories and was refused Work

### ✅ Phase 14 — The interface, redesigned (done, this release)
- **Inter and JetBrains Mono, bundled** (`@fontsource-variable/*`, no Google
  request). The UI asked for `system-ui`, which on this machine resolved to a
  monospace face — the whole app looked like a terminal
- **Cyan interface accent, separate from the status colours.** The graph owns
  emerald / amber / slate; a button the same green as an active node blurred UI
  and data. Status colours are unchanged and still come from `memory-style.ts`
- Sign-in: one glass card over a slow constellation. Header: wordmark, a health
  dot with the belief count, as-of date, archived, refresh, settings, sign out.
  Legend folds its key away ("How to read", remembered per browser)
- Chat: assistant mark, accent user bubbles, typing dots, memory chips; one
  composer with Private/Team, dictation, a **Lumen** button and send inside it
- **Lumen is a full-screen HUD**: counter-rotating arc rings, a tick ring, a
  96-bar radial waveform driven by the microphone level, a reactor core that
  breathes with your voice, ripples, scan line and grid. Colour follows the
  state — cyan listening, amber transcribing, violet thinking (a crest circles
  the ring), teal speaking — with the question and a live subtitle of the answer.
  Motion is written straight to the DOM per frame, never through React state;
  `prefers-reduced-motion` stops it
- Settings is a two-column dialog with side navigation; memory edges show the
  linked memory's words, status and date instead of an id
- **The graph**, rebuilt for stability: no bloom pass (it disables the canvas's
  antialiasing, so thin edges shimmered as the camera moved), no point starfield
  (sub-pixel stars twinkled), no auto-rotation; orbit controls with damping. The
  sky — gradient, faint nebulae, fixed stars — is painted once into a texture.
  Nodes glow with an additive sprite in their status colour; disputed ones
  breathe. Edges curve slightly so two relations between one pair never hide
  each other; particles flow along arrows (new → old)
- **Labels are HTML, laid out every frame without overlap**: projected to screen,
  sorted by priority (selection, its neighbours, disputed, confidence, nearness)
  and placed greedily; a label that would collide fades out until there is room.
  With a selection, only its neighbourhood is labelled, in bold glass pills
- Selecting a memory anywhere moves the camera to frame it **and its
  neighbours**, keeping the current angle, at a distance set by their spread
- **The sidebar is a floating liquid-glass panel** over a full-width graph
  (`.liquid-glass`: translucent, blur + saturation, a specular top sheen). The
  camera's view offset centres the graph in the area the panel leaves open
- Logos: the header uses the mark on its tile (`BrandTile`); the tab icon is
  the bare mark (`public/logo.svg`)
- **The panel collapses** (its header button, or Ctrl+\) to a glass rail of
  Chat / Inbox / Memory buttons, plus **Ask Lumen** (it starts Lumen full-screen
  with the panel left collapsed; if Lumen cannot start, the chat opens to say
  why); remembered per browser. It is hidden with
  `inert`, never unmounted, so the conversation and Lumen keep running
- **Next: Personal mode** — built in Phase 15
- **Edge count fixed.** "2 edges" with one visible: settling a dispute cleared
  the winner's conflict edge but not the loser's, so a stale dispute sat under
  the new arrow. The resolve route now clears both sides, the graph draws a
  dispute only while both memories are still disputed, and a memory shared to
  the team is linked to its shared copy (read from `superseded_by`). The one
  stale edge in live data was cleared
- Checked in a real browser (headless Brave over CDP, fake microphone) against a
  throwaway API and graph; 150 frontend tests, no behaviour changed

### ✅ Phase 13 — Edges across categories, subjects from the words (done, this release)

Found live: "Atlas is using MongoDB" sat next to "Chose Postgres over Mongo"
with no edge. The extractor had filed it as a **fact** with **no subject**; the
old belief was a **decision** about `atlas-project`. The category filter waved it
through as NEW, and the subject-less fact was judged against an unrelated
subject-less memory ("name changed to Mark II") instead.

- **Category families.** `comparable_categories`: decision, fact, constraint and
  person are compared with each other — the extractor files one kind of
  statement under any of them. Preference only with preference; event never
- **A missing subject is taken from the words** when they name exactly one known
  subject (`infer_subject`); naming two, or none, leaves it null
- **The same named subject is judged first.** Only one candidate reaches the
  judge, so a closer-worded subject-less memory no longer takes its place
- **`scripts/recheck.py`** puts stored memories through the same resolver, oldest
  first: fills subjects (re-embedding), writes supersede edges, raises disputes,
  reports duplicates without merging. Dry run by default
- **Held-out cases written and labelled before the change** (6, tag
  `held-out-family`, 3 of them labelled against it). Two passes, same day, same
  judge, against a before-run: the 49 existing cases unchanged; belief loss 0% →
  0%, stale belief 3.6% → 0%, accuracy 76.4% → 80.0%, band misses 9.1% → 3.6%;
  the cost — free decisions 30.9% → 20.0%, escalation 36.4% → 40.0%. The one
  held-out miss is the safe direction: "now uses MongoDB instead of Postgres"
  escalated rather than retired. Pass 2 matched on every changed case; two
  untouched single-memory cases flipped on the judge alone (one each way —
  run-to-run noise, the reason for two passes). Crowded graphs 8/8 both ways.
  Runs kept: `baselines/bgem3-qwen7b-c55-{before-families,families}.json`
- The host `continuum-be/.env` still had nomic and its thresholds pinned (0.94 /
  0.78): the first before-run was meaningless. Fixed to bge-m3 with calibrated
  bands; every host command (`run_eval`, `recheck`, `uvicorn --reload`) reads it

### ✅ Phase 12 — Learning more, remembering better (done, this release)

Self-learning:
- **Team evidence.** `GET /feedback/evidence?scope=team` pools everyone's
  decisions — the gate is one setting for the instance, so pooled evidence
  reaches a verdict sooner. Only counts leave the labels
- **Calibrated gate (opt-in).** A curve from decisions: per confidence band,
  how often people agreed the newer belief held — Laplace-smoothed (one decision
  is not certainty), then made monotone (pool-adjacent-violators). With
  `CALIBRATED_GATE=true` and 30+ decisions the gate compares that number, not the
  raw one; the raw value is still what is recorded, since the curve is built from
  it. Off by default: switching it on is the same kind of decision as moving the
  gate. A judge people keep overruling escalates even when it is sure
- **"This isn't a real fact."** Reject a misreading with a reason: archived, any
  belief it had wrongly superseded comes back, and it becomes an extraction case
  with a `forbid` statement (`--add-extraction`; scored as `forbidden_rate`)
- **Answer feedback.** 👍/👎 on each answer, and "Something missing?" — search,
  mark the memory that should have come up. Exported as retrieval cases;
  `run_eval.py --retrieval FILE` measures recall@k on the live graph and sweeps
  confidence, recency and keyword weights for free from one fetch
- **Rules from "both are true".** Repeated both-hold decisions about one subject,
  and never otherwise, produce a suggestion; a person approves it (an admin for
  the team graph; never an API key). From then on a CONFLICT about that subject
  is stored side by side. A rule never stops a confident supersede — it only
  answers what would have been asked. Revoked rules are kept, with the time

Long-term memory:
- **Private vs team.** Each new private fact is also judged against the team's
  beliefs. A disagreement is flagged on the private memory only
  (`team_conflicts_with`) — even a confident supersede never touches team
  knowledge — and settled by its owner: the team is right, mine holds (shares
  it, so the team graph's own resolver decides), or both. Recorded as a
  `forced` escalation: no gate decided it, so it is no gate evidence. Chat shows
  the dispute even when only the team side was retrieved
- **Keyword + semantic retrieval.** Qdrant full-text index on content; rank uses
  `max(similarity, keyword × 0.7)` so a memory found both ways is not counted
  twice, and a memory naming the entity joins the pool below the similarity floor.
  Live: "Kestrel-9" found its memory at similarity 0.56, keyword 1.0
- **As of a date.** The header's date picker takes the graph and the chat back:
  only what was believed then (`Memory.believed_at`, from `superseded_at` /
  `archived_at`), recency measured from then, and such turns are never
  remembered
- **Summaries.** Subjects with 5+ memories get one, rewritten daily when its
  sources change (fingerprint of ids and disputes — a reinforcement is not a
  change). Never a resolution neighbour, never decays, never an as-of belief,
  marked `[SUMMARY]` in the prompt, superseded by its successor
- **Forget** — the one deliberate exception to "nothing is lost", and only a
  person's act: content, excerpt, subject, escalation reasons and vector are
  replaced; copies in labels, rejections, answer ratings and summaries are
  blanked; the record and its edges stay. Cannot be reactivated
- **Export and backups.** `GET /memories/export` (your graph, every status);
  `scripts/backup.py` snapshots each Qdrant collection and `pg_dump`s Postgres,
  `restore.py --yes` puts them back. Backups are gitignored
- Qdrant `nofile` raised to 65535 in `local.yml`: at Docker's 1024 a fourth
  collection failed with "Too many open files"
- Verified live on an isolated instance (throwaway DB and collection, real
  Ollama): the judge called a private "moved to MongoDB" a confident supersede of
  the team's Postgres — flagged on the private side only, team untouched, other
  member's inbox empty
- 381 backend tests, 148 frontend tests

### ✅ Phase 10 — Shared team space (done, this release)
- One shared graph per instance; **private by default, shared explicitly** —
  a memory's "Share with team" button, `share: true` on ingest, or the chat's
  "share what I say" toggle (off at the start of every session)
- **Sharing goes through the shared graph's resolver**, as if a teammate had
  said it: new → created; already known → merged (reinforced, not duplicated);
  a stated change → supersedes the old shared belief; a contradiction → a shared
  conflict in every member's inbox. Only an active memory can be shared — a
  dispute must not leak into team knowledge unresolved
- Every account's chat, search, graph and inbox read their own graph plus the
  shared one. Shared nodes wear a wireframe halo; memories show "Shared by …"
- Verified live: private stays private both ways; a shared fact reached the
  other account's graph and chat; a contradicting share raised a conflict in
  the other person's inbox

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
- **Fonts are bundled.** Never fall back to `system-ui` for the interface —
  on Linux it can resolve to a monospace face. Inter for text, JetBrains Mono
  for code (`--font-sans` / `--font-mono` in `index.css`).
- **The accent is not a status colour.** UI chrome uses `accent` (cyan); green,
  amber and slate mean active, disputed and superseded, everywhere.
- **No bloom pass on the graph.** EffectComposer renders without MSAA; every
  thin edge shimmered while the camera moved. Glow comes from per-node sprites.
- **Graph labels are HTML with collision avoidance, not 3D text.** Sprite labels
  overlapped in every cluster and smeared under bloom.
- **Rendering Lumen inside the panel.** Any `filter`/`backdrop-filter` makes an
  ancestor the containing block for `position: fixed`, so the glass sidebar
  trapped the full-screen HUD inside itself. It is a portal to `<body>`.
- **`.liquid-glass` sets no `position`.** A utility-layer `position: relative`
  overrode `absolute` and dropped the floating panel into the top-left corner.
- **Lumen's motion goes to the DOM, not state.** The waveform and core update
  every frame from `levelRef`; routing that through React would re-render the
  overlay 60 times a second.
- **The mode is set before the first fetch.** `useMode` calls `setMode()` in
  its state initialiser and in its setter, never in an effect: children fetch in
  their effects, which run before the parent's. It is cleared on sign-out, not on
  unmount — StrictMode's rehearsal unmount would clear it between the first
  render and the requests that need it.
- **One workspace per mode, keyed on it.** Switching unmounts the other mode's
  graph, chat and Lumen, so no conversation, selection or in-flight answer
  carries across. The Personal accent is `:root[data-mode='personal']`
  overriding `--color-accent`; the sky tints per mode in `belief-graph.tsx`.
  Status colours never change with the mode.
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

For the public deployment (`production.yml`, Caddy, HTTPS on a free Oracle VM)
follow `DEPLOY.md`.

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
uv run pytest                          # 603 tests, no services needed
TEST_DATABASE_URL=postgresql+asyncpg://continuum:continuum@localhost:5432/continuum_test \
  uv run pytest -k postgres            # the Postgres-only tests (DB is wiped)
uv run alembic revision --autogenerate -m "..."   # after changing db/models.py
uv run alembic current                 # which revision the database is at
uv run ruff check . --fix
CONTINUUM_API_KEY=ck_... uv run python scripts/seed_demo.py   # end-to-end, live

# Phase 5: check the embedder, then one slow pass, then sweep the gate for free
uv run python scripts/run_eval.py --preflight
uv run python scripts/run_eval.py --crowded     # right memory judged in a crowded graph?
uv run python scripts/run_eval.py --subjects    # when do two slugs name one thing?
uv run python scripts/run_eval.py --confirmations   # a confirmed maybe, reworded
uv run python scripts/run_eval.py --matcher     # the judged extraction matcher vs labels
uv run python scripts/run_eval.py --injection   # planted instructions in memory, real model
TEST_REDIS_URL=redis://localhost:6379/0 uv run pytest tests/test_limits.py   # Redis backend
uv run python scripts/run_eval.py --record r.json --max-belief-loss 0   # as a gate
uv run python scripts/chat_latency.py           # p50/p95 per chat turn, real models
```

```bash
# all from continuum-sdk/ — the agent client and MCP server, HTTP only
uv sync --extra mcp --extra dev && uv run pytest
CONTINUUM_API_KEY=ck_... uv run continuum-mcp            # MCP over stdio
CONTINUUM_API_KEY=ck_... uv run python examples/agent.py
uv run python scripts/run_eval.py --record eval-run.json
uv run python scripts/run_eval.py --replay eval-run.json
# decisions from real use, exported from the Inbox → learning panel:
uv run python scripts/run_eval.py --record run.json --add-cases continuum-cases.yaml
uv run python scripts/run_eval.py --extraction --add-extraction continuum-extraction.yaml
uv run python scripts/run_eval.py --retrieval continuum-retrieval.yaml   # live graph
# Personal mode: its own corpora, run in a Personal graph ("eval:personal")
uv run python scripts/run_eval.py --personal --record personal-run.json
uv run python scripts/run_eval.py --extraction --personal              # personal prompt
uv run python scripts/run_eval.py --extraction --personal --prompt workspace   # baseline
uv run python scripts/run_eval.py --agreement            # replies to suggestions
uv run python scripts/run_eval.py --agreement --before   # today's: the reply alone

uv run python scripts/backup.py                       # -> backups/<UTC stamp>/
uv run python scripts/restore.py backups/<stamp> --yes   # replaces current data
uv run python scripts/recheck.py               # dry run; --apply after a backup
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
- **Reading the speech out with the browser's speechSynthesis again.** It is
  silent on Linux without speech-dispatcher and restricted in Brave, and it
  never reports failure. Piper is the voice; the browser is the fallback.
- **Treating speech heard while Lumen talks as a new question.** The microphone
  hears its answer too, so what comes back is often its words mixed with yours.
  While it is busy, only commands with its name count; everything else waits
  until it has finished.
- **Accepting a bare "stop" while it is speaking.** Its own answer can contain
  the word. Mid-answer, a command needs its name ("stop, Lumen"); a lone "stop"
  works only when it is idle.
- **Letting /speech/synthesize load any voice a request names.** It would download
  arbitrary models on demand. Only `VOICE_CATALOGUE` (plus the server default).
- **Auto-starting autopilot audio on page load.** Browsers block both audio and
  a microphone loop without a gesture; it would fail silently. The overlay asks
  for one tap.
- **Resolving across graphs** — letting a shared decision retire a private
  memory, or a private fact supersede team knowledge. Sharing runs the fact
  through the shared graph's resolver instead; losers come from the winner's
  graph only.
- **Copying or moving a memory into the shared space directly.** It would skip
  the resolver: duplicates instead of reinforcement, and contradictions of team
  knowledge that nobody is asked about.
- **Remembering the chat's share toggle across sessions.** Sharing is always a
  choice made now, never a leftover from last week.
- **Letting the evidence report move the gate by itself.** It counts; a person
  decides. The gate is the most consequential setting in the project, and a
  run of confirmations can end with the one refutation that matters.
- **Resolving a dispute from a free-text chat reply** ("yes, Mongo now") with
  an LLM reading intent. That is a second LLM call arbitrating a contradiction
  (commitment 2). The person presses "This holds".
- **Labelling a pair that was never in dispute.** The resolve endpoint accepts
  any loser ids; `build_labels` skips pairs without a `conflicts_with` edge, or
  arbitrary pairings would enter the test data as contradictions.
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
- **Publishing anything but `web` in `production.yml`.** The API, Qdrant,
  Postgres and Ollama listen on the compose network only, so no firewall slip
  can expose them; Caddy is the one way in, and it is what sets `X-Real-IP`.
- **`AUTH_ALLOW_WEB_SETUP=true` on a public address.** Whoever loads the page
  first becomes the admin. Production creates the admin from `ADMIN_EMAIL` /
  `ADMIN_PASSWORD` at startup.
- **Serving the production UI from a different host than the API** (a static
  host plus an API elsewhere). The session cookie is SameSite=Strict: Caddy
  serves both from one origin for that reason.
- **Publishing Qdrant or the api port on all interfaces again.** Qdrant has no
  auth of its own by default; anyone reaching it reads every memory and never
  meets the API's checks.
- **Hard-deleting superseded memories to keep the collection small.** Breaks
  commitment 1. Archive instead. The only way content leaves is a person pressing
  Forget, which redacts — the record and its edges stay.
- **Letting a private fact retire, or flag, a team belief.** The team check
  writes only to the private memory. If the team is out of date, the person
  shares theirs and the team graph's resolver decides.
- **Turning on `CALIBRATED_GATE` because the curve looks good.** It changes what
  is silently retired, exactly like moving the gate. A person decides.
- **Applying a suggested rule automatically.** Suggestions come from counts; the
  last both-hold might be the exception. A person approves each rule.
- **Judging new facts against summaries, or decaying them.** A summary is made
  from the graph; letting it confirm a fact is the graph agreeing with itself.
- **Re-embedding a forgotten memory's old text, or keeping its vector.** A vector
  of the words is a trace of the words.
- **Counting cross-graph escalations as gate evidence.** Nothing across graphs
  is ever auto-applied, so no gate decided them; they are recorded `forced`.
- **Auto-resolving every conflict with a second LLM call to raise throughput.**
  Breaks commitment 2. That is precisely the failure mode the project exists to
  fix.
- **Dropping `contradicted` out of retrieval so answers look cleaner.** Makes the
  agent silently confident about a disputed fact.
- **Replacing the Qdrant layer with a framework facade to "reduce code".** See
  above.
- **Adding a workspace tool to "tie the monorepo together".** Nx, Turborepo and
  uv workspaces all solve shared-dependency problems these projects do not
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
- **Mixing the modes "to be helpful"** — retrieving a work fact in Personal,
  judging a life fact against a work belief, or letting Personal read `_shared`.
  The modes are separate graphs precisely so that neither can leak into, or
  overrule, the other. A person who wants something in both says it in both.
- **Defaulting an unknown or missing-on-a-key mode.** A typo in
  `x-continuum-mode` must be a 400, and a Work key asking for Personal a 403 —
  never a quiet write to the other graph.
- **Sharing a Personal memory, even by choice.** Personal has no team; the
  share route, the chat toggle and the ingest flag all refuse it.
- **Letting health supersede silently because the judge is sure.** It is
  forced to a conflict on the category, and in Personal on the words too (the
  extractor files "takes 10 mg of cetirizine" as `fact`). Health stays out of
  summaries and exports unless the person opts in, and is never logged.
- **Using Personal's health words in Workspace.** "Condition", "therapy" and
  "dose" mean other things in a contract or a migration; the word rule reads
  the owner's mode first.
- **Trusting a judge DUPLICATE when the names were swapped.** DUPLICATE
  discards the new fact; "a different name for the same relationship" is the
  judge being confidently wrong in exactly the way the duplicate guard exists
  for. `swapped_names` is deliberately narrower than `material_difference`.
- **Feeding the assistant's answer into memory "so agreements are captured".**
  Answers are made from memory; read as a source, they re-learn "you are 22"
  from their own echo. The answer is context for a choice the user adopts —
  only choices, and an adopted fact never reinforces.
- **Putting the reply rules in the system prompts.** Measured: two extra rules
  there cost the Work extractor F1 0.74 → 0.56–0.64 on inputs that are not
  replies at all. They live in the reply's own framing.
- **Trusting the extractor's `tentative` flag or category for an agreement.**
  "I'll think about it" came back decided, "no thanks" came back a decision,
  creatine came back a decision not health — three runs in three each. Read the
  words: hedges, refusals, doses.
- **Adding the unprompted-maybe rule to the extraction system prompts.** It cost
  Work F1 0.74 → 0.56–0.64 and framing in the turn cannot beat them; a hedged
  turn that yielded nothing gets its own prompt instead (FINDINGS §14).
- **Filtering one side of a dispute out of the graph.** The view would show
  half an open disagreement as settled; `filterGraph` brings the other side
  back. Same rule as the chat's disputed counterpart.
- **Drawing every memory at once in a big graph.** 600 labelled nodes are an
  unreadable wall at 8 fps; grouping by subject is automatic above 300.
- **Showing the model a memory's text when it is written as orders to the
  assistant.** Told not to obey, the 7B model still spoke as a pirate 3 times in
  3 and once settled a real dispute "as instructed". It sees only that such a
  note exists (FINDINGS §19); the person still sees the memory in the graph.
- **Putting memory text into the chat prompt unquoted, or across lines.** A
  line break in a memory can forge another numbered memory or a DISPUTED header.
- **Plotting the judge's confidence on every escalation against the gate.** A
  "conflict" is the judge sure there is a dispute — near 1.0, and nothing to do
  with the gate. Near misses are supersedes the gate held for a person.
- **Letting an agent settle a dispute without the person's words, "since the
  web UI's button needs none".** A button is the person deciding; an API key is
  an agent, and an agent deciding a contradiction is commitment 2 broken. The
  words are the audit trail of every agent-made change.
- **Importing the backend's models into the SDK "to avoid duplication".** The
  HTTP API is the contract; the SDK parses only what an agent needs, so a
  backend refactor cannot break it, and it installs with httpx alone.
- **Pointing Continuum at GPU bge-m3 again on this laptop.** It returns NaN for
  any text over ~90 characters (FINDINGS §17) — notes fail to ingest, chat turns
  are silently not remembered. `bge-m3-cpu` is the same weights on the CPU.
- **Making the chat's `done` wait for memory again "so the outcome is in one
  event".** It held the input and Lumen for the whole write — p95 41 s against
  25 s. The outcome comes in `remembered`; agents read the stream to its end.
- **Starting the background write after `done` is yielded.** A client that
  leaves at `done` closes the generator at that yield, and the write never
  begins. It is created first, then shielded.
- **Scoring extraction with embedding similarity.** It matched tea to coffee and
  Thursday to Friday on held-out pairs (FINDINGS §15). The judged matcher reads,
  behind a number guard; the token figure stays beside it.
- **Rewording a confirmed maybe without the guard.** It is a model changing a
  belief's words; a changed number, name or negation keeps the original.
- **Declining a pending item from free text.** Only a button, or a spoken no
  to the question just asked. A contradiction in passing leaves it pending —
  commitment 2.
- **Letting a pending item be judged, or retire anything.** "Maybe I'll switch
  to upper/lower" must not retire the split you actually follow. It is judged
  only when confirmed, as if just said.
- **Counting a reminder when a pending item is merely retrieved.** It is
  counted when the question is put to the person; otherwise it fades unseen.
- **"Tidying" the transcript format into one rule for both modes.** A lone
  chat turn goes to the extractor bare in Personal and as `user: …` in Work,
  because that is what each extractor measured better at (FINDINGS §11: the
  label lost "hello my name is mark" entirely in Personal, and gained 3 of 14
  facts in Work).
- **Claiming in the UI that Personal data "never leaves this machine".** It
  goes wherever `LLM_BASE_URL` points, and that can be Groq. Say what is true:
  never shared with the team.
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
- **Narrowing `STATEMENT_CATEGORIES` back to same-category-only "to save judge
  calls".** It hid a stated database change (fact vs decision) from the resolver
  entirely. Splitting silently turns detection off; merging costs a call.
- **Renaming a subject onto one that means the same.** Meaning is measured as
  relatedness — sister / brother, lunch / dinner meet too. Comparing them costs
  a judge call; renaming one onto the other would file a belief under the
  wrong thing for good.
- **Letting names meet by meaning.** The embedder scores anna / ana at 0.95
  and has no idea whether Mike is Michael (FINDINGS §1). Names match by spelling.
- **Showing the judge both slugs when they met by meaning.** Its own rule — a
  different subject is independent — decides the pair before it reads the words.
- **Guessing a subject when the words name two, or none.** A wrong subject hides
  the pair from the right comparison; a null one at least stays comparable.
- **Merging duplicates in the recheck backfill.** DUPLICATE discards a memory —
  that is ingest's call on new input, never a backfill's on stored beliefs.
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
