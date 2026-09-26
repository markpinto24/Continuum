# Continuum — Backend

Long-term **work memory** for AI agents. Continuum remembers decisions, preferences,
constraints and people — and, unlike most memory layers, it remembers *why things
changed* instead of silently overwriting the past.

---

## The problem this exists to solve

Every memory framework can store "Dave likes pizza". The hard part starts when
Dave says *"I don't like pizza anymore, I like pasta"* — and the system has to
decide, correctly and repeatedly, over months of accumulating half-contradictory
facts, which belief still holds.

That problem is genuinely unsolved. Commercial products handle it poorly, and the
usual failure modes are all the same shape:

| Failure | What goes wrong |
| --- | --- |
| Silent deletion | The old belief is gone; you can't audit why the agent changed its mind |
| Over-eager overwrite | A nuance is destroyed because an LLM judged too confidently |
| No decay | Stale facts stay at full confidence forever and pollute retrieval |
| No provenance | The agent asserts something and you can't trace where it came from |

Continuum's answer: **memories are nodes in a belief graph, not rows in a list.**
Superseding a memory creates an edge, not a `DELETE`. Genuinely ambiguous
contradictions are escalated to a human rather than guessed at by a second LLM
call. Unused memories decay rather than persist.

---

## Architecture

```
          ingest (note / transcript)
                     │
                     ▼
        ┌────────────────────────┐
        │  FactExtractor (LLM)   │  domain-tuned prompt →
        │                        │  {content, category, subject, excerpt}
        └────────────┬───────────┘
                     ▼
        ┌────────────────────────┐
        │   Triage (Phase 1)     │  cosine bands against existing memories:
        │   → Resolver (Phase 2) │    ≥0.86 duplicate   → reinforce (guarded)
        └────────────┬───────────┘    ≥0.45 conflict    → resolve / escalate
                     │                 else  new        → create
                     ▼
        ┌────────────────────────┐
        │   Qdrant (vectors +    │  status, category, subject, confidence
        │   payload indexes)     │  all filterable
        └────────────────────────┘
```

### Why we talk to Qdrant directly

Mem0 is in the dependency set and its prompt design is the starting point for our
extractor — but we own the storage layer rather than using `Memory.add()`.

Frameworks that support every vector DB and every LLM must build wrappers on
wrappers to unify `add()` / `search()`. The moment you need something the
abstraction didn't anticipate, you're stuck. We need payload indexes, filtered
search on lifecycle status, scroll queries for the graph view, and explicit
supersede edges — none of which survive a unified facade. Owning ~200 lines of
Qdrant access is cheaper than fighting someone else's design later.

---

## Domain model

A memory carries far more than its text:

| Field | Why it exists |
| --- | --- |
| `category` | Resolution policy differs per type — a `decision` is superseded by a later decision; an `event` is immutable history and can never be overwritten |
| `subject` | Normalised entity slug, so conflict candidates are narrowed by a cheap payload filter *before* spending an LLM call |
| `confidence` | Decays when unreinforced; rises when re-observed. Drives retrieval ranking |
| `status` | `active` / `superseded` / `contradicted` / `archived` — nothing is ever hard-deleted |
| `supersedes` / `superseded_by` | The belief graph edges. This is the differentiator |
| `conflicts_with` | Unresolved conflicts awaiting human confirmation |
| `source_id` / `source_excerpt` | Full traceability from any assertion back to its origin text |

---

## Stack

| Layer | Choice |
| --- | --- |
| API | FastAPI + Uvicorn |
| Packaging | uv |
| Vector DB | Qdrant (Docker) |
| LLM | Any OpenAI-compatible endpoint — Ollama, vLLM, Groq, Together |
| Embeddings | `bge-m3` via Ollama (1024-dim). Switching model re-embeds automatically |
| Extraction | Mem0-inspired, domain-tuned prompt |
| Logging | `core/logger.py` — coloured line or JSON output, over structlog for context and redaction |

Swapping providers is an `.env` change — no code touches required.

---

## Setup

> This is the backend half of the [Continuum monorepo](../README.md). Compose
> lives at the repo root and brings up the whole stack; `uv` commands run from
> this directory. Paths below say which.

### One command

```bash
docker-compose -f local.yml up -d --build        # from continuum-be/
```

That brings up the whole backend: Qdrant, a one-shot job that makes sure the
host's Ollama has the configured models, and the API. (The UI is not a container
— `yarn dev` in `continuum-fe/`.) The API waits for Qdrant to report
healthy and for the models to be ready, then creates its Qdrant collection —
re-embedding every memory if the embedding model changed — and starts the decay
scheduler from inside its own FastAPI lifespan hook.

