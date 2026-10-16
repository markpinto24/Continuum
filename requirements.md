# Requirements — Personal mode (Part 1) · Agreements and pending memories (Part 2)

Status: **built (Phase 15, 2026-10-10)** on the defaults in §10: strictly
separate modes, one Lumen, finance later, "Personal / Work", existing accounts
stay on Work with a one-time hint. Phases A–G are done; results are in
`continuum-be/src/continuum/evaluation/FINDINGS.md` §11. Open: the judge
escalates most stated life changes (safe, but costly), and Personal subjects are
often `self`. Read `CLAUDE.md` first; everything here must fit its four
commitments and its "things that look like improvements but are not".

---

## 1. Goal

Continuum becomes a **personal AI assistant first**, with **Workspace** as a
second mode. Each person chooses which mode they are in, and can switch at any
time.

| Mode | What it remembers | Status |
| --- | --- | --- |
| **Personal** (new, the default for new accounts) | Your life: people and relationships, health, routines, preferences, goals, places, important dates, plans | Built |
| **Workspace** (today's Continuum) | Your work: projects, decisions, clients, constraints, ownership, deadlines | Built |

The belief-graph machinery is the same in both modes: supersede edges, disputes
escalated to a person, decay, provenance, the inbox, chat and Lumen. What
differs is **what gets extracted, how fast it fades, how the assistant talks,
and who can see it.**

---

## 2. Non-negotiables

1. **The four commitments hold in both modes.** Nothing is hard-deleted (only
   Forget redacts), ambiguity goes to a human, events are append-only, and
   every failure fails toward the human.
2. **The modes never mix silently.** A personal fact must never appear in a
   work answer, a work agent's API call, or the team space. A work fact must
   never retire a personal one, or the reverse.
3. **Identity still comes only from the credential.** The mode selects *which
   of your own graphs* you are using. It can never reach another person's
   memories.
4. **Personal data is more sensitive by default.** Health, relationships and
   finances are never shared, never sent off the machine, and never written to
   logs.

---

## 3. Architecture decision: a mode is a graph

**Each mode is a separate graph, with its own owner id.** It reuses the
mechanism the team space already uses (`SHARED_SPACE = "_shared"`): resolution,
disputes, decay, labels and summaries all work per graph, so separation comes
for free and cannot leak.

| Graph | Owner id | Notes |
| --- | --- | --- |
| Workspace | `{user_id}` (unchanged) | Existing memories stay exactly where they are. No migration of data |
| Personal | `{user_id}:personal` | `:` is not allowed in account ids (`USER_ID_PATTERN`), so no account can ever claim it |
| Team space | `_shared` | **Workspace only.** Never visible from Personal |

- `visible_owners(user_id, mode)` replaces `visible_owners(user_id)`:
  - Workspace → `[user_id, "_shared"]`
  - Personal → `[f"{user_id}:personal"]`
- **Rejected alternative: a `mode` field on each memory.** Every query, filter,
  resolver neighbour search, summary and decay sweep would need one more
  condition, and forgetting it once leaks one mode into the other. A separate
  owner id makes a leak structurally impossible.
- Writes and resolution stay inside one graph, as today. Nothing crosses modes.

### How the mode travels

- **Web UI:** the selected mode is sent with every request (header
  `X-Continuum-Mode: personal|workspace`). The server resolves it to an owner id
  in `get_principal`, so services stay user-agnostic as they are now. An unknown
  value is refused, never defaulted.
- **API keys are scoped to one mode** (new `api_keys.mode` column). A work agent
  holding a workspace key cannot read or write personal memory, and a key never
  switches mode. Existing keys become workspace keys.
- **Accounts gain `users.default_mode`** (Personal for new accounts, Workspace
  for existing ones, so nobody's current graph disappears on upgrade).
- **Postgres rows that belong to a graph** carry `graph_owner` or a `mode`:
  resolution labels (already have `graph_owner`), resolution rules (have
  `owner`), extraction feedback, answer feedback. Each needs a migration with a
  working `downgrade()`.

---

## 4. What Personal mode remembers

### 4.1 Categories

The existing six stay. Three are added for life memory:

| Category | Examples | Half-life | Why |
| --- | --- | --- | --- |
| `person` | "Chris Macwan is Mark's brother" | 730 d | Relationships change slowly |
| `preference` | "Mark prefers window seats", "Mark is vegetarian" | 180 d | Tastes drift, but over months |
| `fact` | "Mark lives in Pune", "Mark drives a Honda City" | 240 d | Stable states of life |
| `decision` | "Mark decided to learn Spanish" | 180 d | |
| `constraint` | "Mark's flight budget is ₹30k", "Gym closes at 9 pm" | 60 d | Limits expire |
| `event` | "Mark ran a half marathon on 12 Oct" | never | History is append-only |
| **`health`** (new) | "Mark is allergic to penicillin", "Mark takes vitamin D daily" | **never decays** | A forgotten allergy is dangerous. Changes only through the resolver or a person |
| **`routine`** (new) | "Mark goes to the gym on weekday mornings" | 45 d | Habits change often; an unconfirmed routine should fade fast |
| **`goal`** (new) | "Mark wants to read 20 books this year" | 120 d | Goals expire or get replaced |

- Half-lives are **per mode** (`MODE_HALF_LIFE_DAYS[mode][category]`), so the
  workspace table is untouched.
- `comparable_categories` (Phase 13) gains the new ones: `health`, `routine`
  and `goal` join the statement family, so "stopped taking vitamin D" is still
  compared with "takes vitamin D daily".
- `health` is **sensitive** (section 6).

### 4.2 Extraction

- **A separate, personal extraction prompt** (`services/extraction.py`),
  selected by mode. It keeps the existing rules (one fact per memory,
  third-person, a verbatim excerpt, record what is true now) and adds personal
  guidance:
  - important dates ("Mom's birthday is 4 June") are `fact`s with the date in
    the text, not events;
  - "I'm thinking about…" or "maybe" is not a decision — record nothing, or a
    goal if it is stated as an aim;
  - a medication or allergy is `health`, never `preference`.
- Guidance is added **as rules, never by naming a category in an example**
  (FINDINGS §7: examples skew a 7B extractor).
- Subjects stay entity slugs: `mom`, `chris-macwan`, `home`, `pune`,
  `half-marathon`.

### 4.3 Resolution

The same pipeline in both modes. Personal-specific cases to cover:

- **Relationships behave like roles.** "Mark is dating Priya" followed by "Mark
  is dating Anya" must not auto-supersede unless the change is stated ("broke
  up", "is now"). Extend the role-word list (`is_role_statement`) with
  relationship words: dating, married to, lives with, works at.
- **Health never auto-supersedes.** A health change always escalates to the
  inbox, even at high judge confidence: a wrong silent change to an allergy is
  the worst possible belief loss. (The same kind of policy rule as the role
  rule, recorded as a `forced` escalation, so it is not gate evidence.)
- **Routines supersede freely** when the change is stated ("switched the gym
  to evenings").
- `auto_supersede_confidence` stays 0.80 in both modes until the personal
  evidence report says otherwise. That is a product decision, as always.

---

## 5. The assistant in each mode

| | Personal | Workspace |
| --- | --- | --- |
| Chat system prompt | A personal assistant: warm, practical, remembers your life | Today's prompt |
| Greeting | "Good evening, Mark. Anything on your mind?" plus open disputes | Today's greeting |
| Lumen | Same HUD and commands; greets in the mode's voice | Unchanged |
| Disputes in answers | Surfaced exactly as today: both sides, ask which holds | Unchanged |
| Summaries | Per subject, as today (people, home, health excluded — see §6) | Unchanged |
| Team space | **Not available** | As today |

**Answers never draw on the other mode.** "Am I free Thursday?" in Personal mode
does not look at work memory. Cross-mode questions are listed under open
questions (§10), not built implicitly.

---

## 6. Privacy and safety in Personal mode

- **Sensitive categories** — `health` now, `finance` possibly later:
  - never shared (the share button and `share: true` are refused, and there is
    no team space in Personal mode anyway);
  - excluded from periodic summaries (a summary copies the words around);
  - excluded from `GET /feedback/export` cases unless the person opts in when
    exporting;
  - logs already never contain memory content; keep it that way, and add a
    test that a health ingest logs nothing but counts.
- **Forget and export work per mode.** "Download my memories" asks which mode,
  or offers both as separate files.
- **Backups** contain both modes; the backup README says so plainly.
- **Everything stays local:** the LLM, Whisper and Piper already run on this
  machine. No personal feature may add a cloud call.

---

## 7. Interface

- **Mode switcher in the header:** a segmented control, **Personal | Work**,
  next to the wordmark. Switching changes the graph, chat, inbox, memory panel
  and Lumen together. The choice is remembered per browser, and the account's
  `default_mode` is used on first load.
- **Each mode has its own conversation.** Switching never carries the chat
  history across.
- **A subtle mode accent:** Personal uses a warm accent (rose/violet) in the
  header chip and the graph sky; Workspace keeps cyan. The status colours
  (green, amber, slate) are identical in both — they mean status everywhere.
- **First-run choice:** the setup screen asks "What will you use Continuum for?"
  (Personal / Work / Both) and sets `default_mode`.
- **Settings:**
  - default mode;
  - API keys: choose the mode when creating a key, and show each key's mode
    in the list.
- **Empty states per mode:** "Tell me about your life — people, routines,
  what you're working towards" vs today's work prompt.
- **The graph legend** gains the new categories only where categories are
  shown (memory detail, tooltips); the status legend is unchanged.

---

## 8. Evaluation (before any of it is trusted)

Follow `CLAUDE.md`: the corpus is the instrument; write held-out cases first,
label them before running, and include cases labelled *against* each change.

1. **Personal extraction corpus** (`corpus/extraction-personal.yaml`), at least
   20 cases, each with a `why`. It covers:
   - relationships, health, routines, goals and important dates;
   - "maybe/thinking about" (nothing to extract);
   - a question;
   - mixed work-and-life text in Personal mode (only life facts should come
     out).

   Run three times before and after every prompt change, since one draw is
   noise.
2. **Personal resolution corpus** (`corpus/resolution-personal.yaml`), at least
   20 cases. It covers:
   - a relationship change, stated and unstated;
   - a routine change;
   - a health change, which must escalate;
   - a preference flip;
   - two compatible facts about one person;
   - an event, which must never be retired.

   It also includes a held-out set of at least 6.
3. **Isolation tests (unit):**
   - a personal fact is never retrieved in Workspace;
   - a workspace key cannot read personal memory;
   - Personal mode never sees `_shared`;
   - resolution never compares across modes.
4. **Live check** on a throwaway instance (as in Phases 12–14): ingest life
   notes in Personal mode, confirm Workspace is untouched, and check the inbox
   per mode.

Success bar: belief loss **0%** on both personal corpora, and stale beliefs no
worse than Workspace's.

---

## 9. Delivery plan

Each phase ends with tests green, `ruff` clean, docs updated, and a live check.

| Phase | Scope | Done when |
| --- | --- | --- |
| **A — Modes as graphs** | `{user_id}:personal` owners; mode header resolved in `get_principal`; `visible_owners(user_id, mode)`; `users.default_mode`; `api_keys.mode`; migrations with downgrades; team space Workspace-only | Isolation tests pass; existing data and keys behave exactly as before |
| **B — Personal memory model** | New categories `health`, `routine`, `goal`; per-mode half-lives; category families updated; health-escalation and relationship rules | Unit tests for each rule's failure modes |
| **C — Personal extraction** | Personal prompt + extraction corpus; three runs before/after | Corpus targets met; no regression on the work extraction corpus |
| **D — Personal resolution** | Resolution corpus incl. held-out; two passes, same day, same judge | Belief loss 0% on the personal corpus; workspace corpus unchanged |
| **E — The assistant** | Mode-specific chat prompt, greeting, empty states; Lumen per mode | Chat tests per mode; prompt never mixes modes |
| **F — Interface** | Header switcher; per-mode chat history; mode accent; first-run choice; settings (default mode, key mode) | Frontend tests; real-browser check of both modes |
| **G — Privacy hardening** | Sensitive categories: no share, no summaries, export opt-in; per-mode export and forget; logging test | Each refusal tested |

Phases A and B can start together. C and D need B. E and F need A.

---

## 10. Open questions (decide before the phase that needs them)

1. **Cross-mode questions.** Should Personal mode ever *ask permission* to look
   at work memory ("You have a work deadline Thursday — want me to include
   work?"), or stay strictly separate? (Default in this plan: strictly separate.)
2. **One Lumen, or a different name or voice per mode?** (Default: one Lumen,
   one voice, greeting adjusted to the mode.)
3. **Finance as a sensitive category** now, or later?
4. **Mode names in the UI:** "Personal / Work", or "Life / Work"?
5. **Should existing accounts be asked once** to set their default mode, or
   silently stay on Workspace? (Default: stay on Workspace, with a one-time
   hint about Personal mode.)

---

## 11. Out of scope for this plan

- Connectors (calendar, email, contacts, health apps).
- Reminders and notifications ("remind me on Mom's birthday").
- Mobile apps.
- Sharing personal memories with family members (a "family space" would be a
  third graph, like the team space, and needs its own plan).

---
---

# Part 2 — Agreements, pending memories and questions

Status: **built 2026-10-16 — Phases H, I, J, K done** (CLAUDE.md Phase 16, FINDINGS §12). Applies to both modes.

**Changed from §17 by measurement:** an unprompted plan ("maybe I'll start running on Sundays") is **not** yet pending — the rule that would do it caught 0 of its cases and cost the Work extractor (F1 0.74 → 0.56–0.64). A maybe in reply to a suggestion is pending, as decided.

## 12. The problem

Found in use. Asked for a workout split, the assistant proposed push/pull/legs;
the user replied "it sounds good for me for now". Nothing was stored, because
only the user's own words are ever extracted and "sounds good" holds no fact.
A real decision was lost.

The rule that caused it exists for a reason (CLAUDE.md, "Ingesting the
assistant's reply"): answers are assembled *from* memory, so feeding them back
in lets the graph confirm itself — "you are 22" re-learned from its own echo.
The fix must tell **a choice the user made about something the assistant
proposed** apart from **a fact the assistant repeated from memory**.

## 13. Decisions (asked 2026-10-16)

| Question | Decision |
| --- | --- |
| A clear yes ("sounds good", "let's do that") | **Stored at once as confirmed**, shown in the turn ("Remembered: you follow a push/pull/legs split…") with one-tap **Undo** |
| A maybe ("I'll think about it", "maybe") | **Stored as pending** — kept, never treated as true |
| A no to a pending item | **Declined**: leaves the graph and retrieval, never raised again; the record stays for audit. Forget still erases the words |
| How pending items come back | **In chat when the topic comes up**, with option buttons, **and** a **Pending** tab in the inbox |
| An unanswered pending item | **Asked up to 3 times, then fades** to archived (or after ~30 days). Recoverable, never deleted |
| Modes | **Both** — "sounds good, let's go with Postgres" is a work decision |

## 14. Design (proposed — overrule any line)

**Two new statuses.** `pending` — said, not decided. `declined` — the person
said no. Both live in Qdrant payloads like every status: no migration.

| | Retrieved | In the graph | Judged by the resolver | Decays |
| --- | --- | --- | --- | --- |
| `pending` | yes, in its own **PENDING** prompt section | dashed outline | never retires or is retired by anything | no — it fades by reminders (3) or age (30 d) |
| `declined` | no | only with "Show archived" | no | no |

- **A pending item is not a belief.** It never supersedes an active memory and
  is never superseded: "maybe I'll switch to upper/lower" must not retire "you
  follow push/pull/legs". Only when confirmed does it go through the resolver,
  as if just said — so a confirmed switch supersedes the old split normally.
- **Confirming** (a button, or restating it in your own words — a cheap
  duplicate match, no LLM) makes it `active` and runs it through resolution.
  **Declining** needs a button or a spoken "no" to the question just asked;
  a free-text contradiction never declines on its own (commitment 2: no LLM
  reading intent decides). It shows the buttons again instead.
- **The answer is context, never a source of facts about you.** For a turn that
  replies to an answer, the extractor sees that answer marked *proposed by the
  assistant — context only*, with a rule: record only what the user adopted,
  in the user's name. Two guards that read the result, not the model:
  - only a **choice** can come from an agreement — decision, routine, goal,
    preference. Never `person`, `fact` about the user, and **never health**:
    "ok" to a suggested supplement is not a medication.
  - an agreement fact that duplicates a memory **the answer itself used** is
    dropped — not reinforced. Echo is not evidence.
- **It stores the choice, not the document.** "The user follows a push/pull/
  legs split, 6 days a week, ~2 h a session" — one memory, `routine`, subject
  `gym`, source excerpt = the user's reply. The full exercise list is a
  document, not a belief; keeping it whole would make it unsupersedable.
- **Questions with options, like Claude Code's.** The chat's `done` event gains
  `questions`: each has text, 2–4 fixed options and the assistant's own short
  take ("6 days fits PPL well"). **Options map to server-side actions only**
  (confirm / still unsure / decline; and the existing *this holds* / *both are
  true* for disputes) — the model words the question, never the outcome. An
  "Other" reply is just the next chat turn, through normal ingest.
- **Reminders are counted when shown**, not when retrieved silently. At 3, or
  30 days, the item fades to archived and stops being raised.
- **Lumen** reads the options aloud and accepts a spoken "yes", "no" or "not
  sure" — word match right after the question, like its commands, no LLM.
- **Undo on a confirmed agreement** is the existing *This isn't a real fact*:
  archived, anything it superseded restored, and it becomes an extraction case.

## 15. Evaluation

- **Agreement corpus** (`corpus/extraction-agreement.yaml`), each case = the
  assistant's answer + the user's reply, labelled before the first run, both
  modes: clear yes; yes with conditions ("yes, but 5 days"); maybe; no; yes to
  an answer that also repeats the user's age (age must **not** come back); yes
  to a supplement suggestion (nothing health); a reply that changes topic
  (nothing from the answer); a held-out set of 6, one labelled against the echo
  guard so its cost is visible. Three runs before and after.
- **Unit tests** for every lifecycle edge: pending never retires or is retired;
  confirm runs the resolver; decline hides and never resurfaces; three reminders
  fade it; a free-text contradiction does not decline; an echo is not
  reinforced; health is never stored from an agreement; Work and Personal both.
- Existing corpora must not move: the Work and Personal extraction corpora (a
  turn with no preceding answer is extracted exactly as today), and both
  resolution corpora.

## 16. Delivery plan

| Phase | Scope | Done when |
| --- | --- | --- |
| **H — Pending lifecycle** ✅ | `pending` / `declined`; confirm, decline, keep-pending routes (owner-checked, 404 across modes); PENDING prompt section; reminder count and fade in the decay sweep | Lifecycle tests, incl. every failure mode above |
| **I — Agreement extraction** ✅ | Previous answer as context; tentative flag; choice-only and echo guards; health excluded | Agreement corpus 3× before/after; other corpora unchanged |
| **J — Questions with options** ✅ | `done.questions`; chat prompt raises pending items when relevant; option chips; Lumen spoken answers | Chat + frontend tests; real-browser check |
| **K — Interface** ✅ | "Remembered: …" with Undo; Pending tab in the inbox; dashed pending nodes; declined under "Show archived"; `types.ts` mirror | Frontend tests; live check in both modes |

## 17. Decided before Phase I

1. **A maybe that is not a reply** (2026-10-16): **pending only if it is a
   plan** — "maybe I'll start running on Sundays" is pending and asked about;
   idle musing ("someday, who knows") stays nothing, so passing thoughts are
   never nagged about. `maybe-is-nothing` keeps its label.
