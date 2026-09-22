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
   Variant spellings are canonicalised. Null subjects were measured harmless on
   bge-m3 in crowded graphs (8/8); they matter only with a weaker embedder.
5. **Still open:** the two `known-gap` cases (a role contested across person
   subjects; one constraint recorded at two scopes) cannot be reached by any
   threshold — the subject filter decides first.

The gate was never the binding constraint. It was just the only knob with a
comment on it.