**Ollama runs on the host, not in a container.** A containerised Ollama keeps
its own copy of every model; on a machine that already runs Ollama that was a
5.5 GB image and 6.1 GB of duplicated models. The containers reach the host
daemon as `host.docker.internal`, so Ollama must listen beyond loopback — the
Linux installer's default (`*:11434`). Set `OLLAMA_URL` to use another daemon.

There is no separate migrate, init or model-pull step to run by hand.

```bash
docker-compose -f local.yml logs -f api
docker-compose -f local.yml down       # keeps stored memories
docker-compose -f local.yml down -v    # wipes them
```

API docs: <http://localhost:8000/docs> · Qdrant dashboard: <http://localhost:6333/dashboard>

> If the models are not in your Ollama yet, the first start pulls ~6 GB.
> `docker-compose -f local.yml logs -f models` shows the progress.

### Using a hosted LLM instead

Container settings live in `.envs/.local/.api` (tracked local defaults). Put
anything personal — above all a real API key — in `.envs/.local/.api.override`,
which is gitignored and wins over `.api`:

```bash
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=gsk_...
LLM_MODEL=llama-3.3-70b-versatile
```

Embeddings still come from your Ollama unless you override `EMBEDDING_BASE_URL`
too. Changing the embedding model is safe: set `EMBEDDING_DIM` to match, and on
the next start every memory is re-embedded into a collection for the new model.
Nothing is deleted, and switching back works.

`local.yml` reads nothing from `continuum-be/.env`. That file configures the API
when you run it with `uv run uvicorn` on the host, where every URL is
`localhost`; inside a container those would point at the container itself.

### Developing with reload

Run the dependencies in Docker and the API on the host:

```bash
# continuum-be/ — dependencies in Docker (Ollama is already on the host)
docker-compose -f local.yml up -d qdrant postgres

# ...and the API on the host, with hot reload
uv sync --extra dev
cp ../.env.example .env
uv run uvicorn continuum.main:app --reload
```

`uvicorn` runs with `continuum-be/` as its working directory, and
pydantic-settings resolves `env_file=".env"` against the CWD — so the local run
reads `continuum-be/.env` while compose reads the one at the repo root. Both are
gitignored.

### Verify

```bash
# from continuum-be/
uv run pytest                      # 270 tests, no services needed
uv run ruff check .
CONTINUUM_API_KEY=ck_... uv run python scripts/seed_demo.py  # full pipeline, live
```

---

## Authentication

Every endpoint except `health`, `auth/status`, `auth/setup`, `auth/login` and
`auth/logout` needs a credential, and **the memory owner is whoever the
credential belongs to**. No request body or query names a user; a body that
still sends `user_id` is refused with a 422 saying so.

| Who | Credential | Can |
| --- | --- | --- |
| A person in the web UI | session cookie (httpOnly, SameSite=Strict, 14 days) | everything |
| An agent | `Authorization: Bearer ck_...` | the memory API only — not keys, password or users |

- Accounts, sessions and keys live in Postgres (`DATABASE_URL`), through
  SQLAlchemy. Passwords are argon2id; session tokens and keys are 256-bit random
  and stored only as SHA-256. A key's plaintext is shown once, at creation.
- The first admin comes from the web UI's setup screen, or from `ADMIN_EMAIL` /
  `ADMIN_PASSWORD` at startup. Set `AUTH_ALLOW_WEB_SETUP=false` on an instance
  strangers can reach.
- Cookie-authenticated writes must carry `x-continuum-client` (CSRF). Five failed
  sign-ins per email, or per client address, lock it for 15 minutes. LLM-spending
  endpoints are limited to 30 requests per user per minute; inputs to 50k chars.
- Another user's memory id answers 404, never 403.
- The limiters count in process memory: correct for the single uvicorn process
  shipped, wrong for several workers (move them to Redis first).

## Database and migrations

Postgres holds the account store only; memories stay in Qdrant. The schema is
owned by Alembic, and **the API upgrades it on every start** (under an advisory
lock, so replicas migrate once) — there is no separate migrate step.

```bash
# from continuum-be/, with `docker-compose -f local.yml up -d postgres` running
uv run alembic revision --autogenerate -m "add teams"   # after editing db/models.py
uv run alembic upgrade head       # the API does this itself; handy for inspection
uv run alembic current            # which revision the database is at
uv run alembic downgrade -1
uv run alembic upgrade head --sql # print the DDL instead of running it
```

