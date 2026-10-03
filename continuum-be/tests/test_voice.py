"""Spoken answers: the synthesis service's refusals and the route.

A fake voice stands in for Piper (it writes a real, tiny WAV), so this needs no
model download. The real voice was checked by round-tripping its audio through
Whisper (see CLAUDE.md, Phase 9).
"""

from __future__ import annotations

import asyncio
import io
import wave

import httpx
import pytest
from fastapi import FastAPI

from continuum.api.routes import speech as speech_route
from continuum.config import Settings, get_settings
from continuum.services.limits import SlidingWindow
from continuum.services.speech import SpeechDisabled, SpeechService
from continuum.services.voice import (
    InvalidText,
    SynthesisService,
    TextTooLong,
    voice_repo_path,
)
from tests.auth_helpers import sign_in_as


class FakeVoice:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def synthesize_wav(self, text, wav, syn_config=None):  # noqa: ANN001, ANN201
        self.spoken.append(text)
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 2205)  # 0.1 s of silence


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def service(**overrides: object) -> tuple[SynthesisService, FakeVoice, list]:
    voice, loads = FakeVoice(), []

    def loader(_settings):  # noqa: ANN001, ANN202
        loads.append(1)
        return voice

    return SynthesisService(settings(**overrides), voice_loader=loader), voice, loads


def test_voice_names_map_to_the_piper_voices_layout():
    assert voice_repo_path("en_US-lessac-medium") == "en/en_US/lessac/medium/en_US-lessac-medium"
    assert voice_repo_path("en_GB-alba-medium").startswith("en/en_GB/alba/medium/")
    with pytest.raises(ValueError):
        voice_repo_path("lessac")


async def test_returns_playable_wav_and_collapses_whitespace():
    synth, voice, _ = service()
    audio = await synth.synthesize("  Atlas runs\n on   Mongo.  ")

    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getframerate() == 22050 and wav.getnframes() == 2205
    assert voice.spoken == ["Atlas runs on Mongo."]


@pytest.mark.parametrize("text", ["", "   ", "*** --- ..."])
async def test_nothing_speakable_is_refused(text):
    synth, voice, _ = service()
    with pytest.raises(InvalidText):
        await synth.synthesize(text)
    assert voice.spoken == []


async def test_over_long_text_is_refused_before_synthesis():
    synth, voice, loads = service(tts_max_chars=10)
    with pytest.raises(TextTooLong):
        await synth.synthesize("x" * 11)
    assert loads == []


async def test_disabled_voice_says_so():
    synth, _, _ = service(tts_enabled=False)
    with pytest.raises(SpeechDisabled):
        await synth.synthesize("hello")


async def test_the_voice_loads_once_under_concurrent_first_requests():
    synth, voice, loads = service()
    await asyncio.gather(*(synth.synthesize(f"sentence {i}.") for i in range(5)))
    assert loads == [1] and len(voice.spoken) == 5


async def test_a_failed_preload_leaves_the_api_up():
    def boom(_settings):  # noqa: ANN001, ANN202
        raise RuntimeError("offline")

    synth = SynthesisService(settings(), voice_loader=boom)
    await synth.preload()
    assert synth.ready is False


# --- The route ----------------------------------------------------------------


def app_with(synth: SynthesisService, *, per_minute: int = 1000) -> FastAPI:
    app = FastAPI()
    app.include_router(speech_route.router)
    app.state.voice = synth
    app.state.speech = SpeechService(settings(speech_enabled=False))
    app.dependency_overrides[get_settings] = lambda: settings()
    sign_in_as(app)
    app.state.tts_limiter = SlidingWindow(per_minute, 60)
    return app


async def post(app: FastAPI, text: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/speech/synthesize", json={"text": text})


async def test_the_route_returns_uncacheable_audio():
    synth, _, _ = service()
    response = await post(app_with(synth), "Hello.")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    assert response.content[:4] == b"RIFF"


async def test_the_route_has_its_own_rate_limit():
    synth, _, _ = service()
    app = app_with(synth, per_minute=2)
    codes = [(await post(app, "Hi.")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


@pytest.mark.parametrize(
    ("overrides", "text", "expected"),
    [({"tts_enabled": False}, "Hi.", 503), ({}, "...", 422), ({"tts_max_chars": 3}, "Hello.", 413)],
)
async def test_refusals_map_to_http_statuses(overrides, text, expected):
    synth, _, _ = service(**overrides)
    assert (await post(app_with(synth), text)).status_code == expected


# --- Choosing a voice ------------------------------------------------------------


class RecordingVoice(FakeVoice):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.scales: list[float] = []

    def synthesize_wav(self, text, wav, syn_config=None):  # noqa: ANN001, ANN201
        self.scales.append(syn_config.length_scale)
        super().synthesize_wav(text, wav, syn_config)


def multi(**overrides: object) -> tuple[SynthesisService, dict, list]:
    voices: dict[str, RecordingVoice] = {}
    loads: list[str] = []

    def loader(name):  # noqa: ANN001, ANN202
        loads.append(name)
        voices[name] = RecordingVoice(name)
        return voices[name]

    return SynthesisService(settings(**overrides), voice_loader=loader), voices, loads


async def test_each_request_can_pick_a_catalogued_voice():
    synth, voices, _ = multi()
    await synth.synthesize("Hello.", voice="en_GB-alan-medium")
    assert voices["en_GB-alan-medium"].spoken == ["Hello."]


async def test_a_voice_outside_the_catalogue_is_refused_not_downloaded():
    from continuum.services.voice import UnknownVoice

    synth, _, loads = multi()
    with pytest.raises(UnknownVoice):
        await synth.synthesize("Hello.", voice="../../etc/passwd")
    assert loads == []


async def test_only_a_few_voices_stay_loaded():
    synth, _, loads = multi(tts_max_loaded_voices=2)
    for name in ("en_US-amy-medium", "en_US-ryan-medium", "en_GB-cori-medium", "en_US-amy-medium"):
        await synth.synthesize("Hi.", voice=name)
    # amy was dropped when cori arrived, so asking for amy again reloads it.
    amy, ryan, cori = "en_US-amy-medium", "en_US-ryan-medium", "en_GB-cori-medium"
    assert loads == [amy, ryan, cori, amy]


async def test_speed_shortens_or_stretches_speech_within_bounds():
    synth, voices, _ = multi(tts_length_scale=1.0)
    await synth.synthesize("Hi.", speed=1.25)
    await synth.synthesize("Hi.", speed=9.0)  # clamped to 1.8
    scales = voices["en_US-lessac-medium"].scales
    assert scales[0] == pytest.approx(0.8)
    assert scales[1] == pytest.approx(1 / 1.8)


def test_a_custom_server_default_is_offered_too():
    custom = settings(tts_voice="en_US-libritts_r-medium")
    synth = SynthesisService(custom, voice_loader=lambda _voice: None)
    assert synth.catalogue()[0].id == "en_US-libritts_r-medium"
    assert synth.is_allowed("en_US-libritts_r-medium")
