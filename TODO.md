# TODO — Continuum

Improvements, grouped into iterations. Each iteration is one coherent piece of
work: plan it, write and label its test cases first, measure before and after
(three runs for extraction, two passes for resolution — see `CLAUDE.md`), then
tick it off with the numbers in `FINDINGS.md`.

Status keys: `[ ]` to do · `[~]` in progress · `[x]` done

---

## Iteration 0 — Housekeeping (do first)

- [ ] **Commit the uncommitted work**: short replies to agreements, naming
      "this workout" in full, graph centred on launch, chat scroll while
      streaming, graph re-framed after unselecting
- [ ] Rebuild the API container so it runs the latest backend code
- [ ] Settle the one false dispute in the live Personal graph
      ("coding and gym" vs the four-day split) with **Both are true**

---

## Iteration 1 — Semantic subject matching *(highest value)*

The weakest link today. Many facts are filed under `self`, and one thing is
filed as `gym` in one run and `workout` in another. That causes false disputes
(everything on `self` is compared) and risks silent stale beliefs (`gym` and
`workout` are never compared).

- [ ] Treat two subjects as one when their names are semantically close
      (embedding similarity between subject slugs), not only when spelled alike
- [ ] Calibrate the threshold on real subject pairs (same thing / different thing)
- [ ] Then re-try the reverted rule "an agreed choice gets a real subject, never
      `self`" (FINDINGS §12) — safe once synonyms meet
- [ ] Held-out cases first: synonym subjects that must meet (gym / workout,
      home / flat), and look-alikes that must not (sara / sarah-k)
- [ ] Success: no new false disputes on the agreement corpus; belief loss and
      stale beliefs stay at 0% on both resolution corpora

## Iteration 2 — A judge that asks less

Personal mode sends about 68% of cases to the inbox; clear changes ("no longer
vegetarian", "budget is now 10000") come back as "conflict".

- [ ] Run both resolution corpora with a stronger judge (14B–32B local, or an
      API model) — two passes each, compare per metric
- [ ] Calibrate the cosine bands for any new embedder (`run_eval.py --calibrate`)
- [ ] Turn on `CALIBRATED_GATE` once there are 30+ real decisions — a product
      decision, recorded with the evidence
- [ ] Success: escalation down, belief loss still 0%

## Iteration 3 — Agreement and pending polish

- [ ] An unprompted "maybe I'll start running on Sundays" becomes pending
      (decided 2026-10-16, not achieved — FINDINGS §12)
- [ ] A confirmed maybe drops its hedged wording ("will think about adding…")
- [ ] One decay rate for adopted choices, so decision vs routine filing does not
      change how fast they fade (180 vs 45 days)
- [ ] Long plans: store the shape (days, order), never the full exercise list

## Iteration 4 — Evaluation you can quote

- [ ] **Public benchmark**: LongMemEval or LoCoMo
- [ ] **Head-to-head** with Mem0 and Zep on the same data
- [ ] Better matcher: embedding similarity (threshold validated) beside the token
      score — today correct but longer answers count as misses
- [ ] Grow the corpora from exported inbox decisions (`--add-cases`)
- [ ] Nightly CI job running the corpora; fail if belief loss rises above 0%

## Iteration 5 — Speed

- [ ] Send the chat's `done` event first, write memory in the background, and
      update the turn when it lands (confirming a maybe took ~8 s live)
- [ ] Measure and record p50 / p95 latency per chat turn

## Iteration 6 — Let other agents use it

- [ ] **MCP server** for Continuum (remember, recall, list disputes, settle)
- [ ] Small Python SDK over the HTTP API, with an example agent
- [ ] Docs: "add long-term memory to your agent in 5 minutes"

## Iteration 7 — Production readiness

- [ ] Move rate limits and sign-in lockout to Redis (needed for >1 worker)
- [ ] Observability: escalation rate, decisions per day, judge confidence over
      time — one small dashboard
- [ ] Prompt-injection test: a memory saying "ignore previous instructions…"
      must stay data in the chat prompt
- [x] A production compose file: reverse proxy, HTTPS, built frontend served
      same-origin, `AUTH_ALLOW_WEB_SETUP=false`, `TRUST_PROXY_HEADERS=true`
      (`production.yml`, `continuum-fe/Dockerfile` + `Caddyfile`, `DEPLOY.md`)
- [~] Scheduled backups: `scripts/backup-production.sh` + cron on the VM —
      still to do: copying them off the machine automatically
- [ ] Deploy to the Oracle VM (`DEPLOY.md`) and record the public URL

## Iteration 8 — Interface at scale

- [ ] Graph beyond a few hundred nodes: cluster by subject, filter by category /
      status / date
- [ ] Mobile layout for the chat and inbox
- [ ] Accessibility pass (keyboard, screen-reader labels on the graph's lists)

## Iteration 9 — Showcase

- [ ] 2-minute demo video: a belief changes, the dispute appears, it is settled,
      the history stays
- [ ] README: lead with the problem, the measured results and the demo
- [ ] Public deployment — guide in `DEPLOY.md`
