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

## What this changes

Nothing in this file is a tuning problem. In priority order:

1. **Switch the embedding model** and re-derive both thresholds from the
   preflight distribution. Until then every resolution number is noise.
   Changing the model changes `EMBEDDING_DIM`, and vector size is fixed at
   collection creation — the collection must be recreated.
2. **Make the duplicate short-circuit consult the judge**, or exclude
   quantity/date-bearing categories from it. It is currently the only path that
   discards an incoming fact, and it fires on exactly the contradictions that
   matter most.
3. **Stop treating `auto_supersede_confidence` as the main dial** until the judge
   produces calibrated confidence. Right now the band thresholds do the work.
4. **Fix subject normalisation** in extraction before relying on the subject
   filter.

The gate was never the binding constraint. It was just the only knob with a
comment on it.
