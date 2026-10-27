# What the first real run found

Run on 2026-09-22 against the default stack: `qwen2.5:7b-instruct` as judge,
Ollama on the host. 28-case corpus. Recorded runs are in `baselines/`.

The short version: **three of the four mechanisms this project rests on were not
working**, and none of the failures were visible from the outside. The system
returned plausible answers the whole time.

---

## 1. The embedding model is blind to entity swaps

`nomic-embed-text` returns **byte-identical 768-dim vectors** for:

| | cosine |
| --- | --- |
| `Atlas stores events in Postgres` / `... in MongoDB` | **1.0000** |
| `The billing service runs on ECS` / `... on EKS` | **1.0000** |
| `Sara owns the billing service` / `Raj owns ...` | **1.0000** |
| `Chose Auth0 for authentication` / `Chose Okta ...` | **1.0000** |
| `the cat sat on the mat` / `the dog sat on the mat` | 0.8639 |
| `Atlas stores events in Postgres` / `... in bananas` | 0.8169 |

Not "similar" — identical, element for element, batched or one at a time. Common
words discriminate fine; **out-of-vocabulary proper nouns collapse onto the same
representation.** Product names, service names, vendor names and people's names
are exactly what work memories are made of.

Nomic's documented `search_document:` prefix does not help.

### Why it is worse than it looks

Cosine 1.0 is above `duplicate_similarity_threshold` (0.94). So a direct
contradiction is classified **duplicate**, which means ingest *reinforces the old
belief* and **discards the contradicting fact without storing it**. No conflict
is raised, nothing reaches the inbox, and nothing reaches the judge.

The one case the whole project exists to handle — "we moved from Postgres to
Mongo" — was silently dropped, and the old belief's confidence went **up**.

### The check that now exists

```bash
uv run python scripts/run_eval.py --preflight
```

`preflight.py` embeds five minimal pairs and refuses to report resolution numbers
if any entity swap lands in the duplicate band. `all-minilm` (45 MB, 384-dim)
passes: entity swaps score 0.44–0.88, a true paraphrase 0.94.

---

## 2. The cosine thresholds were never calibrated against a model

`0.94` and `0.78` are model-specific constants that predate having anything to
calibrate them against. Swapping to `all-minilm` without touching them moved the
failure rather than fixing it: `band_miss_rate` went from 25% to **35.7%** —
a third of cases never found a candidate to compare at all, because all-minilm's
similarity scale sits well below 0.78.

Lowering the conflict band to 0.40 brought band misses down to 10.7%.

### And the duplicate band is not sound at any threshold

With `all-minilm`:

| pair | cosine | should be |
| --- | --- | --- |
| `budget is capped at 5k` / `... at 8k` | 0.967 | **contradiction** |
| `launch is 14 March` / `... 2 April` | 0.922 | **contradiction** |
| `The billing service runs on ECS` / `Billing runs on ECS` | 0.939 | duplicate |

The contradictions score *higher* than the genuine duplicate. There is no
threshold that separates them, because they differ by one low-weight token.

**A similarity-only duplicate short-circuit cannot be made safe.** It will keep
classifying numeric and date contradictions as duplicates — and a duplicate
verdict is the one path that discards the incoming fact entirely. The fix is
structural, not a number: either the duplicate band must also consult the judge,
or contradiction-bearing categories (`constraint`, dates, quantities) must be
excluded from the short-circuit.

---

## 3. The confidence gate is inert

`auto_supersede_confidence` is described in `CLAUDE.md` as "the single most
consequential setting in the project". On this corpus, with this judge, **it does
nothing at all**:

```
  gate    belief-loss   stale   escalation   retire-R   accuracy
  0.50      3.6%         7.1%    39.3%         12.5%       50.0%
  0.95      3.6%         7.1%    39.3%         12.5%       50.0%
```

Flat across every value from 0.50 to 0.95.

The reason is in the recorded judgements: `qwen2.5:7b-instruct` emits a small set
of canned confidence values — `{0.55, 0.75, 0.80, 0.85, 0.95, 1.00}` — and
**every single `supersedes` verdict came back at exactly 0.95**, in both runs,
without exception. 0.95 clears every gate in the range, so the dial never
engages.

The gate is not wrong. It is a well-designed mechanism resting on an assumption —
that the judge reports calibrated confidence — which a 7B local model does not
satisfy. Options, in rough order of cost: ask the judge for a discrete risk band
rather than a float; use logprobs for a real probability; or accept that the gate
only earns its keep with a larger judge.

---

## 4. The judge over-escalates badly

On the seven cases labelled as clear reversals, it said `supersedes` **once**:

| case | judge said | conf |
| --- | --- | --- |
| `db-swap-explicit` (Postgres → MongoDB) | conflict | 0.55 |
| `comms-preference-flip` | conflict | 0.85 |
| `runtime-migration` (ECS → EKS) | conflict | 0.80 |
| `role-change-explicit` | conflict | 0.55 |
| `budget-cap-raised` | *never reached the judge* — duplicate band | — |
| `deadline-moved` | *never reached the judge* — duplicate band | — |
| `vendor-dropped` | **supersedes** | 0.95 |

Retire recall: **12.5%**. Escalation: **39.3%**.

The judge prompt says *"Prefer conflict over supersedes whenever you are unsure.
Escalating is cheap; erasing a true belief is not."* A 7B model is unsure about
almost everything, so it follows that instruction into near-total escalation. The
instruction is right; its effect on a small model is an inbox nobody will empty.

Worth noting what did **not** break: `belief_loss_rate` stayed at 3.6% across
every configuration. The safety bias works. The system is failing safe, loudly,
in the direction the design intended — it is just failing far more often than
"escalate only genuine ambiguity" implies.

---

## 5. Extraction: subjects are mostly wrong

Against the 8-document extraction corpus:

| metric | value |
| --- | --- |
| fact precision | 70.0% |
| fact recall | 58.3% |
| category accuracy | 71.4% |
| **subject accuracy** | **14.3%** |
| provenance (excerpt present) | 100.0% |

Subject is not cosmetic: it is the cheap filter that decides what reaches the
judge, and two of the three `known-gap` corpus cases are about subject
mismatches. At 14% agreement, the filter is routing on a field that is close to
noise.

Some of the missed/invented pairs are the token matcher being crude rather than
the extractor being wrong — `Chose Postgres over Mongo for Atlas because
reporting needs real joins` vs the extracted `Chose Postgres over Mongo for
relational integrity` scored below the 0.34 threshold. Read recall as a floor.

It also invented a memory from the noise case (`The review scheduled for Thursday
is being pushed to Friday`), which is a genuine precision miss: a scheduling
detail is not durable, and the extraction prompt says so explicitly.

---