The CLI reads `DATABASE_URL`, the same setting as the API. Tests apply the real
migrations to in-memory SQLite, and fail if `db/models.py` changes without a
migration. Behaviour only Postgres can show — two first-admin setups racing —
runs with `TEST_DATABASE_URL` set to a database the tests may wipe.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Liveness of Qdrant, Postgres and the LLM (public) |
| `GET` | `/api/v1/auth/status` | Needs first-run setup? (public) |
| `POST` | `/api/v1/auth/setup` | Create the first admin (public, once) |
| `POST` | `/api/v1/auth/login` · `/logout` | Web sign-in / sign-out |
| `GET` | `/api/v1/auth/me` | Who this credential belongs to |
| `POST` | `/api/v1/auth/password` | Change password; signs out other browsers |
| `GET` `POST` `DELETE` | `/api/v1/auth/keys[/{id}]` | List, create, revoke API keys (session only) |
| `GET` `POST` `PATCH` | `/api/v1/admin/users[/{id}]` | Manage accounts (admins, session only) |
| `GET` | `/api/v1/speech/status` | Is dictation on, is the model loaded, max length |
| `POST` | `/api/v1/speech/transcribe` | Multipart `audio` → text, by local Whisper; nothing stored |
| `POST` | `/api/v1/ingest` | Feed a note or transcript; extract, resolve, store |
| `POST` | `/api/v1/memories/search` | Semantic search over your memories |
| `GET` | `/api/v1/memories` | List memories |
| `GET` | `/api/v1/memories/graph` | Belief graph — nodes + edges for the 3D view |
| `GET` | `/api/v1/memories/{id}` | Fetch one memory |
| `POST` | `/api/v1/memories/{id}/reinforce` | Confirm still true; resets decay clock |
| `POST` | `/api/v1/memories/{id}/reactivate` | Restore an archived or superseded memory |
| `GET` | `/api/v1/conflicts` | The contradiction inbox |
| `POST` | `/api/v1/conflicts/resolve` | Human verdict (`keep_both` is first-class) |
| `POST` | `/api/v1/decay/sweep` | Run decay now; `dry_run` to preview |
| `POST` | `/api/v1/chat` | Answer from memory, streamed over SSE |
| `POST` | `/api/v1/chat/context` | What chat *would* retrieve — no generation |

Quick check:

```bash
curl -X POST localhost:8000/api/v1/ingest \
  -H "Authorization: Bearer $CONTINUUM_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"text":"We picked Postgres over Mongo because reporting needs real joins. Acme prefers async written updates, not calls."}'
```

---

## The resolution pipeline

```
score ≥ 0.86 ..................... duplicate     → reinforce, write nothing
  └─ unless a number, date, name or negation differs → judged instead
different category ............... new           → free, no LLM call
different subject ................ new           → free, no LLM call
either side is an event .......... new           → free, no LLM call
score ≥ 0.45, same kind .......... judge (LLM, reason before verdict)
      ├─ supersedes, p ≥ 0.80 ..... SUPERSEDES   → write edges, retire old
      ├─ supersedes, p < 0.80 ..... CONFLICT     → escalate to a human ★
      ├─ conflict ................. CONFLICT     → escalate to a human
      └─ independent .............. store, no edges
```

The bands (0.86 / 0.45) are bge-m3's, derived by `run_eval.py --calibrate`;
another embedding model gets its own pair from `CALIBRATED_THRESHOLDS`. `p` is
the judge's token probability for its verdict — the confidence it *writes* is
canned on a 7B model and was leaving the gate inert.

★ **`AUTO_SUPERSEDE_CONFIDENCE` is the most consequential setting in the
project.** Raise it and you escalate more; lower it and you silently lose
beliefs. Cheap category/subject filters run first so LLM calls are spent only
where the outcome is genuinely uncertain.

Every failure path escalates. A judge timeout, an unparseable response, an
unknown verdict — none of them can retire a memory.

## Decay

Confidence decays exponentially from `last_reinforced_at`, with a per-category
half-life: constraints 60 days, preferences 90, facts 120, decisions 180, people
365. Events never decay — history is not a belief that can weaken.

Below `DECAY_ARCHIVE_THRESHOLD` a memory is **archived**, not deleted: it drops
out of retrieval, keeps its edges, and can be reactivated. The sweep is
idempotent and runs every six hours; `dry_run` previews it.

---

## Chat

```bash
curl -N -X POST localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $CONTINUUM_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"what database are we on?"}]}'
```

```
event: context
data: {"query":"...","memories":[{"memory":{...},"similarity":0.81,"recency":0.94,"score":0.61}],
       "disagreements":[{"subject":"atlas","memories":[{...},{...}]}]}

event: delta
data: {"text":"The record disagrees here"}

event: done
data: {"memory_ids":[...],"cited_ids":[...],"disagreements":1,"remembered":{...}}
```

