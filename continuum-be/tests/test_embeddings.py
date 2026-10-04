"""Embeddings survive an embedder that answers NaN for particular inputs.

Found live: Ollama's bge-m3 fails deterministically on "[acme-corp] Budget ... 5k/month."
and fails the whole batch with it, so one fact made a whole note unrecordable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from continuum.clients.llm import LLMClient, LLMError
from continuum.config import Settings

BAD = "[acme-corp] Budget is capped at 5k/month."


class FakeEmbeddings:
    def __init__(self, poison: set[str], nan_in_vector: bool = False) -> None:
        self.poison, self.nan_in_vector, self.calls = poison, nan_in_vector, []

    async def create(self, *, model, input):  # noqa: A002, ANN001, ANN201, ARG002
        self.calls.append(list(input))
        if any(text in self.poison for text in input):
            raise RuntimeError("failed to encode response: json: unsupported value: NaN")
        value = float("nan") if self.nan_in_vector else 0.5
        return SimpleNamespace(data=[SimpleNamespace(embedding=[value, 1.0]) for _ in input])


def client(fake: FakeEmbeddings) -> LLMClient:
    llm = LLMClient(Settings(_env_file=None))
    llm._embed = SimpleNamespace(embeddings=fake)  # type: ignore[assignment]
    return llm


async def test_one_poisoned_text_no_longer_fails_the_batch():
    fake = FakeEmbeddings({BAD})
    vectors = await client(fake).embed(["hello", BAD])
    assert len(vectors) == 2
    # Batch, then each alone; the bad one again with its spacing nudged.
    assert fake.calls[0] == ["hello", BAD]
    assert any("5k / month" in call[0] for call in fake.calls[1:])


async def test_a_text_that_fails_every_way_still_raises():
    fake = FakeEmbeddings({BAD, "[ acme-corp ] Budget is capped at 5k / month.",
                           "acme-corp Budget is capped at 5k month.", f"{BAD} ."})
    with pytest.raises(RuntimeError, match="NaN"):
        await client(fake).embed([BAD])


async def test_a_nan_inside_the_vector_is_never_returned():
    with pytest.raises(LLMError):
        await client(FakeEmbeddings(set(), nan_in_vector=True)).embed(["hello"])


async def test_other_errors_are_not_disguised():
    class Down(FakeEmbeddings):
        async def create(self, **kwargs):  # noqa: ANN003, ANN201
            raise ConnectionError("embedder down")

    llm = client(Down(set()))
    with pytest.raises(ConnectionError):
        await llm.embed(["hello"])
