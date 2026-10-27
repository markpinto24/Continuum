# TODO — Continuum

Improvements, grouped into iterations. Each iteration is one coherent piece of
work: plan it, write and label its test cases first, measure before and after
(three runs for extraction, two passes for resolution — see `CLAUDE.md`), then
tick it off with the numbers in `FINDINGS.md`.

Status keys: `[ ]` to do · `[~]` in progress · `[x]` done

---

## Iteration 0 — Housekeeping (do first)

- [x] **Commit the uncommitted work**: short replies to agreements, naming
      "this workout" in full, graph centred on launch, chat scroll while
      streaming, graph re-framed after unselecting (`8acee10`)
- [ ] Rebuild the API container so it runs the latest backend code
- [ ] Settle the one false dispute in the live Personal graph
      ("coding and gym" vs the four-day split) with **Both are true**

---

## Iteration 1 — Semantic subject matching *(highest value)* — done, FINDINGS §13

The weakest link today. Many facts are filed under `self`, and one thing is
filed as `gym` in one run and `workout` in another. That causes false disputes
(everything on `self` is compared) and risks silent stale beliefs (`gym` and
`workout` are never compared).

- [x] Treat two subjects as one when their names are semantically close
      (embedding similarity between subject slugs), not only when spelled alike
      — *compared*, never renamed: slug similarity is relatedness, not identity.
      Names, look-alikes and `self` stay spelling-only
- [x] Calibrate the threshold on real subject pairs (same thing / different thing)
      — `corpus/subjects.yaml`, `run_eval.py --subjects`: bge-m3 0.67
- [x] Then re-try the reverted rule "an agreed choice gets a real subject, never
      `self`" (FINDINGS §12) — kept: `self` 11/18 → 0/18, P/R unchanged
- [x] Held-out cases first: synonym subjects that must meet (gym / workout,
      home / flat), and look-alikes that must not (sara / sarah-k) — 9/9 meet,
      sara / sarah-k apart; 11 held-out resolution cases
- [x] Success: no new false disputes (5 cost cases, all independent, 2 passes);
      belief loss 0% on both corpora; stale 0% except one known-gap case added
      this iteration, which was stale before too
- [ ] Follow-ups found: a subject-first sentence ("Payments now run…") reads as
      a name and stays apart; the *spelling* rule renames `brother-in-law` to
      `brother` (a qualified form, like sara / sara-chen)

## Iteration 2 — A judge that asks less *(deferred 2026-10-26: needs a 14B–32B model download)*

Personal mode sends about 68% of cases to the inbox; clear changes ("no longer
vegetarian", "budget is now 10000") come back as "conflict".

- [ ] Run both resolution corpora with a stronger judge (14B–32B local, or an
      API model) — two passes each, compare per metric
- [ ] Calibrate the cosine bands for any new embedder (`run_eval.py --calibrate`)
- [ ] Turn on `CALIBRATED_GATE` once there are 30+ real decisions — a product
      decision, recorded with the evidence
- [ ] Success: escalation down, belief loss still 0%

## Iteration 3 — Agreement and pending polish — done, FINDINGS §14

- [x] An unprompted "maybe I'll start running on Sundays" becomes pending
      — its own small prompt for hedged turns; 4/4 pending ×3, musing still
      nothing, agreement F1 0.61–0.70 → 0.80
- [x] A confirmed maybe drops its hedged wording ("will think about adding…")
      — guarded rewrite; 13/15 as labelled, the other 2 right in other words
- [x] One decay rate for adopted choices, so decision vs routine filing does not
      change how fast they fade (180 vs 45 days) — 90 days, decided 2026-10-26
- [x] Long plans: store the shape (days, order), never the full exercise list
      — already true, 12/12 runs; held-out cases kept as a regression check
- [ ] Follow-up: the "never self" rule's example word anchors (a long run and a
      meal plan both filed under `gym`) — compared anyway, but worth a neutral
      example

## Iteration 4 — Evaluation you can quote

