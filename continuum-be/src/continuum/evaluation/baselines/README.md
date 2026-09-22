# Recorded runs

A recorded run is a JSON list of `CaseOutcome`. Each one holds the judge's
relation and confidence *before* the gate was applied, so it can be replayed at
any gate without touching a model:

```bash
uv run python scripts/run_eval.py --replay baselines/<file>.json
```

Keep a run here when it is worth comparing against later — a model change, a
prompt change, a threshold change. The filename should say what produced it.

**A recorded run is evidence, not a target.** If a change makes the numbers
worse, the answer is to understand why, not to relabel the corpus until the
number recovers.
