# Continuum — Frontend

**Not built yet. This is Phase 4.**

The belief graph deserves to be looked at, not just queried. `/memories/graph`
already returns exactly the shape this will consume — nodes carrying status and
confidence, edges carrying the `supersedes` chain — so the backend contract is
settled before a line of UI exists.

## What it will be

- **React + shadcn/ui + Three.js**, a force-directed graph off
  `GET /api/v1/memories/graph`
  - node colour = lifecycle status (`active` / `superseded` / `contradicted` /
    `archived`), node size = confidence
  - `supersedes` edges directed, so you can watch a belief evolve
  - `conflicts_with` edges highlighted — an unresolved disagreement should be
    visible at a glance
  - click a node → its source excerpt and provenance
- **The contradiction inbox** off `GET /api/v1/conflicts`, with confirm / reject
  / keep-both. `keep_both` is a first-class verdict, not a cop-out: plenty of
  apparent contradictions are two things that are simply both true.
- **A chat panel** alongside the graph, streaming from `POST /api/v1/chat` over
  SSE and lighting up the memories each answer cites.

## Why it is empty

Nothing here is scaffolded yet, deliberately — the API surface it will be built
against is finished and tested, so the UI can be written against a real running
stack rather than a mock.

See the [project README](../README.md) for the quickstart, and
[`../CLAUDE.md`](../CLAUDE.md) for the engineering brief.