- [ ] **Public benchmark**: LongMemEval or LoCoMo *(deferred 2026-10-27)*
- [ ] **Head-to-head** with Mem0 and Zep on the same data *(deferred 2026-10-27)*
- [x] Better matcher beside the token score — FINDINGS §15. Embedding similarity
      was tried and dropped (held out, it matched tea to coffee); a model's
      reading behind a number/negation guard is kept: held out 11/12, one partial
      fact matched. Reported as "+ judged" beside the token figures
- [ ] Grow the corpora from exported inbox decisions (`--add-cases`) *(waiting on
      an export from the live account)*
- [x] Nightly CI job running the corpora; fail if belief loss rises above 0% —
      `nightly-eval.yml`, belief and merge loss gated; not yet run on GitHub

## Iteration 5 — Speed — done, FINDINGS §16

- [x] Send the chat's `done` event first, write memory in the background, and
      update the turn when it lands (confirming a maybe took ~8 s live) — a new
      `remembered` event; shielded, in order per graph
- [x] Measure and record p50 / p95 latency per chat turn —
      `scripts/chat_latency.py`: done p50 12.4 s / p95 24.6 s, against 21.5 s /
      40.9 s when it waited for memory
- [x] Follow-up: one turn in 24 lost its write to a bge-m3 NaN the nudge did
      not fix — the GPU, not the text: bge-m3 is pinned to the CPU (FINDINGS §17)

## Iteration 6 — Let other agents use it — done, FINDINGS §18

- [x] **MCP server** for Continuum (remember, recall, list disputes, settle) —
      `continuum-sdk`, `continuum-mcp`; settle only on the person's words
- [x] Small Python SDK over the HTTP API, with an example agent —
      `Continuum` / `AsyncContinuum`, `examples/agent.py`
- [x] Docs: "add long-term memory to your agent in 5 minutes" —
      `continuum-sdk/README.md`
- [ ] Publish the SDK to PyPI (installed from the repository today)

## Iteration 7 — Production readiness

- [x] Move rate limits and sign-in lockout to Redis (needed for >1 worker) —
      `REDIS_URL`, `production.yml --profile scale` (FINDINGS §20)
- [ ] Before more than one worker: the chat's per-graph write lock, the decay
      scheduler and the startup re-embedding are still per process
- [x] Observability: escalation rate, decisions per day, judge confidence over
      time — one small dashboard: the Inbox's Activity panel (FINDINGS §20)
- [x] Prompt-injection test: a memory saying "ignore previous instructions…"
      must stay data in the chat prompt — 15/18 obeyed → 0/18 (FINDINGS §19)
- [ ] Memory does not shape how chat answers: a recorded "short answers" or
      "reply in Spanish" is ignored 3/3 (FINDINGS §19 cost cases)
- [x] A production compose file: reverse proxy, HTTPS, built frontend served
      same-origin, `AUTH_ALLOW_WEB_SETUP=false`, `TRUST_PROXY_HEADERS=true`
      (`production.yml`, `continuum-fe/Dockerfile` + `Caddyfile`, `DEPLOY.md`)
- [~] Scheduled backups: `scripts/backup-production.sh` + cron on the VM —
      still to do: copying them off the machine automatically
- [ ] Deploy to the Oracle VM (`DEPLOY.md`) and record the public URL

## Iteration 8 — Interface at scale

- [x] Graph beyond a few hundred nodes: cluster by subject, filter by category /
      status / date — `lib/graph-view.ts`; grouped by subject above 300 (32 vs 8
      fps at 600 in software rendering); a filter never hides half a dispute; the
      UI loads up to 2000 and says when the graph is larger (`truncated`)
- [ ] Past 2000 memories: filter on the server, not only in the browser
- [x] Mobile layout for the chat and inbox — the panel is a full-width sheet
      below 640 px; the header compacts and scrolls within itself
- [x] Accessibility pass (keyboard, screen-reader labels on the graph's lists) —
      the graph as a list (one tab stop, arrows, Enter), legend rows as toggles,
      every icon-only button named
- [ ] Touch targets: inbox buttons are ~23 px tall on a phone (WCAG 2.2: 24)

## Iteration 9 — Showcase

- [ ] 2-minute demo video: a belief changes, the dispute appears, it is settled,
      the history stays
- [ ] README: lead with the problem, the measured results and the demo
- [ ] Public deployment — guide in `DEPLOY.md`
