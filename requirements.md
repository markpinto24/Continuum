# Requirements — Personal mode (general life memory)

Status: **planned, not started.** This is the plan to implement later. Read
`CLAUDE.md` first; everything here must fit its four commitments and its
"things that look like improvements but are not".

---

## 1. Goal

Continuum becomes a **personal AI assistant first**, with **Workspace** as a
second mode. Each person chooses which mode they are in, and can switch at any
time.

| Mode | What it remembers | Status |
| --- | --- | --- |
| **Personal** (new, the default for new accounts) | Your life: people and relationships, health, routines, preferences, goals, places, important dates, plans | To build |
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