## 6. The Mem0 baseline: a near-tie on facts, a rout on structure

Same corpus, same judge model, Mem0's own extraction against an isolated store
reset between documents.

| | continuum | mem0 |
| --- | --- | --- |
| facts extracted (12 labelled) | 10 | 14 |
| precision | **70.0%** | 57.1% |
| recall | 58.3% | **66.7%** |
| F1 | 0.64 | 0.62 |
| category accuracy | **71.4%** | 0.0% |
| subject accuracy | **14.3%** | 0.0% |
| provenance (excerpt) | **100.0%** | 0.0% |

On raw fact-finding they are within noise of each other — Mem0 trades precision
for recall, and F1 lands at 0.64 against 0.62. **The domain-tuned prompt is not
meaningfully better at finding facts.**

The structural columns are the whole argument. Mem0 returns flat strings, so
every fact arrives with no category, no subject and no source excerpt. A fact
with no category cannot be routed by resolution policy — `event` immutability,
per-category half-lives and the category filter all become inoperative. A fact
with no subject cannot be filtered before the judge, so every neighbour in the
cosine band costs an LLM call. And with no excerpt there is no provenance, which
was failure #4 on the list this project exists to fix.

### What Mem0 actually emitted

Three failures worth naming, because they are the ones the domain prompt was
written against:

