# Evaluation — Phase 5

`auto_supersede_confidence` was 0.80 because 0.80 felt about right. This package
exists to replace that sentence with a number.

> **Read [FINDINGS.md](FINDINGS.md) first.** The first real run showed that three
> of the four mechanisms the project rests on were not working, including one
> that silently discarded the exact contradictions the system was built to catch.
> Resolution numbers are not meaningful until the embedding preflight passes.

## What it measures

The system can fail in two directions, and they are not equally bad:

| Failure | What happens | How bad |
| --- | --- | --- |
| **Belief loss** | A belief that should have survived was retired, unasked | Information is destroyed with no way to notice |
| **Stale belief** | A belief that should have been retired was kept, and nobody was told | The agent confidently asserts something false |
| **Merge loss** | A distinct fact was folded into an existing one as a duplicate | The new fact is never stored at all |
| **Escalation** | The pair went to a human | Costs a minute of attention. Not a failure |

A metric that averages these is useless for setting a gate, because moving the
dial trades one for the other. So they are reported separately, and
`recommend_gate` applies the safety constraint *first* — the cheapest gate with
belief loss inside budget — rather than maximising F1. F1 would treat a destroyed
belief and a wasted minute as the same size of mistake.

## Running it

**Check the embedding model first.** Nothing else means anything if it cannot
tell your entities apart:

```bash
uv run python scripts/run_eval.py --preflight
```

It fails a model whose vectors are effectively identical for an entity swap
(nomic scored `Postgres` vs `MongoDB` at 1.0000) — retrieval cannot tell those
entities apart, whatever else is fixed. A high score is not by itself a failure
any more: the duplicate guard reads the words and catches a changed name or
number. `--record` refuses to run when preflight fails, unless you pass `--force`.

Then derive the two cosine bands the model needs:

```bash
uv run python scripts/run_eval.py --calibrate
```

The conflict band goes just below the lowest pair that must be judged; the
duplicate band just above every non-duplicate the guard cannot see. Add the pair
to `CALIBRATED_THRESHOLDS` in `config.py`.

Then, with a reachable LLM and embedding endpoint:

```bash
docker-compose -f local.yml up -d qdrant     # from continuum-be/; Ollama runs on the host
cd continuum-be

uv run python scripts/run_eval.py --record eval-run.json   # one slow pass
uv run python scripts/run_eval.py --replay eval-run.json   # free, repeatable
```

**Record once, sweep forever.** The judge call is the slow, non-deterministic
part. Each outcome stores the judge's relation and confidence *before* the gate
was applied, so every other gate value is recomputed by arithmetic rather than by
asking the model again. A sweep over eight gate values costs zero LLM calls.

```bash
uv run python scripts/run_eval.py --replay eval-run.json --cases  # per-case table
uv run python scripts/run_eval.py --record r.json --only narrowing  # one tag
uv run python scripts/run_eval.py --extraction --baseline           # vs mem0
```

## The corpus

`corpus/resolution.yaml` — 28 labelled contradiction cases.
`corpus/extraction.yaml` — 8 documents with expected facts.

Every case carries a `why`. A label without a rationale is a guess, and a corpus
of guesses will happily certify a broken gate. The loader rejects a case with an
empty rationale and rejects duplicate ids, because a shadowed case disappears
from the denominator without anyone noticing.

Labels are **actions**, not resolver verdicts — `retire`, `escalate`,
`reinforce`, `store`. `Verdict.NEW` and `Verdict.INDEPENDENT` are two routes to
the same behaviour, and grading them apart would mark the resolver down for
reaching a correct answer cheaply.

### The labelling principle

The incoming fact is always the later one, by construction — it is being ingested
now. So a plain reversal **is** a supersede, and "the timing is unclear" is not
on its own a reason to escalate.

What earns an escalation is **partial overlap**: some of the old belief survives
and a human has to say how much. `Postgres stays for reporting only` does not
replace `Atlas stores events in Postgres` — it carves it down and leaves a
remainder nobody has written.

### Cases that are meant to fail

Three cases are tagged `known-gap`. They are here deliberately: a corpus
containing only passes measures nothing. Two of them describe the same real
limitation — the subject filter cannot see a contradiction when the contested
thing is a *role* and the subject is a *person*, or when the same constraint is
recorded at two different scopes. The filter is doing exactly what it was
designed to do; the design has a blind spot.

The third, `implicit-reversal-without-naming`, measures embedding reach rather
than judgement: if the two statements never land in the same cosine band, the
resolver never gets a candidate to compare and the stale belief survives at full
confidence. `band_miss_rate` counts exactly this, separately from judge quality,
because the fix is different — thresholds and embedding text, not the prompt.

## Reading the output

```
  gate    belief-loss   stale   escalation   retire-R   accuracy
  0.80      3.6%         0.0%    21.4%         87.5%      82.1%
  0.90      0.0%         3.6%    32.1%         75.0%      85.7%
```

A trade-off curve, not a leaderboard. Belief loss falls and escalation rises
together. Pick the cheapest gate whose belief loss you can live with.

`judge_agreement` is reported separately from `accuracy` on purpose: it says how
often the judge's own relation matched the label, ignoring the gate entirely.
High agreement with poor accuracy means the dial is wrong. Low agreement means
the dial cannot save you and the prompt is the problem.

If no gate reaches zero belief loss, the recommendation says so rather than
offering the least-bad option. A judge that is confidently wrong is not fixable
by moving a threshold, and a harness that suggests otherwise is worse than none.

## The extraction baseline

`Mem0Extractor` runs Mem0's own extraction against a **throwaway embedded-Qdrant
store per document**, so its dedup-and-update pass cannot turn an extraction miss
into a hit. Mem0 returns flat strings, so its category and subject scores are
structurally zero — that is the measurement, not a bug. A fact with no category
cannot be routed by resolution policy, and one with no subject cannot be filtered
before the judge.

Fact matching is token overlap over content words (Jaccard, threshold 0.34),
greedily paired best-first. Crude and deliberately so: transparent,
deterministic, and it does not make the extraction score depend on an embedding
model that might itself be under test. Read `fact_recall` as "had something
recognisably similar extracted", not "was extracted correctly".

## Layout

```
evaluation/
├── FINDINGS.md     ★ what the first real run found
├── baselines/      recorded runs, replayable at any gate
├── preflight.py    ★ can the embedding model see an entity swap?
├── calibrate.py    ★ derive both cosine bands for one embedding model
├── crowded.py      does the resolver judge the RIGHT memory in a crowded graph?
├── types.py        Action vocabulary, corpus and outcome models
├── corpus.py       strict loading and validation
├── corpus/*.yaml   the labelled data
├── runner.py       executes cases against the REAL resolver
├── metrics.py      ★ pure scoring, replay, sweep, recommendation
├── extraction.py   token matching + the Mem0 baseline adapter
└── report.py       plain-text rendering
```

`metrics.py` is pure — no I/O, no LLM — so the instrument can be tested before it
is pointed at anything. `tests/test_evaluation.py` covers the scoring, the
replay, and the runner against a stubbed judge.
