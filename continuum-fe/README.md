# Continuum — Frontend

**The belief graph, made visible.** A Three.js force-directed view of a memory
graph, a contradiction inbox, and a chat panel that shows its working.

This is the frontend half of the [Continuum monorepo](../README.md). It talks to
`continuum-be` over HTTP and shares nothing else with it.

---

## Why a 3D graph and not a list

A memory list can show you what the agent believes. It cannot show you *how a
belief got there* — which is the only interesting question once memories start
contradicting each other.

The graph encodes four things at once:

| Channel | Meaning |
| --- | --- |
| **Colour** | Lifecycle status. Amber = disputed, and nothing else is loud |
| **Size** | Confidence — how much the system still trusts it |
| **Arrow** | `supersedes`, drawn new → old, so a belief's history reads in one direction |
| **Pulse** | `conflicts_with` — symmetric, unresolved, and deliberately attention-seeking |

Depth is load-bearing, not decoration. A belief graph is not a tree: one subject
accumulates supersede chains *and* conflict edges that cross between them, and in
two dimensions that becomes an unreadable hairball at around thirty nodes.

Conflict edges are the one place the drawing refuses to imply an answer. They get
no arrowhead, because a contradiction is symmetric — asserting a direction would
be the UI deciding the very thing the backend escalated to you.

---

## What it does

- **Belief graph** off `GET /memories/graph`. Click a node to select it; its
  one-hop neighbourhood stays lit and the rest of the scene dims.
- **Memory detail** — the verbatim `source_excerpt` the belief was extracted
  from, its confidence and decay half-life, and every edge as a click-through.
  *Where did the agent get that?* is the question a memory system must be able to
  answer, and this panel is the answer.
  - **Still true** raises confidence and resets the decay clock.
  - **Restore** brings an archived or superseded belief back. Nothing here is a
    one-way door.
- **Contradiction inbox** off `GET /conflicts`, with three verdicts: *this one
  holds*, *the other holds*, *both are true*. **Keep-both is a first-class
  button**, not a skip — plenty of apparent contradictions are two things that
  are simply both true, and burying that option is how a review queue gets
  abandoned.
- **Chat** streaming from `POST /chat` over SSE. The `context` frame lands before
  the first token, so you see which memories the answer stands on — with the
  `similarity × confidence × recency` breakdown — while it is still being
  written. When those memories disagree, a banner says so above the answer.
  Every turn is fed back through ingest, so the graph refreshes when a
  conversation changes it — and the turn says what it did, including when
  nothing durable was found.
- **Answers render as Markdown**: highlighted, language-labelled code blocks
  with a copy button, lists, bold. Model output is untrusted, so HTML it emits
  is shown as text rather than injected.
- **Drag the sidebar's left edge to resize it** (arrow keys work when it is
  focused; double-click resets). The width is remembered per browser.

---

## Running it

With the stack up (`docker compose up -d` from the repo root), `web` is served at
<http://localhost:3000> and nginx proxies `/api` to the API container.

For hot reload against a locally running backend:

```bash
cd continuum-fe
npm install
npm run dev            # http://localhost:5173
```

Vite proxies `/api` to `http://localhost:8000`, so the browser sees one origin
and CORS never enters the picture. Point it elsewhere with
`VITE_API_PROXY=http://host:port npm run dev`, or set `VITE_API_URL` to call an
absolute origin directly.

```bash
npm run lint           # eslint, flat config, TypeScript
npm run build          # tsc -b, then vite build
npm test               # vitest, jsdom — 40 tests, no backend needed
```

---

## Layout

```
src/
├── lib/
│   ├── types.ts           the backend contract, mirrored by hand
│   ├── api.ts             thin typed client + SSE chat stream
│   ├── sse.ts             ★ incremental SSE framing
│   └── memory-style.ts    ★ the visual vocabulary — colour, size, half-lives
├── hooks/
│   ├── use-resource.ts    minimal async resource, newest-write-wins
│   └── use-element-size.ts
├── components/
│   ├── belief-graph.tsx   ★ the Three.js scene
│   ├── memory-detail.tsx  provenance + edges + the two non-destructive actions
│   ├── contradiction-inbox.tsx
│   ├── chat-panel.tsx     ★ streaming, citations, disagreement banner
│   ├── graph-legend.tsx
│   ├── app-header.tsx
│   └── ui/                shadcn-style primitives, owned in-repo
└── App.tsx
```

`lib/types.ts` is written by hand rather than generated from OpenAPI. The surface
is small and changes rarely, and a hand-written mirror is the one place a
breaking backend change shows up as a type error instead of a runtime
`undefined`. **If you change `models/schemas.py`, change that file in the same
commit.**

`components/ui/` holds shadcn components, which are copied into a repo by design
rather than installed — so they are edited here, not upgraded from a registry.

---

## Conventions

- **TypeScript throughout**, `strict`, with no `any` in application code. Even
  the ESLint config is `.ts`.
- **No data-fetching framework.** Three endpoints, no cache sharing, no
  pagination — `useResource` is about forty lines and handles the one thing that
  actually bites: a slow response from a previous key landing after a newer one.
  Adding TanStack Query here would be the frontend's version of the mistake the
  backend avoided by owning its Qdrant access.
- **`three` and `react-force-graph-3d` are split into their own chunk.** They are
  ~85% of the bundle and change only when bumped, so app edits ship ~110 kB
  gzipped and the big chunk stays cached.
- Tests cover the logic that is genuinely easy to get wrong: SSE frames arriving
  split across network chunks, and the inbox sending the verdict you actually
  clicked.

---

## Not built

Search and the memory list (`GET /memories`, `POST /memories/search`) have no UI
yet — the graph is the primary navigation. Decay is visible as confidence but has
no timeline view. Phase 5 evaluation will want one.

See [`../CLAUDE.md`](../CLAUDE.md) for the engineering brief.