**It recorded questions as beliefs.** From `question-not-fact` ("Should we move
the event store to Mongo? ... has anyone looked at the Redis bill?") it produced
*"User suggests moving the event store to Mongo and proposes discussing it at the
next sync"* and *"User asks if anyone has looked at the Redis bill."* Neither is
a durable fact. A proposal recorded as memory is a belief nobody holds. (It did
correctly return nothing for pure chatter.)

**It joined unrelated facts with "and".** From the noise transcript: *"Auth0 will
be dropped and the review scheduled for Thursday has been pushed to Friday due to
a dentist appointment."* Two unrelated claims and one non-durable one, welded
into a single memory. That memory cannot be superseded: retiring the Auth0
decision later would retire the dentist appointment with it. This is precisely
why the extraction prompt says *one fact per memory, never join two ideas with
"and"* — the rule is load-bearing for the belief graph, not a style preference.

**It framed statements relative to the speaker.** *"User kicked off the Atlas
project today"*, *"User's company is moving authentication in-house"*. Standalone
third-person phrasing is what makes a memory readable months later with no
surrounding context.

So the honest summary: Mem0's extraction is competitive at the task it was built
for, and the structure this project layers on top is what the resolution layer
consumes. The benchmark supports keeping the native extractor — but on the
grounds of structure and one-fact-per-memory, not on fact recall, which is where
the intuition would have put it.

---

## 7. Extraction had no place for what someone is working on

*Found in real use, 2026-09-23, after the first run.* The chat turn "i am
currently working on fastapi project with poetry env" stored nothing. The logs
showed the write-back ran and `extraction.completed count=0`; replaying the call
showed the model returning `{}` deterministically. Not a parse drop.

Varying the input isolated it: dropping "currently", dropping the `user:`
prefix, even rewriting it as "Mark is working on a FastAPI project that uses
Poetry" all extracted nothing. Only "our fastapi project uses poetry for
dependency management" got through. The six categories cover decisions,
preferences, stable facts, events, people and constraints — and **not what
someone is working on**, which is the most basic work memory there is.

The sentence became corpus case `current-work-and-stack` (two labelled facts,
because the tooling can change without the project changing). Prompt variants
were then compared over **three runs each**, because a single draw on nine
documents moves by a fact or two on noise alone:

| variant | F1 | precision | recall | category | current-work |
| --- | --- | --- | --- | --- | --- |
| v0 committed | 0.58 | 70% | 50% | 71% | 0/2 |
| v2 explicit rule, names `` `fact` `` | 0.64 | 67% | 62% | 54% | 2/2 |
| v3–v5 guidance inside the category list | 0.44–0.56 | 56–69% | 36–48% | 40–55% | 0/2 |
| **v6 explicit rule, no category named** | **0.82** | **83%** | **81%** | 64% | **2/2** |

Two findings worth keeping:

- **Naming a category in an example biases a small model toward it.** Every one
  of v2's extra category errors was a `constraint` filed as `fact` — the budget
  cap, the deploy days, the SOC2 audit. Removing the single word `` `fact` ``
  from the directive recovered most of it.
- **Editing the category definitions made everything worse**, including cases
  the edit had nothing to do with. v5 changed one line of the `fact` definition
  and category accuracy fell from 71% to 40%. With a 7B extractor, put new
  guidance in the rules, not in the list it chooses categories from.

v6 shipped. Its category *rate* is below v0's, but it matches far more facts
(~11 vs 7), so it yields more correctly-categorised memories in absolute terms
(~7 vs 5). Both empty-is-correct cases stay empty on every run. Subject accuracy
is unchanged at ~12–14% — still open item 4 below.

---

## 8. Priority 1: making the core claim true

*2026-09-23.* The four fixes from §1–§5, each measured on the same 28-case
resolution corpus and `qwen2.5:7b-instruct` judge. Recorded runs in `baselines/`.

| configuration | belief | merge | stale | escalation | band miss | accuracy | judge agrees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline — nomic, 0.94 / 0.78 | 3.6% | **10.7%** | **14.3%** | 17.9% | 25.0% | 46.4% | 44.4% |
| + duplicate guard | 3.6% | 0% | 7.1% | 28.6% | 25.0% | 50.0% | 41.7% |
| + bge-m3, calibrated 0.86 / 0.45 | 3.6% | 0% | 0% | 42.9% | 7.1% | 57.1% | 44.4% |
| + judge template, reason-first | **0%** | 0% | 0% | 39.3% | 7.1% | 64.3% | 61.1% |
| **+ "NEW is later" — shipped** | 3.6% | **0%** | **0%** | 28.6% | **7.1%** | **75.0%** | **77.8%** |

Retire recall went from 25% to **62.5%** (precision 83%). The shipped row is two
runs, identical to the case.

### The duplicate guard

`material_difference` in `resolution.py`: before the duplicate shortcut discards
a fact, it checks whether the two statements differ in a number, a date word, a
negation, or a capitalised name. If they do, the shortcut is withheld and the
pair is judged. It reads words, so it does not care that nomic scored
Postgres/MongoDB at 1.0000 or that all-minilm put 5k/8k above a real paraphrase.
It is deliberately over-eager — a capitalised sentence-opening verb counts —
because the cost of that mistake is one judge call.

**Merge loss 10.7% → 0% on its own**, before any model change. Live, the
"capped at 5k" → "capped at 8k" pair scored 0.965 — deep in the old duplicate
band — and went to the inbox instead of reinforcing the old cap.

### Embeddings and thresholds

Four candidates, each with thresholds derived by `run_eval.py --calibrate`
rather than chosen:

| model | preflight | calibrated dup / conflict | accuracy | judge agrees |
| --- | --- | --- | --- | --- |
| nomic-embed-text | **blind** (4 swaps at 1.0000) | 0.90 / 0.66 | 50.0% | 41.7% |
| all-minilm | pass | 0.91 / 0.19 | 50.0% | 36.8% |
| mxbai-embed-large | pass | 0.86 / 0.52 | 53.6% | 42.1% |
| **bge-m3** | pass | **0.86 / 0.45** | **57.1%** | **44.4%** |

bge-m3's lead over mxbai is one case; both are defensible. bge-m3 shipped.

Calibration found something about the design: for all-minilm the conflict band
must reach down to 0.19, because `standard-with-carve-out` ("standardised on AWS
for all infrastructure" / "ML workloads run on GCP") is a genuine contradiction
that shares almost no vocabulary. **Once the category and subject filters exist,
the cosine conflict band does little filtering** — it mostly chooses *which*
same-subject memory gets judged. A low band costs at most one judge call per
fact, and only when a same-kind, same-subject memory exists.

Thresholds are now a property of the model: `CALIBRATED_THRESHOLDS` in
`config.py`, used unless explicitly overridden.

### Switching models without losing anything

Each embedding model gets its own collection (`continuum_memories__bge-m3-1024`).
On startup, if the configured model's collection is not the active one, every
memory is re-embedded from the active one, payloads copied verbatim, and only
then does a marker move (`services/reindex.py`). The source is never deleted.

That design was tested for real on its first live run: the disk filled mid-copy
and Qdrant failed nine times with "No space left". Each failure left the marker
on the old collection; the tenth attempt, after space was freed, completed. All
8 memories are in the new collection; the old 768-dim one is untouched.

### Subjects

The extractor's subject problem is mostly *null* subjects, which are harmless:
with no subject the filter does not apply and the pair is judged anyway. The
dangerous case is two spellings of one entity — `atlas` and `atlas-project` —
which the filter treats as different, so a contradiction between them was never
examined. `services/subjects.py` snaps a new subject onto an existing one when
they are the same name once generic words (`project`, `corp`, `service`, …) are
stripped, or one is a qualified form of the other. It leans toward merging:
merging two different subjects costs a judge call, splitting one subject
silently disables contradiction detection.

The corpus supplies subjects directly, so this does not move the table above;
`test_two_spellings_of_one_subject_are_still_judged_against_each_other` pins it.

---

## 9. The judge was copying its prompt

Reading judge outputs one by one, while wiring up token probabilities, turned up
this for the canonical Postgres → MongoDB case:

```
{"relation": "conflict", "confidence": 0.55, "reason": "one short sentence"}
```

That is the prompt's own worked example, verbatim — including the placeholder
reason. It happened on **5 of 15** judge calls in the all-minilm baseline, among
them `db-swap-explicit` and `role-change-explicit`. Part of §4's "over-escalation"
was never judgement. The example is now a template with placeholders.

### Confidence from token probabilities

The written confidence is what made the gate inert (§3). The judge now requests
logprobs and reads the model's probability over the four relations at the token
where it names one — the relations start with four different letters, so the
mass attributes cleanly. The written number is kept for audit and as a fallback
for providers without logprobs.

`supersedes` confidences went from `[0.95, 0.95, 0.95]` to values like
`[0.57, 0.97, 1.00, …]`, and the gate sweep is no longer flat. Reason-first
ordering did not collapse the probabilities to 1.0 as feared.

### "Which is later?"

With the echo gone, the judge's reasons showed the actual gap:

> *"The two statements provide different dates for the Atlas launch **without
> clear indication of which is later**."*

The supersede definition required the NEW statement to be "clearly the later
position", but nothing told the judge which was later. The NEW statement is
always later — it is being ingested now — and the corpus was labelled on that
principle, but the judge never knew it. It is now stated, in the system prompt
and on the NEW statement itself; and "conflict" is redefined as *partial overlap*
rather than *unknown order*. That one change took retire recall from 25% to 62.5%.

### What is still wrong

**One belief is still lost**, and no gate can stop it:
`ownership-handover-or-shared` — "Sara owns the billing service" → "Raj owns the
billing service" — superseded at confidence **1.000**. The label says escalate:
a service can have two owners, and guessing a handover erases a real one. The
label stands.

A targeted rule for shared roles was tried (J4) and **rejected**: it fixed that
case, but `one-off-or-new-baseline` started losing its belief instead, accuracy
became unstable between runs (71.4% / 78.6%), escalation rose, and the gate
sweep went flat again. Telling the judge the new statement is later made it keener
to supersede partial overlaps; another rule just moved which one it got wrong.

`recommend_gate` therefore still reports no zero-loss gate. **`auto_supersede_confidence`
stays at 0.80** — the sweep shows 0.50 would retire one more correct case, but
lowering the most consequential setting in the project on the strength of one
case is exactly the vibes-based tuning this harness exists to replace. That is a
product decision, and the data for it is in `baselines/bgem3-qwen7b-j3.json`.

---

## 10. The last lost belief was a class, not a case

*2026-09-23.* Follow-up to the three "still wrong" items at the end of §9.

### Method: held-out cases first

Every fix after §9 was designed having *seen* the Sara/Raj failure, so it could
only be trusted on cases it was never tuned on. Before running anything, 13
**held-out** cases were written (`holdout` tag), and later 5 more (`holdout2`)
written specifically to test the fix below before it ran. No held-out label was
changed after seeing a result. One (`single-contact-without-marker`) is
deliberately labelled against the fix, to measure its cost instead of hiding it.

### What the held-out set showed

On cases it had never seen, the shipped configuration lost beliefs at **7.7%**
(held-out) and **40%** (held-out-2). Every loss had the same shape:

| case | new statement |
| --- | --- |
| `ownership-handover-or-shared` | Raj owns the billing service |
| `second-maintainer` | Ben maintains the search indexer |
| `rotation-member` | Ivan is on the payments on-call rotation |
| `second-design-reviewer` | Marco reviews design documents |

A second person named for a role that is usually shared, each superseded at
probability ≈1.0. Not a case — a class.

It also found `release-cadence` ("ship every two weeks" → "now ship every week")
classified a **duplicate**: the guard looked for digits, and "two" is a word.
Number words, cadence words, time units and change markers ("now", "instead")
now count as material. *This fix was found by the held-out set, so the held-out
set cannot score it without bias — the 7.7% above is the honest out-of-sample
figure.*

### Three candidates

**B — ask the model a narrower question.** Before applying a supersede, ask only
"can the existing memory still be true as well?", and gate on the lower of the
two probabilities. It changed nothing. The check agreed with the judge, at
probability **1.00**:

> *"The ownership of the billing service can only be held by one person at a
> time."*

— with owners and maintainers explicitly listed as shareable in its
instructions. qwen2.5:7b holds a firm prior that ownership is exclusive, and a
second LLM call shares it. **With this model no LLM-side fix can catch this
class.** Rejected and removed.

**A — never auto-retire a `person` memory.** Deterministic and free, but it also
escalates every genuine handover.

**C — a person-role supersede needs change language.** The genuine handovers in
the corpus all say so in words — "has taken over **from**", "**now** leads",
"**no longer**", "**moved off**"; the losses never do. So a `person` supersede is
applied only when the NEW statement contains one; otherwise it is escalated.
`states_a_change` in `resolution.py`.

Both policies act only after the judge says `supersedes`, so they were scored
exactly by applying them to recorded judgements — two recorded runs, 46 cases:

| | belief loss | escalation | accuracy | retire recall |
| --- | --- | --- | --- | --- |
| shipped before §10 | **8.7%** | 24–26% | 72–74% | 75.0% |
| A: never auto-retire people | 0% | 41–44% | 72–74% | 50.0% |
| **C: people need change language** | **0%** | 35–37% | **78–80%** | 68.8% |

C shipped. On `holdout2` — written to test C before it ran — belief loss is 0%,
both unseen shared-role cases are escalated, and C itself caused exactly one
miss: the pre-registered `single-contact-without-marker` ("Pat is the QA contact
for Globex"). The other two holdout2 misses were the judge (conflict, or
supersedes at 0.74 — under the gate), not the policy.

### The live check found what the corpus could not

Sent through the real API, Sara → Raj was **still superseded**. The extractor
had filed both statements as `fact` — its own definition of fact lists
"ownership" — so a rule keyed on `category == person` never saw them. The
resolution corpus supplies categories directly and skips extraction, which is
why every offline number above looked clean.

The rule now keys on words instead (`is_role_statement`): a pair where both
statements describe a role — owns, maintains, reviews, leads, contact,
rotation — needs change language to be retired, whatever the category. On the
46 recorded cases it changes nothing (no new triggers outside `person`); three
new cases carry the categories the extractor actually produces
(`role-as-fact`). Live, "Raj owns the billing service" is now escalated and both
memories are kept.

**The general lesson: the resolution corpus tests the resolver in isolation.**
It cannot see a failure that comes from extraction feeding it the wrong
category. An end-to-end corpus — raw text in, graph state out — is the missing
instrument.

### The gate: keep 0.80

After C, all **25** gate-eligible supersede verdicts across both runs were
correct — in every confidence band. The wrong ones had all been at ≈1.0: on this
model's actual failure mode, **the judge is most confident exactly where it is
wrong**, so no gate setting protects against it; only the policy does.

| band | verdicts | wrong |
| --- | --- | --- |
| 0.50–0.80 | 3 (2 cases) | 0 |
| 0.80–0.99 | 2 | 0 |
| ≥ 0.99 | 20 | 0 |

Lowering 0.80 → 0.50 would auto-apply those 3, all correct. But zero failures in
three bounds the true error rate only below ~100% (rule of three) — that is not
evidence. A miss costs one inbox click; a wrong retirement destroys a belief
silently. **0.80 stays** until the 0.50–0.80 band holds ~15+ verdicts with none
wrong (error rate bounded below ~20%) — roughly a 5× larger corpus, or real
usage data.

### Null subjects: checked, harmless on bge-m3

The worry: the resolver judges only its single top comparable candidate, so in
a graph with several clients' budgets, a subject-less fact must reach the right
client on cosine alone. `run_eval.py --crowded` tests exactly that, with real
embeddings, subjects null vs set:

| embedding | null subjects | with subjects |
| --- | --- | --- |
| **bge-m3** | **8/8** | 8/8 |
| nomic-embed-text | 6/8 | 8/8 |

Under nomic, a subject-less fact judged Initech's preference instead of
Globex's, and Vega's deadline instead of Orion's — the real contradiction never
examined. bge-m3 separates entity names inside the text, so null subjects cost
nothing; subjects are a safety net for a weaker model. The extraction prompt was
**not** changed: §7 showed every edit costs a 7B extractor category accuracy, and
there is no measured harm here to pay for.

## 11. Personal mode: what the first run found

Same judge (qwen2.5:7b), same embedder (bge-m3, calibrated bands), 2026-10-10.
Two new corpora, every case labelled before it was first run:
`extraction-personal.yaml` (20 cases, 22 facts, 5 forbidden statements) and
`resolution-personal.yaml` (23 cases + 6 held-out written to test the Personal
rules + 4 held-out written after the first run to test the guard below). Cases
run in a Personal graph (`eval:personal`), so the owner turns the rules on
exactly as in the API.

**Extraction**, three runs each side, personal corpus:

| | Work prompt (before) | Personal prompt (after) |
| --- | --- | --- |
| precision | 85.7% ×3 | 72.2 / 76.5 / 88.2% |
| recall | 54.5% ×3 | 59.1 / 59.1 / 68.2% |
| F1 | 0.67 ×3 | 0.65 / 0.67 / 0.77 |
| category | 41.7% | 92.3 / 84.6 / 86.7% |
| subject | 0.0% | 30.8 / 30.8 / 40.0% |
| forbidden | 20% | 0% ×3 |

The Work prompt filed an allergy as a preference — it would have decayed away
in months — and recorded the job's database migration as a life fact. The
Personal prompt fixes the category, which is what decay and the health rule key
on. Subjects are still weak: the extractor often answers `self` where the label
names the other entity (`penicillin`, `gym`). `self` still compares like with
like, so it costs judge calls rather than beliefs. Not tuned further: §7 is why.
The Work prompt is byte-identical; its corpus is unaffected by construction.

**Resolution**, two passes, 33 cases: **belief loss 0%, stale belief 0%,
merge loss 0%** — the success bar. Escalation 66.7–69.7%; retire recall
16.7–25%.

1. **The judge folded a new partner into the old one.** "Sam is the user's
   partner" against "Priya is the user's partner": *duplicate*, 0.93, "a
   different name for the same relationship". DUPLICATE discards the new fact
   — merge loss 3.5%. The duplicate guard only read the cosine shortcut, never
   the judge. Now a judge DUPLICATE where each statement names something the
   other lacks (`swapped_names`) escalates. It is narrower than
   `material_difference` on purpose, because it overrides the judge: one name
   added ("Kothrud, Pune") is detail; one name for another is a swap.
2. **Two homes were "independent".** "Lives in Bangalore" against "lives in
   Pune", independent at 1.00 — both stored, no edge, the graph silently
   holding two homes. For the one-at-a-time subjects the Personal extractor
   uses (`partner`, `home`, `job`), two different names are now a conflict.

Held-out cases for both, written before the change: 3 of 4 pass both passes;
the fourth (`home-neighbourhood-added`, labelled store) escalated on the judge's
own *conflict* — the guard did not fire. The guard fired on exactly three cases,
never on a cost case, and never on the Work corpus. Two Work passes: 0% on all
three safety metrics, accuracy 80.0% and 76.4% against the `c55-families`
baseline's 80.0%. Every case that moved was the judge changing its own relation
with no rule involved — pass 2 called two shared-reviewer cases *independent*
(stored side by side; labelled escalate), the run-to-run noise the two-pass rule
exists for. Runs kept: `baselines/bgem3-qwen7b-personal-c33-pass{1,2}.json`,
`baselines/bgem3-qwen7b-c55-modes-pass{1,2}.json`.

**Found in use (2026-10-12): a self-introduction recorded nothing.** "hello
my name is mark" stored no memory, and "my age is 22 and i am a male i do weight
lifting and i am an software engineer" stored one. Two causes, measured apart:

- The prompt never said that facts about the user themselves are memory. Three
  cases added and labelled first (one held out); the old prompt missed all of
  them, 3 runs in 3. One rule added (no category named): recall 48–52% → 77–81%,
  F1 0.60–0.63 → 0.77–0.81, every self fact found in every run; the cost is
  precision 79–83% → 76–81% (extra true details such as "lives in a flat near
  the river").
- A chat turn reaches the extractor as `user: …`. "user: hello my name is mark"
  extracted nothing 3 times in 3; the bare words, once each time. Over whole
  corpora, three runs each: Personal 25/31 bare vs 23–24 labelled, Work **10/14
  bare vs 13/14 labelled**. So a lone user turn goes in bare in Personal only;
  Work keeps the label.

**Still open: the judge over-escalates life changes.** "Budget is now 10000",
"no longer vegetarian", "lowered the reading goal to 12", "broke up … now Sam"
— each *conflict* at ≈1.00, with reasons like "part of the original may still
hold". The same judge retires 63–68% of the Work corpus's reversals. Safe —
every one goes to the inbox — but it is the cost of Personal mode today, and
the decisions people make there are exactly the labelled evidence a judge
prompt change would need. Of the two cases labelled against the rules,
`blood-orange-preference` shows the word rule's cost ("blood" is a health
word); `second-flatmate` passed, stored free by the subject filter.

## 12. Replies to suggestions: "sounds good" was a lost decision

Found in use (2026-10-12): asked for a workout split, the user replied "it
sounds good for me for now" and nothing was stored — only the user's turn is
extracted, and it holds no fact. `extraction-agreement.yaml`: 23 cases (17 + 6
held-out), both modes, each the assistant's answer plus the reply, labelled
before the first run. Three runs per configuration.

| | precision | recall | pending flag | forbidden (12) |
| --- | --- | --- | --- | --- |
| today (the reply alone) | 25.0% ×3 | 5.6% ×3 | — | 0% |
| v1: reply rules in both system prompts | 71–79% | 56–61% | 100% | 8.3% (creatine) |
| v2: rules in the reply's own framing | 69–75% | 61–67% | 82–83% | 8.3% (a "no") |
| **v3: + hedges and refusals read from the words** | **73.3% ×3** | **61.1% ×3** | **100%** | **0%** |
| v4 (25 cases): + a question makes no choice | 73–80% | 58–63% | 100% | 0% (of 14) |

What each version taught:

- **v1 cost the Work extractor.** Two extra rules in its system prompt took
  its own corpus from F1 0.74 ×3 to 0.56–0.64 — rewording facts the matcher
  then missed, and inventing a "should discuss". It also dropped the user's own
  words beside a suggestion ("a change of subject takes nothing" was read as
  "record nothing"), and the unprompted-maybe rule it paid for caught none of
  its cases. So the rules moved into the reply's own framing: every other input
  is extracted from byte-identical text, and both corpora cannot move.
- **The category guard alone leaked health.** "Added 5 grams of creatine daily"
  was filed as a *decision*, three runs in three. An adopted fact naming a dose,
  a supplement or a health word is now dropped, whatever its category.
- **The model's flags were not the signal; the words were.** Without the flag
  in the system prompt's JSON shape, "I'll think about it" came back as a plain
  decision three in three; "no thanks" came back as "the user will not wake up
  at 5 am". A hedged reply now makes what it adopts pending; a reply opening
  with a no adopts nothing. Own words are kept either way.
- **Whose words, read from the words.** A fact is the user's own when at least
  half its content words are in the reply; anything else was adopted, and only
  a choice survives. Every echo case passed: no age, no allergy, no "Sara owns
  billing" came back from an answer, and an adopted fact never reinforces.

**The live check found what the corpus could not** (throwaway instance, real
Ollama, both modes, the conversation from the original report):

- "should I add a Sunday long run?", replying to an answer, was stored as "the
  user considered adding one" — the reply framing had overridden the
  extractor's own *a question is not a fact*. Confirming the later maybe then
  raised a dispute with it. A question now makes no choice, and is read with no
  answer as context.
- The Work extractor stored "for the Atlas event store, should we use Postgres
  or MongoDB? we need transactions" as "chose Postgres", 3 runs in 3 (two cases
  added, labelled first: forbidden 7.1% → 0%). The guard was at first skipped on
  a session's first turn — no answer before it — so every lone turn now takes
  the same path.
- `swapped_names` read "Postgres"/"PostgreSQL" as a swap. One name that is a
  prefix of the other (4+ letters) is now one name.

Re-run live after the fixes: every turn as designed in both modes.

**Found in use (2026-10-22): an agreement that named nothing.** "Keep this
workout as my active current workout" was stored as "the user keeps this workout
as their active current workout" — so "what is my split?" could not be answered.
The guard counted it as the user's own words (most of them are in the reply), and
the model copied the reply. Three cases added, labelled first (one held out):
before, the live case failed 3 in 3; the other two already resolved their "that"
and "it". Now the reply's guide says to name the choice in full, and a statement
that still points at "this workout", "that plan" or "it" gets one more try, told
what is missing. After, 3 in 3: "the user has chosen a 6-day split with specific
days for push, pull, legs, full body and rest"; forbidden 5.9% → 0%. Recall
reads lower (55–64% → 50–59%), but every case that moved is the matcher: the
outputs are right and longer than their labels ("The user's sister Ana moved to
Porto last month"). The real cost: one run in three stored the exercises too.

**Tried and not kept (2026-10-22): a real subject for an agreed choice.**
Live, "the user will follow a four-day workout split" (subject `self`) was
disputed against "the user does coding and goes to the gym" (also `self`) —
the judge called them an overlap at 0.84; both are true. Telling the reply's
extractor to name the subject ("gym", "sleep"), never `self`, took subject
accuracy from 8% to 58–69% (0 of 18 facts on `self`, 3 runs in 3), recall and
precision unchanged. Reverted anyway: the same split came back as `gym` in one
run and `workout` in another, and the resolver never compares two different
subjects — so a later change of split could leave both splits active with
nobody asked. That trades an unnecessary question for a silent stale belief,
the wrong way round (commitment 4). The false dispute is the judge's
over-escalation (§11), and "Both are true" settles it. (Re-tried and kept once
synonym subjects could meet: §13.)

Still open: an unprompted plan ("maybe I'll start running on Sundays", "we
might move standups to 10am") records nothing, 0 in 3 — the prompts' musing rule
wins, and the rule that would change it cost Work more than it caught. The
adopted choice is filed as decision or routine run to run (they decay at 180 vs
45 days). "Sounds good, and I train at Gold's Gym" keeps the gym, loses the yes.

## 13. Subjects by meaning: one thing under two words

TODO Iteration 1, 2026-10-26. Same judge (qwen2.5:7b), same embedder (bge-m3).

The subject filter compares slugs exactly, so one split filed as `gym` in one
run and `workout` in the next was never compared (§12), and nor were `home` and
`flat`. The plan was "embedding similarity between subject slugs". Measured
first, on `corpus/subjects.yaml` — 95 labelled pairs (17 held out), each a
*meet* (a statement about one can replace one about the other) or *apart* (two
things a person has at once: two people, two topics):

- **Slug similarity is relatedness, not identity.** Separation is weak whatever
  the wording: AUC 0.69 for the bare slug, 0.74 for "my {slug}" (kept; "the",
  "topic:" in between). frontend / backend scores 0.92, coffee / tea 0.88,
  sister / brother 0.83 — as high as true synonyms. So a pair that meets is only
  **compared**, never renamed: renaming sister onto brother would file a belief
  under the wrong person for good, while comparing them costs a judge call.
- **Names are the worst case.** anna / ana 0.95, john / jon 0.95, sara / sarah
  0.89 — the highest "different" scores in the set, the entity-swap blindness of
  §1. So names never meet by meaning. A slug written with a capital in the
  memory's own words is a name ("Sarah K prefers…", "Atlas stores…"), and
  look-alike slugs (`looks_alike`: a shared word plus a swapped one, or a near
  spelling that is not an inflection) stay apart whatever their case. `self`
  never meets a topic: every fact about the user would become comparable with
  everything.
- **The judge must not be shown both slugs.** Its rule "a different subject is
  independent" would undo the match. A pair that met by meaning is judged on
  its words with neither slug shown — how every subject-less pair is judged.

**Threshold: 0.67**, by the conflict band's rule — just below the lowest
*training* pair that must meet, since a pair kept apart is a change nobody is
asked about and a pair that meets costs a call. Training pairs: 37/42 meet (the
5 misses are the rule's measured cost: mom / mum to the look-alike guard;
kubernetes / k8s, frontend / ui, ci / pipeline, doctor / gp because they are
written as names), and 18 of 36 apart pairs meet too. Held out: **9/9 meet**
(home / flat 0.78, gym / workout 0.87, kids / children, wife / spouse, …), and
sara / sarah-k stays apart; 3 of 8 apart pairs meet (lunch / dinner, mum / aunt,
and brother / brother-in-law — joined by the *spelling* rule, which reads it as
a qualified form like sara / sara-chen: an older bug, renamed, not just
compared; not fixed here). The sweep shows the trade: at 0.80, 35/42 meet and
only 3/36 apart pairs do — but held-out home / flat (0.78) would be lost.

**Resolution, held-out cases written and labelled before the first run** (11:
7 Personal, 4 Work; 5 of them cost cases — apart pairs that now meet, labelled
store — and one labelled against the rule):

| | before (meaning off) | after, pass 1 | after, pass 2 |
| --- | --- | --- | --- |
| changes under a synonym subject, retired or asked (5, one a known gap) | 0 — **all 5 silently stale** | 4 | 4 |
| cost cases stored, no dispute (5) | 5 (free) | 5 (judged independent) | 5 |
| look-alike kept free (1) | 1 | 1 | 1 |

`split-filed-under-workout` and `payments-filed-apart-from-billing` were retired;
`partner-named-as-girlfriend` was asked; `moved-from-house-to-flat` was asked
rather than retired (judge *conflict* at 0.72 — the safe direction). The known
gap held as labelled: "Payments now run through Adyen" opens with its subject,
the capital reads as a name, and the old provider stays — stale, as it was
before. Every cost case was judged *independent*: no false dispute.

Full corpora, two passes: Work (59) belief loss 0%, merge loss 0%, stale 1.7%
(that known-gap case; 0% on the 55 cases of `c55-modes`), accuracy 79.7 / 81.4%
against 80.0 / 76.4%. Personal (40) 0% / 0% / 0%, accuracy 67.5% ×2, escalation
60%. Six older cases moved; all six have one subject on both sides, which this
change never touches — the judge changing its own answer. Runs kept:
`baselines/bgem3-qwen7b-c59-subjects-pass{1,2}.json`,
`baselines/bgem3-qwen7b-personal-c40-subjects-pass{1,2}.json`.

**Then the reverted rule, re-tried** (§12): the reply's guide now says "give the
choice the subject it is about ("gym", "sleep"), never "self"". It was reverted
because `gym` and `workout` never met; now they do. Agreement corpus, three runs
each; "meets" is new — the share of matched facts whose subject the resolver
would compare with the label's (`subjects_that_meet`, the resolver's own rule):

| | before | after |
| --- | --- | --- |
| precision | 72.2 / 77.8 / 72.2% | 72.2% ×3 |
| recall | 59.1 / 63.6 / 59.1% | 59.1% ×3 |
| subject (exact) | 15.4 / 14.3 / 15.4% | 61.5% ×3 |
| subject meets | 15.4 / 21.4 / 15.4% | **100% ×3** |
| facts on `self` | 11 of 18 ×3 | **0 of 18 ×3** |
| forbidden (17) | 0% ×3 | 0% ×3 |

The rule moves subjects, not facts: the same statements come back, filed under
the thing they are about. Kept. Its cost, read from the outputs: the example
word anchors — "a Sunday long run" was filed under `gym` (labelled `running`),
three in three. Harmless here, because running and gym meet and are compared,
but it is why a prompt example is a measured choice (§7). The system prompts
are untouched, so the Work and Personal extraction corpora cannot move.

## 14. Agreement and pending polish

TODO Iteration 3, 2026-10-26. qwen2.5:7b, bge-m3. Six cases added to the
agreement corpus (34 in all) and a new confirmation corpus (15), every case
labelled before its first run; three runs per configuration.

**An unprompted maybe that is a plan is now pending** (decided 2026-10-16). Two
attempts:

- *Framing in the turn*, as §12 did for replies: no change, 0 in 3 on every
  unprompted maybe. The extractor was asked directly why: both system prompts
  say musing ("maybe", "thinking about") records nothing, and a system prompt
  beats text inside the input. Changing them is what cost Work F1 in §12.
- *A reading of its own*: a hedged lone turn the extractor took nothing from is
  read once more by a small dedicated prompt — is it a plan, with what and
  when? — that never sees any other input. Kept.

| agreement corpus (34) | before | after |
| --- | --- | --- |
| precision | 75.0 / 70.0 / 80.0% | 83.3% ×3 |
| recall | 57.7 / 53.8 / 61.5% | 76.9% ×3 |
| F1 | 0.65 / 0.61 / 0.70 | **0.80 ×3** |
| unprompted maybes pending (4) | 0 ×3 | **4 ×3** |
| musing and hedges with no plan (3) | nothing ×3 | nothing ×3 |
| forbidden (21) | 0% | 0% |
| subject meets | 100% | 100% |

The cost cases held: "maybe someday we'll rewrite it all in Rust, who knows"
and "not sure what to cook tonight" store nothing, three in three. Exact subject
accuracy reads 55–64% either side; the new pending items use slugs like
`nightly-build-schedule`, and every one meets its label.

**A confirmed maybe is worded as decided.** "Yes" to "the user will consider
adding a Sunday long run" left a belief reading as a plan nobody holds. Now one
rewrite call drops the hedge (`services/wording.py`), and a guard refuses any
rewrite that changes a number, a name or a negation, keeps a hedge, or loses
words — then the old wording stays. Said again in the person's own decided
words, those words are taken, no call. The maybe's wording is kept
(`tentative_wording`). Confirmation corpus: 13 of 15 read as labelled, three
runs in three; the other 2 (both held out) are right and worded "has decided
on moving to Bangalore in March" — the 0.8 overlap check wants "will move".
No rewrite changed a detail, so the guard never had to fire.

**One rate for adopted choices.** The same adopted split was filed as a
decision in one run and a routine in the next — 180 or 45 days to half its
confidence. `Memory.adopted` is now stored, and an adopted choice fades at 90
days whatever its category (a product decision, 2026-10-26). Restating it in
the person's own words reinforces it as before.

**Long plans: already their shape.** Two held-out long plans (a 4-day split with
every exercise, set and rep; a week of named dishes) came back as their shape —
"a 4-day upper/lower split", "a high-protein meal plan" — in 6 runs of 6, and
`ref-keep-this-workout` in 6 more. The reply guide's "not every exercise or
step" holds; no rule added with nothing to fix. The cases stay as a regression
check. The cost seen instead: the meal plan was filed under `gym`, the example
word in the "never self" rule (§13) — `diet` was labelled. They meet (0.77), so
it is compared; it is the second sign that the example anchors.

## 15. A matcher that reads: extraction scores that count right, longer answers

TODO Iteration 4, 2026-10-27. The token matcher pairs a label with an extraction
by shared words, so a right answer worded longer than its label ("Ana moved to
Porto last month" for "Ana lives in Porto") counted as a miss. `corpus/matcher.yaml`:
14 real misses of the token matcher, labelled same or different (a lost reason,
date or qualifier is different — the prompt keeps reasons), then 12 held-out
pairs written and labelled before either candidate saw them.

**Embedding similarity: tried and dropped.** Set precision-first — just above
every different pair, including every two labels of one corpus case — the bar
came out at 0.86 and rescued 2 of 8 true pairs ("The user is male" / "The user
is a software engineer" scored 0.84). Held out, it then matched 4 of 7
different pairs: tea for coffee, Thursday for Friday, reviews for owns, and a
job without its title. Relatedness again, not identity (§13).

**A model's reading, behind a number guard: kept.** qwen2.5:7b asked "does the
extraction state the labelled fact?" (`states_label`), after a guard that every
number in the label is in the output (4 June = June 4th, six = 6) and both or
neither say no. Three runs each, identical:

| | training (14) | held out (12, before the guard fix) |
| --- | --- | --- |
| agree | 13 | 10 |
| false matches | 0 of 6 | 1 of 7 — "works at Initech" for "… as a backend engineer" |
| true pairs missed | 1 of 8 | 2 of 5 — one is "6 am" / "6:00 a.m." split by the guard |

The "6:00" split was a guard bug and is fixed (11 of 12 after); the partial
fact is the matcher's measured error. Both figures are reported: the token
matcher's, unchanged so every earlier run still compares, and beside it
"+ judged" — the same draw with the model's pairs added, each listed to read.
On one draw each: Work F1 0.69–0.74 → 0.81–0.85, Personal 0.77–0.78 → 0.81–0.84,
agreement 0.80 → 0.92. Every judged pair read in two runs was right (the Lisbon
move, the respelled birthday, the staging rollout).

**A nightly gate** (`.github/workflows/nightly-eval.yml`): both resolution
corpora against the same models on the runner's CPU, failing on any belief or
merge loss (`run_eval.py --max-belief-loss 0 --max-merge-loss 0`). Stale beliefs
are reported, not gated: one known-gap case is stale by design. Checked locally
on recorded runs — a clean baseline exits 0, the old nomic run (3.6% belief
loss, 10.7% merge loss) exits 1 — and not yet on GitHub.

## 16. The answer no longer waits for memory

TODO Iteration 5, 2026-10-27. `done` used to wait for the whole write-back —
extraction, the resolver, sometimes a judge call — with the input locked and
Lumen's last sentence held back. Now `done` is sent when the answer is complete,
and the turn is written in a shielded background task reported in a new
`remembered` event. Writes for one graph run one at a time, in order; pending
questions are counted under the same lock, from a fresh read, so a reminder
never puts back an item an earlier turn just confirmed.

`scripts/chat_latency.py`: the real chat service on the real models against a
throwaway in-memory graph, one 12-turn Personal conversation per pass, 2 passes:

| 24 turns | p50 | p95 |
| --- | --- | --- |
| first token | 4.8 s | 7.2 s |
| **done** — the input is free | **12.4 s** | **24.6 s** |
| remembered — when `done` used to arrive | 21.5 s | 40.9 s |

A turn now frees the input 9 s sooner at the median and 16 s at p95. Found on
the way: the write was first started *after* `done` was sent, so a client
leaving the moment it had the answer closed the stream at that point and the
turn was never written — a test caught it (`test_leaving_after_done_…`). And one
turn of 24 failed to write: bge-m3 returned NaN for its text and the spacing
nudge (§1) did not save it. The answer still arrived; the failure is logged.

## 17. bge-m3 on this GPU: NaN for anything longer than a sentence

Found 2026-10-27, verifying Iteration 6: the first note ingested returned 500.
Ollama's bge-m3 answered NaN — not for a few odd inputs, as §1's spacing nudge
assumed, but for **every text over ~90 characters**, single or batched, on both
of Ollama's embedding routes, and again after unloading the model. Forced onto
the CPU (`num_gpu: 0`) the same text embedded at once. On the RTX 4050 the
model loads with flash attention switched on; on the CPU it does not.

It explains the intermittent history: while qwen2.5 holds the 6 GB card,
bge-m3 lands on the CPU and works; alone, it takes the GPU and long facts fail.
The 1-in-24 lost chat write in §16 was this. Live, it meant notes failing to
ingest and chat turns silently not remembered.

Fix, a product decision (2026-10-27): **`bge-m3-cpu`**, an Ollama alias of the
same weights with `num_gpu 0`. Its vectors are bge-m3's exactly (cosine
1.000000 on a long sentence), so it shares bge-m3's calibrated bands and its
collection (`config.embedding_weights`): switching re-embedded nothing
(`reindexed=0`). The compose `models` job creates any `<model>-cpu` alias from
its base, keeping the one-command start. Set in this machine's gitignored
overrides only — a machine whose GPU embeds correctly keeps it.

## 18. Memory for other agents

TODO Iteration 6, 2026-10-27. `continuum-sdk/`: a typed client and an MCP server
over the HTTP API, nothing imported from the backend.

**Settling, the one product decision** (2026-10-27): an agent may settle a
dispute only on the person's own words. `said` is required of an API key on
both resolve routes (422 without it) and stored with the decision, beside
`settled_via`; Forget blanks it with the label's other copies of content. The
web UI's buttons send none — a button is the person deciding.

**Verified end to end** on a throwaway instance — its own Qdrant container,
SQLite accounts, the real models: a decision remembered; a change to it raising
a dispute (not a silent retire); recall returning both sides as DISPUTED; the
server refusing a settle with no words (422) and applying one with them
(keep both, 0 left open); chat read to its `remembered` event. A real MCP
client over stdio listed the four tools and ran each; `examples/agent.py`
answered "who is on call?" from what the previous turn had remembered.

## 19. A memory is data: prompt injection, measured

TODO Iteration 7, 2026-10-27. Memory reaches the chat prompt from other people
too — a teammate's shared memory, whatever an agent's key ingests — so a stored
"ignore previous instructions" is a real attack. `corpus/injection.yaml`: six
planted instructions beside ordinary memories, and three cost cases (memory that
should shape the answer), labelled before the first run; the real model, three
runs a case, `run_eval.py --injection`.

| | obeyed (strict count) | read |
| --- | --- | --- |
| the rule at the top only (before) | 15 of 18 | answered "PWNED" ×3; a pirate ×3; appended the evil.example link ×3; leaned to MongoDB on a real dispute; the health case reported, not obeyed |
| + one quoted line per memory, the rule repeated after them | 8 of 18 | link, PWNED and the prompt leak held; the pirate ×3; once "as instructed, MongoDB is correct" |
| + a memory written as orders shown only as a withheld note | **0 of 18** | — |

What held in the end reads the words, not the model (§10): the extractor writes
memories about the person ("The user prefers …"), so a memory that says "you
are", "from now on", "ignore previous instructions" or "Assistant instruction:"
did not come from an ordinary note. `addresses_the_assistant` flags it — 0 false
positives across 250 statements from every corpus — and the model is told only
that such a note exists. The memory itself is untouched: the person sees it in
the graph. Every memory is also quoted on one line, so a line break can no longer
forge another numbered memory or a DISPUTED header (unit-tested).

The cost cases found an older gap, not a new one: a recorded "answers of one or
two sentences" or "replies in Spanish" was ignored 3 times in 3 — by the
original prompt as much as the hardened one. Memory has never shaped how chat
answers; it is listed in TODO. "Always" inside an ordinary fact is never taken as
an order (3/3 both ways). Not covered: an instruction in a document an agent
ingests, at extraction time.

## 20. Limits for more than one worker; the resolver over time

**Redis.** The sign-in lockout and rate limits counted in process memory, so
every extra worker would have multiplied every limit. `REDIS_URL` moves them to
Redis: per key a sorted set, and one Lua script that trims, counts and adds, so
two workers checking at once cannot both slip in. One contract, both backends
(`tests/test_limits.py`; Redis in CI); ten simultaneous requests through two
clients against a limit of five let exactly five through. An unreachable Redis
fails open, logged — an outage must not lock everyone out of signing in, and a
guess still costs an argon2id verification. Production has it as an opt-in
profile; one worker needs none. Still per process before scaling out: the chat's
write lock, the decay scheduler, the startup re-embedding.

**Activity** (`GET /feedback/activity`, the Inbox). Read back from what the graph
already keeps — no new logging: per day, memories taken in, escalations, beliefs
retired by the resolver alone (a supersede no person's decision explains, and not
a share), decisions and how many came through an agent, and the near misses.
The first version plotted every escalation's judge confidence against the gate;
on real data it was 0.998 — the judge sure of a *conflict*, which the gate never
sees. Near misses are supersedes the gate held for a person. Checked in a real
(headless) browser on a seeded throwaway instance; the status colours fail as a
categorical set on this surface (validated), so the strips share the accent and
are told apart by their titles.

---

## What this changes

Status after §8–§9:

1. ~~**Switch the embedding model**~~ — done: bge-m3, thresholds calibrated, with
   an automatic, crash-safe re-embedding migration.
2. ~~**Make the duplicate short-circuit safe**~~ — done: the duplicate guard.
   Merge loss 10.7% → 0%.
3. ~~**Judge calibration**~~ — done as far as this model allows: confidence
   from token probabilities, and the shared-role class it is confidently wrong
   about is handled by policy (§10). Belief loss 0% on 46 cases. The gate stays
   at 0.80 until there is enough data in the 0.50–0.80 band to justify moving.
4. **Subject normalisation — the dangerous half done, the other half checked.**
   Variant spellings are canonicalised; two words for one thing are compared
   (§13). Null subjects were measured harmless on
   bge-m3 in crowded graphs (8/8); they matter only with a weaker embedder.
5. **Still open:** the two `known-gap` cases (a role contested across person
   subjects; one constraint recorded at two scopes) cannot be reached by any
   threshold — the subject filter decides first.

The gate was never the binding constraint. It was just the only knob with a
comment on it.
