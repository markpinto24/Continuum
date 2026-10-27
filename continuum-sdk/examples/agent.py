"""An agent with long-term memory: any OpenAI-compatible model, plus Continuum.

    export CONTINUUM_API_KEY=ck_...                     # Settings -> API keys
    uv run python examples/agent.py                     # talk to it
    uv run python examples/agent.py "we moved Atlas to MongoDB" "what does Atlas use?"

The model is whatever AGENT_LLM_URL serves (default: a local Ollama, the same
qwen2.5 Continuum uses). The loop is the whole pattern:

  1. recall   — before answering, ask Continuum what it believes about the turn;
  2. answer   — with those beliefs in the prompt, disputes and maybes marked;
  3. remember — feed the person's words back. Never the answer: it was made
                from memory, and remembering it would count an echo as evidence.

Continuum does the rest — extraction, deciding what replaced what, asking the
person when it is unsure — so the agent itself stays a dozen lines.
"""

from __future__ import annotations

import os
import sys

import httpx

from continuum_sdk import Continuum
from continuum_sdk.prompt import describe_recall

LLM_URL = os.environ.get("AGENT_LLM_URL", "http://localhost:11434/v1").rstrip("/")
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "qwen2.5:7b-instruct")
LLM_KEY = os.environ.get("AGENT_LLM_API_KEY", "ollama")

SYSTEM = """You are a helpful assistant with a long-term memory of this person.
What you remember about the question is below. Use it; do not invent memories.
If something is DISPUTED, say the record disagrees and give both sides. If
something is UNDECIDED, ask about it rather than stating it.

{memory}"""


def answer(history: list[dict[str, str]], memory: str) -> str:
    response = httpx.post(
        f"{LLM_URL}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_KEY}"},
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "system", "content": SYSTEM.format(memory=memory)}, *history],
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def turn(memory: Continuum, history: list[dict[str, str]], said: str) -> str:
    recalled = describe_recall(memory.recall(said))           # 1. recall
    history.append({"role": "user", "content": said})
    reply = answer(history, recalled)                          # 2. answer
    history.append({"role": "assistant", "content": reply})
    memory.remember(messages=[{"role": "user", "content": said}])  # 3. remember
    return reply


def main() -> None:
    history: list[dict[str, str]] = []
    with Continuum() as memory:
        scripted = sys.argv[1:]
        for said in scripted or iter(lambda: input("you> ").strip(), "quit"):
            if said:
                print(f"agent> {turn(memory, history, said)}\n")


if __name__ == "__main__":
    main()
