"""Dictation: the service's refusals and the route around it.

A fake model and decoder stand in for Whisper, so this needs no download. The
real model is exercised by hand (see the Phase 7 notes in CLAUDE.md).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from continuum.api.routes import speech as speech_route
from continuum.config import Settings, get_settings
from continuum.services.speech import (
    AudioTooLong,
    InvalidAudio,
    SpeechDisabled,
    SpeechService,
)
from tests.auth_helpers import sign_in_as

RATE = 16_000


class FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, samples, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        self.kwargs = kwargs
        segments = iter([SimpleNamespace(text=" Atlas moved "), SimpleNamespace(text="to Mongo. ")])
        return segments, SimpleNamespace(language="en")


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def seconds(n: float):
    return lambda _audio: np.zeros(int(n * RATE), dtype=np.float32)


def service(decoder=seconds(3), **overrides: object) -> tuple[SpeechService, FakeModel, list]:
    model = FakeModel()
    loads: list[int] = []

    def factory(_settings):  # noqa: ANN001, ANN202
        loads.append(1)
        return model

    speech = SpeechService(settings(**overrides), model_factory=factory, decoder=decoder)
    return speech, model, loads


async def test_segments_are_joined_into_one_message():
    speech, model, _ = service()
    result = await speech.transcribe(b"audio")

    assert result.text == "Atlas moved to Mongo."
    assert result.language == "en"
    assert result.duration_seconds == 3.0
    # Silence skipped, fixed language: see SpeechService._transcribe.
    assert model.kwargs["vad_filter"] is True and model.kwargs["language"] == "en"


async def test_an_over_long_recording_is_refused_before_any_transcription():
    speech, model, loads = service(decoder=seconds(121), speech_max_seconds=120)
    with pytest.raises(AudioTooLong):
        await speech.transcribe(b"audio")
    assert model.calls == 0 and loads == []  # a decode, not minutes of CPU


async def test_undecodable_audio_is_bad_input_not_a_crash():
    def broken(_audio):  # noqa: ANN001, ANN202
        raise ValueError("not audio")

    speech, _, _ = service(decoder=broken)
    with pytest.raises(InvalidAudio):
        await speech.transcribe(b"garbage")


async def test_an_empty_recording_is_refused():
    speech, _, _ = service()
    with pytest.raises(InvalidAudio):
        await speech.transcribe(b"")


async def test_disabled_dictation_says_so():
    speech, _, _ = service(speech_enabled=False)
    with pytest.raises(SpeechDisabled):
        await speech.transcribe(b"audio")


async def test_the_model_loads_once_under_concurrent_first_requests():
    speech, model, loads = service(speech_concurrency=4)
    await asyncio.gather(*(speech.transcribe(b"audio") for _ in range(4)))
    assert loads == [1] and model.calls == 4


async def test_a_failed_preload_leaves_the_api_up():
    def boom(_settings):  # noqa: ANN001, ANN202
        raise RuntimeError("no network for the download")

    speech = SpeechService(settings(), model_factory=boom, decoder=seconds(1))
    await speech.preload()  # logs, does not raise
    assert speech.ready is False


# --- The route ----------------------------------------------------------------


def app_with(speech: SpeechService, **overrides: object) -> FastAPI:
    app = FastAPI()
    app.include_router(speech_route.router)
    app.state.speech = speech
    app.dependency_overrides[get_settings] = lambda: settings(**overrides)
    sign_in_as(app)
    return app


async def post(app: FastAPI, data: bytes) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/speech/transcribe", files={"audio": ("clip.webm", data, "audio/webm")}
        )


async def test_the_route_returns_the_text():
    speech, _, _ = service()
    response = await post(app_with(speech), b"audio")
    assert response.status_code == 200
    assert response.json() == {
        "text": "Atlas moved to Mongo.",
        "language": "en",
        "duration_seconds": 3.0,
    }


async def test_an_oversized_upload_is_refused_by_size():
    speech, model, _ = service()
    response = await post(app_with(speech, speech_max_bytes=10), b"x" * 11)
    assert response.status_code == 413
    assert model.calls == 0


@pytest.mark.parametrize(
    ("overrides", "decoder", "expected"),
    [
        ({}, "broken", 422),
        ({"speech_enabled": False}, None, 503),
        ({"speech_max_seconds": 1}, None, 413),
    ],
)
async def test_refusals_map_to_http_statuses(overrides, decoder, expected):
    def broken(_audio):  # noqa: ANN001, ANN202
        raise ValueError

    speech, _, _ = service(decoder=broken if decoder else seconds(3), **overrides)
    response = await post(app_with(speech, **overrides), b"audio")
    assert response.status_code == expected


async def test_status_tells_the_ui_whether_to_offer_the_microphone():
    speech, _, _ = service()
    transport = httpx.ASGITransport(app=app_with(speech))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/speech/status")).json()
    assert body == {"enabled": True, "ready": False, "max_seconds": 120, "language": "en"}
