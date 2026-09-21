"""Thin client over any OpenAI-compatible endpoint.

Deliberately thin. We are not wrapping a framework's wrapper — the whole point
of owning this layer is that when we need a provider-specific knob later
(logprobs, guided JSON, prefix caching) it is one file to change, not a fight
with somebody else's abstraction.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

import structlog
from openai import AsyncOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from continuum.config import Settings, get_settings

log = structlog.get_logger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._chat = AsyncOpenAI(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
            timeout=self.settings.llm_timeout_seconds,
        )
        self._embed = AsyncOpenAI(
            base_url=self.settings.embedding_base_url,
            api_key=self.settings.embedding_api_key,
            timeout=self.settings.llm_timeout_seconds,
        )

    # --- Chat --------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                temperature if temperature is not None else self.settings.llm_temperature
            ),
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
        }
        if json_mode:
            # Supported by Ollama, vLLM, Groq and OpenAI. Harmless if ignored —
            # `parse_json` below is defensive anyway.
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._chat.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if not content:
            raise LLMError("LLM returned an empty completion")
        return content

    async def complete_json(self, *, system: str, user: str, **kwargs: Any) -> Any:
        raw = await self.complete(system=system, user=user, json_mode=True, **kwargs)
        return parse_json(raw)

    # Deliberately not wrapped in @retry. A stream that fails halfway has already
    # delivered tokens to the client; replaying it from the top would duplicate
    # them. Transport failures surface to the caller, which ends the SSE stream
    # with an `error` event instead.
    async def stream(
        self,
        *,
        messages: Sequence[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield content deltas from a chat completion."""
        response = await self._chat.chat.completions.create(
            model=self.settings.llm_model,
            messages=list(messages),
            temperature=(
                temperature if temperature is not None else self.settings.llm_temperature
            ),
            max_tokens=max_tokens or self.settings.llm_max_tokens,
            stream=True,
        )
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    # --- Embeddings --------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._embed.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        if not vectors:
            raise LLMError("Embedding endpoint returned no vectors")
        return vectors[0]

    # --- Health ------------------------------------------------------------

    async def ping(self) -> bool:
        try:
            await self.embed_one("ping")
            return True
        except Exception as exc:  # noqa: BLE001 - health check must not raise
            log.warning("llm.ping_failed", error=str(exc))
            return False

    async def aclose(self) -> None:
        await self._chat.close()
        await self._embed.close()


def parse_json(raw: str) -> Any:
    """Parse model output that is *supposed* to be JSON.

    Small local models ignore `response_format` often enough that this needs to
    survive markdown fences and leading prose.
    """
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _JSON_FENCE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    # Last resort: grab the outermost {...} or [...] span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise LLMError(f"Could not parse JSON from model output: {text[:300]!r}")
