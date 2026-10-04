"""End-to-end smoke test against a running stack.

    docker-compose -f local.yml up -d            # from continuum-be/
    # sign in to the web UI, Account -> API keys -> create one, then:
    cd continuum-be
    CONTINUUM_API_KEY=ck_... uv run python scripts/seed_demo.py

The notes are stored as whoever owns the key. Use a throwaway account if you do
not want demo memories in your own graph.

Feeds in a short arc of work notes where a decision is later reversed, then asks
about it over chat. Watch three things:

  * the reversal is either auto-superseded with edges written, or escalated to
    the inbox — never silently overwritten,
  * the narrowing qualifier ("Postgres stays for reporting only") should land in
    the inbox rather than being guessed at,
  * the chat answer *surfaces* the disagreement instead of picking a side.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

BASE = os.getenv("CONTINUUM_URL", "http://localhost:8000/api/v1")
API_KEY = os.getenv("CONTINUUM_API_KEY", "")

NOTES = [
    "Kicked off the Atlas project today. Sara is leading the data platform side. "
    "We decided to use Postgres over Mongo because the reporting layer needs real "
    "joins and we already run Postgres in prod.",

    "Call with Acme. They really don't want weekly status calls — they'd rather get "
    "written async updates every Friday. Budget is capped at 5k/month, hard limit.",

    "Shipped Atlas v1 to staging on the 14th.",

    # The reversal. This is the interesting one.
    "Revisited the Postgres decision. Volume projections changed — we're moving the "
    "event store to Mongo after all, Postgres stays for reporting only.",
]


async def main() -> None:
    if not API_KEY:
        raise SystemExit(
            "Set CONTINUUM_API_KEY. Create one in the web UI: Account -> API keys."
        )
    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with httpx.AsyncClient(timeout=180.0, headers=headers) as client:
        health = (await client.get(f"{BASE}/health")).json()
        print(f"health: {health}\n")
        if health.get("status") != "ok":
            print("!! Stack is degraded — check Qdrant and your LLM endpoint.\n")

        for i, note in enumerate(NOTES, 1):
            print(f"--- note {i} " + "-" * 50)
            print(f"{note[:90]}...\n")

            resp = await client.post(
                f"{BASE}/ingest", json={"text": note}
            )
            resp.raise_for_status()
            data = resp.json()

            print(f"extracted={data['extracted']}  "
                  f"created={len(data['created'])}  "
                  f"duplicates={data['duplicates_skipped']}  "
                  f"superseded={len(data['superseded'])}  "
                  f"conflicts={len(data['conflicts_raised'])}")
            for m in data["created"]:
                print(f"  + [{m['category']:<10}] {m['content']}")
            for r in data["resolutions"]:
                if r["verdict"] == "new":
                    continue
                conf = f"{r['judge_confidence']:.2f}" if r["judge_confidence"] else "—"
                flag = " [ESCALATED]" if r["escalated"] else ""
                print(f"  ~ {r['verdict']}{flag} (judge {conf}) — {r['reason']}")
            print()

        print("=" * 62)
        for query in ("What database are we using?", "How does Acme want updates?"):
            resp = await client.post(
                f"{BASE}/memories/search",
                json={"query": query, "limit": 5},
            )
            print(f"\nQ: {query}")
            for row in resp.json()["results"]:
                m = row["memory"]
                print(f"  {row['score']:.3f}  [{m['category']:<10}] {m['content']}")

        # --- Phase 2: the contradiction inbox --------------------------
        inbox = (await client.get(f"{BASE}/conflicts")).json()
        print("\n" + "=" * 62)
        print(f"contradiction inbox: {inbox['total']} awaiting a human\n")
        for pair in inbox["conflicts"]:
            print(f"  ? {pair['memory']['content']}")
            for other in pair["conflicting"]:
                print(f"    vs {other['content']}")

        # --- Phase 2: decay preview ------------------------------------
        preview = await client.post(
            f"{BASE}/decay/sweep", json={"dry_run": True}
        )
        print(f"\ndecay preview (nothing written): {preview.json()}")

        graph = (await client.get(f"{BASE}/memories/graph")).json()
        print(f"belief graph: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
        for e in graph["edges"]:
            print(f"  {e['kind']}: {e['source'][:8]} -> {e['target'][:8]}")

        # --- Phase 3: chat over the belief graph ------------------------
        question = "What database are we using for the event store?"
        print("\n" + "=" * 62)
        print(f"chat: {question}\n")

        # remember=false so asking a question does not itself become a memory
        # and skew the counts printed below.
        payload = {
            "messages": [{"role": "user", "content": question}],
            "remember": False,
        }
        async with client.stream("POST", f"{BASE}/chat", json=payload) as stream:
            stream.raise_for_status()
            event = None
            async for line in stream.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data = json.loads(line.removeprefix("data: "))
                    if event == "context":
                        for item in data["memories"]:
                            m = item["memory"]
                            print(f"  ctx {item['score']:.3f} "
                                  f"(sim {item['similarity']:.2f} x "
                                  f"conf {m['confidence']:.2f} x "
                                  f"rec {item['recency']:.2f})  {m['content']}")
                        for group in data["disagreements"]:
                            print(f"  !! disputed ({group['subject']}): "
                                  + " VS ".join(m["content"] for m in group["memories"]))
                        print()
                    elif event == "delta":
                        print(data["text"], end="", flush=True)
                    elif event == "done":
                        print(f"\n\n  cited {len(data['cited_ids'])} of "
                              f"{len(data['memory_ids'])} memories")
                    elif event == "error":
                        print(f"\n!! chat failed: {data['message']}")

        total = (await client.get(f"{BASE}/memories")).json()
        print(f"\ntotal memories stored: {total['total']}")
        print("\nThe reversed database decision was either auto-superseded (judge "
              "was confident) or escalated to the inbox (judge was not). Either "
              "way the original is still queryable — nothing was deleted, and "
              "chat reported the disagreement rather than choosing for you.")


if __name__ == "__main__":
    asyncio.run(main())