**Ranking.** `similarity × confidence^w × recency^w`. Multiplicative, so a memory
that fails badly on any one factor falls — a stale belief does not outrank a
freshly confirmed one just because it shares more vocabulary with the question.
Candidates are over-fetched by similarity and then re-ranked, because top-k by
final score is not a subset of top-k by cosine. Recency uses its own half-life,
not the per-category decay one: decay asks *do we still believe this*, recency
asks *is this topical now*.

**Disagreement is the payoff.** When retrieval returns a `contradicted` memory,
both sides go into the prompt under a `DISPUTED` heading — the counterpart
fetched by id, since the other side of a contradiction is usually worded
differently, which is why they disagree in the first place. The model is told to
say the record disagrees, give both sides with dates, and ask. Not to pick the
newer one, the more confident one, or the one that reads better.

**Confirmation goes through ingest.** "Reinforce what the conversation confirms"
is the question the Phase 2 resolver already answers: the turn is extracted, and
a `duplicate` verdict *is* the confirmation. Chat cannot invent a reinforcement
rule that disagrees with ingest's, and a turn that contradicts the record lands
in the inbox like any other input. Only the user's turn is remembered — ingesting
the reply would let a paraphrase re-enter the graph as evidence for itself.
Retrieval itself never reinforces: being retrieved is not evidence of still being
true. Set `CHAT_REMEMBER_TURNS=false`, or `"remember": false` per request, for a
read-only chat.

---

## Evaluation

`AUTO_SUPERSEDE_CONFIDENCE` was 0.80 because 0.80 felt about right. Phase 5
replaces that with a number.

```bash
docker-compose -f local.yml up -d qdrant    # from continuum-be/; Ollama on the host

uv run python scripts/run_eval.py --preflight             # can the embedder see a swap?
uv run python scripts/run_eval.py --calibrate             # which bands does it need?
uv run python scripts/run_eval.py --crowded               # right memory judged when crowded?
uv run python scripts/run_eval.py --record eval-run.json   # one slow pass
uv run python scripts/run_eval.py --replay eval-run.json   # free, repeatable
```

**Record once, sweep forever.** Each recorded outcome stores the judge's relation
and confidence *before* the gate was applied, so every other gate value is
recomputed by arithmetic. A sweep across eight thresholds costs zero LLM calls.

The system fails in two directions and they are not equally bad, so they are
never averaged into one score:

| Metric | What it catches |
| --- | --- |
| `belief_loss_rate` | Retired a belief that should have lived. Information destroyed, silently |
| `stale_belief_rate` | Should have retired, did not, and did not ask either |
| `merge_loss_rate` | Folded a distinct fact into an existing one as a duplicate |
| `escalation_rate` | Sent to a human. A cost, not a failure |
| `band_miss_rate` | The fact never got near the belief it contradicts — a retrieval problem, not a judgement one |

`recommend_gate` picks the cheapest threshold inside your belief-loss budget
rather than the best F1: F1 treats a destroyed belief and a wasted minute as the
same size of mistake.

See [`src/continuum/evaluation/README.md`](src/continuum/evaluation/README.md)
for the corpus labelling principle, the three cases that are meant to fail, and
how to read the sweep.

---

## Roadmap

| Phase | Status |
| --- | --- |
| **1 — Ingest & storage** | ✅ done |
| **2 — Resolution & decay** | ✅ done |
| **3 — Memory-augmented chat** | ✅ done — SSE streaming, retrieval ranked by similarity × confidence × recency, disagreement surfaced rather than resolved |
| **4 — Belief graph UI** | ✅ done — `continuum-fe`: React + shadcn + Three.js force-directed graph off `/memories/graph`, the contradiction inbox, and a chat panel alongside |
| **5 — Evaluation** | ✅ done — 46-case labelled corpus incl. held-out sets, belief-loss / stale-belief / merge-loss measured separately, and a gate sweep that replays one recorded run at every threshold for free |
| **6 — Authentication** | ✅ done *(this release)* — accounts, sessions and per-agent API keys; identity from the credential only; per-user isolation, CSRF, lockout and rate limits |

See [`../CLAUDE.md`](../CLAUDE.md) for the full engineering brief, layering rules
and conventions, and [`../README.md`](../README.md) for the project overview.

---

## Running the demo

`scripts/seed_demo.py` deliberately includes a reversed decision (Postgres →
Mongo, then narrowed to "Postgres stays for reporting only"). Watch what the
resolver does with it: a confident reversal is auto-superseded with edges
written, while the narrowing qualifier should land in the contradiction inbox
rather than being guessed at.

Either way the original is still queryable. That is the whole point.
