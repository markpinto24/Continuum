"""Speech to text for chat dictation, with a local Whisper model.

Local on purpose. Continuum holds a person's private working notes; sending
their voice to a cloud recogniser to get those notes typed would undo the point
of running the LLM locally. faster-whisper runs Whisper on the CPU through
CTranslate2 — no GPU, no network after the one-time model download.

Audio is decoded and transcribed in memory and then dropped. Nothing about a
recording is stored or logged except its length: the transcript is user content,
and it only enters the system if the user reviews it and sends it.
"""

from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from continuum.config import Settings
from continuum.core.logger import get_logger

log = get_logger(__name__)

_SAMPLE_RATE = 16_000  # what faster-whisper decodes to


class SpeechError(Exception):
    """Base for refusals. `status` is the HTTP code the route should send."""

    status = 422


class SpeechDisabled(SpeechError):
    status = 503


class InvalidAudio(SpeechError):
    status = 422


class AudioTooLong(SpeechError):
    status = 413


class Transcription(BaseModel):
    text: str
    language: str
    duration_seconds: float


ModelFactory = Callable[[Settings], Any]


def _load_whisper(settings: Settings) -> Any:
    # Imported here: ctranslate2 is heavy, and nothing else needs it loaded.
    from faster_whisper import WhisperModel

    return WhisperModel(
        settings.speech_model,
        device=settings.speech_device,
        compute_type=settings.speech_compute_type,
    )


def _decode(audio: bytes) -> Any:
    from faster_whisper import decode_audio

    return decode_audio(io.BytesIO(audio), sampling_rate=_SAMPLE_RATE)


class SpeechService:
    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: ModelFactory = _load_whisper,
        decoder: Callable[[bytes], Any] = _decode,
    ) -> None:
        self.settings = settings
        self._factory = model_factory
        self._decode = decoder
        self._model: Any = None
        # The model loads in a worker thread; two first requests must not both
        # download and load it.
        self._load_lock = threading.Lock()
        self._slots = asyncio.Semaphore(settings.speech_concurrency)

    @property
    def enabled(self) -> bool:
        return self.settings.speech_enabled

    @property
    def ready(self) -> bool:
        return self._model is not None

    def _get_model(self) -> Any:
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    log.info("speech.model_loading", model=self.settings.speech_model)
                    self._model = self._factory(self.settings)
                    log.info("speech.model_ready", model=self.settings.speech_model)
        return self._model

    async def preload(self) -> None:
        """Load (and on first run, download) the model without blocking startup."""
        if not self.enabled:
            return
        try:
            await asyncio.to_thread(self._get_model)
        except Exception:  # noqa: BLE001 - dictation degrades; the API stays up
            log.exception("speech.preload_failed", model=self.settings.speech_model)

    async def transcribe(self, audio: bytes) -> Transcription:
        if not self.enabled:
            raise SpeechDisabled("Dictation is turned off on this server (SPEECH_ENABLED).")
        if not audio:
            raise InvalidAudio("The recording is empty.")
        async with self._slots:
            return await asyncio.to_thread(self._transcribe, audio)

    def _transcribe(self, audio: bytes) -> Transcription:
        try:
            samples = self._decode(audio)
        except Exception as exc:  # noqa: BLE001 - any decoder failure is bad input
            raise InvalidAudio("That recording could not be decoded.") from exc

        duration = len(samples) / _SAMPLE_RATE
        # Measured before transcribing, so an over-long upload costs a decode,
        # not minutes of CPU.
        if duration > self.settings.speech_max_seconds:
            raise AudioTooLong(
                f"Recordings are limited to {self.settings.speech_max_seconds} seconds."
            )

        segments, info = self._get_model().transcribe(
            samples,
            language=self.settings.speech_language,
            beam_size=self.settings.speech_beam_size,
            # Skip silence before, after and between phrases: faster, and stops
            # Whisper inventing words ("Thank you.") to fill quiet stretches.
            vad_filter=True,
        )
        # `segments` is lazy: iterating it is where the work happens.
        text = " ".join(segment.text.strip() for segment in segments).strip()

        log.info(
            "speech.transcribed",
            duration_seconds=round(duration, 2),
            characters=len(text),
            language=info.language,
        )
        return Transcription(
            text=text, language=info.language, duration_seconds=round(duration, 2)
        )
