"""Text to speech for read-aloud and autopilot, with a local Piper voice.

Why the server and not the browser's speechSynthesis: in the browser, speech
depends on the operating system's speech service (speech-dispatcher on Linux,
frequently not running) and on browser policy (Brave restricts voices to resist
fingerprinting). Both fail silently — a "Read aloud" button that does nothing,
or never says it has finished. Piper gives every browser the same voice, offline,
at ~20x real time on a CPU.

Like dictation, nothing is stored: the text is synthesised in memory and the
audio returned. Logs record lengths, not words.
"""

from __future__ import annotations

import asyncio
import io
import threading
import wave
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from continuum.config import Settings
from continuum.core.logger import get_logger
from continuum.services.speech import InvalidAudio, SpeechDisabled, SpeechError

log = get_logger(__name__)


class TextTooLong(SpeechError):
    status = 413


class InvalidText(InvalidAudio):
    """Nothing speakable in the request."""


VoiceLoader = Callable[[str], Any]


class VoiceInfo(BaseModel):
    id: str
    label: str
    accent: str
    gender: str


#: The voices people can choose in Settings. Single-speaker Piper voices only,
#: each checked to exist on huggingface.co/rhasspy/piper-voices. A fixed list,
#: not "any name": the API must never download whatever a request names.
#: Labels are the dataset names; how each sounds is for the listener to judge
#: (Settings has a "Hear it" button).
VOICE_CATALOGUE: tuple[VoiceInfo, ...] = (
    VoiceInfo(id="en_US-lessac-medium", label="Lessac", accent="American", gender="female"),
    VoiceInfo(id="en_US-amy-medium", label="Amy", accent="American", gender="female"),
    VoiceInfo(id="en_US-kristin-medium", label="Kristin", accent="American", gender="female"),
    VoiceInfo(id="en_US-hfc_female-medium", label="HFC", accent="American", gender="female"),
    VoiceInfo(id="en_US-ryan-medium", label="Ryan", accent="American", gender="male"),
    VoiceInfo(id="en_US-joe-medium", label="Joe", accent="American", gender="male"),
    VoiceInfo(id="en_US-john-medium", label="John", accent="American", gender="male"),
    VoiceInfo(id="en_GB-alba-medium", label="Alba", accent="British (Scottish)", gender="female"),
    VoiceInfo(id="en_GB-jenny_dioco-medium", label="Jenny", accent="British", gender="female"),
    VoiceInfo(id="en_GB-cori-medium", label="Cori", accent="British", gender="female"),
    VoiceInfo(id="en_GB-alan-medium", label="Alan", accent="British", gender="male"),
    VoiceInfo(
        id="en_GB-northern_english_male-medium",
        label="Northern English",
        accent="British (northern)",
        gender="male",
    ),
)

# Speed as a person sets it (1.0 = natural). Piper takes its inverse, the
# "length scale". Outside this range speech gets hard to follow.
MIN_SPEED, MAX_SPEED = 0.6, 1.8


class UnknownVoice(SpeechError):
    status = 422


def voice_repo_path(voice: str) -> str:
    """'en_US-lessac-medium' -> 'en/en_US/lessac/medium/en_US-lessac-medium'.

    The layout of huggingface.co/rhasspy/piper-voices.
    """
    parts = voice.split("-")
    if len(parts) < 3 or "_" not in parts[0]:
        raise ValueError(f"Not a Piper voice name (lang_REGION-name-quality): {voice!r}")
    lang, quality, name = parts[0], parts[-1], "-".join(parts[1:-1])
    return f"{lang.split('_')[0]}/{lang}/{name}/{quality}/{voice}"


def _load_piper(voice: str) -> Any:
    from huggingface_hub import hf_hub_download
    from piper import PiperVoice

    base = voice_repo_path(voice)
    model = hf_hub_download("rhasspy/piper-voices", f"{base}.onnx")
    hf_hub_download("rhasspy/piper-voices", f"{base}.onnx.json")  # beside the model
    return PiperVoice.load(model)


class SynthesisService:
    def __init__(self, settings: Settings, *, voice_loader: VoiceLoader = _load_piper) -> None:
        self.settings = settings
        self._loader = voice_loader
        # Loaded voices, least recently used first.
        self._voices: OrderedDict[str, Any] = OrderedDict()
        self._load_lock = threading.Lock()
        # Synthesis is quick and mostly single-threaded; two at once keeps a
        # streamed answer's next sentence ready while the current one plays.
        self._slots = asyncio.Semaphore(2)

    @property
    def enabled(self) -> bool:
        return self.settings.tts_enabled

    @property
    def default_voice(self) -> str:
        return self.settings.tts_voice

    @property
    def ready(self) -> bool:
        return self.default_voice in self._voices

    def catalogue(self) -> list[VoiceInfo]:
        """What Settings offers: the fixed list, plus the server default if it
        was configured to something outside it."""
        voices = list(VOICE_CATALOGUE)
        if all(v.id != self.default_voice for v in voices):
            voices.insert(0, VoiceInfo(id=self.default_voice, label=self.default_voice,
                                       accent="server default", gender=""))
        return voices

    def is_allowed(self, voice: str) -> bool:
        return any(v.id == voice for v in self.catalogue())

    def _get_voice(self, voice: str) -> Any:
        with self._load_lock:
            if voice in self._voices:
                self._voices.move_to_end(voice)
                return self._voices[voice]
            log.info("voice.loading", voice=voice)
            loaded = self._loader(voice)
            self._voices[voice] = loaded
            while len(self._voices) > max(1, self.settings.tts_max_loaded_voices):
                dropped, _ = self._voices.popitem(last=False)
                log.info("voice.unloaded", voice=dropped)
            log.info("voice.ready", voice=voice)
            return loaded

    async def preload(self) -> None:
        if not self.enabled:
            return
        try:
            await asyncio.to_thread(self._get_voice, self.default_voice)
        except Exception:  # noqa: BLE001 - read-aloud degrades; the API stays up
            log.exception("voice.preload_failed", voice=self.default_voice)

    async def synthesize(
        self, text: str, *, voice: str | None = None, speed: float | None = None
    ) -> bytes:
        """Return WAV audio (16-bit mono PCM) for `text`."""
        if not self.enabled:
            raise SpeechDisabled("Spoken answers are turned off on this server (TTS_ENABLED).")
        voice = voice or self.default_voice
        if not self.is_allowed(voice):
            raise UnknownVoice(f"Unknown voice {voice!r}. Choose one listed in Settings.")
        speed = min(MAX_SPEED, max(MIN_SPEED, speed)) if speed else 1.0
        text = " ".join(text.split())
        if not any(ch.isalnum() for ch in text):
            raise InvalidText("There is nothing to say.")
        if len(text) > self.settings.tts_max_chars:
            raise TextTooLong(
                f"Send at most {self.settings.tts_max_chars} characters per request."
            )
        async with self._slots:
            return await asyncio.to_thread(self._synthesize, text, voice, speed)

    def _synthesize(self, text: str, voice: str, speed: float) -> bytes:
        from piper.config import SynthesisConfig

        model = self._get_voice(voice)
        # The server's base pace scaled by the person's speed (faster = shorter).
        length_scale = self.settings.tts_length_scale / speed
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            model.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=length_scale))
        audio = buffer.getvalue()
        log.info("voice.synthesized", voice=voice, characters=len(text), bytes=len(audio))
        return audio
