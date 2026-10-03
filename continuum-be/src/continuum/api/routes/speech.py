"""Dictation: audio in, text out.

The browser records with MediaRecorder (webm/opus in Chrome and Brave, ogg or
mp4 elsewhere) and uploads the clip here once the user stops. The text comes
back into the chat box for the user to read and edit — it is never sent as a
message, or remembered, on its own.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from continuum.api.deps import (
    CurrentUser,
    SettingsDep,
    SpeechDep,
    VoiceDep,
    limit_llm_requests,
    limit_tts_requests,
)
from continuum.models.schemas import SpeechStatus, SynthesisRequest
from continuum.services.speech import SpeechError, Transcription

router = APIRouter(prefix="/speech", tags=["speech"])


@router.get("/status", response_model=SpeechStatus)
async def speech_status(
    principal: CurrentUser, speech: SpeechDep, voice: VoiceDep, settings: SettingsDep
) -> SpeechStatus:
    """Whether to offer dictation, and its limits. `ready` is false while the
    model is still loading — dictation works then too, the first one is slower."""
    return SpeechStatus(
        enabled=speech.enabled,
        ready=speech.ready,
        max_seconds=settings.speech_max_seconds,
        language=settings.speech_language,
        synthesis=voice.enabled,
        synthesis_ready=voice.ready,
        synthesis_max_chars=settings.tts_max_chars,
    )


@router.post(
    "/transcribe", response_model=Transcription, dependencies=[Depends(limit_llm_requests)]
)
async def transcribe(
    principal: CurrentUser,
    speech: SpeechDep,
    settings: SettingsDep,
    audio: UploadFile = File(..., description="The recording: webm, ogg, mp4, wav, …"),
) -> Transcription:
    """Transcribe one recording. Nothing is stored — not the audio, not the text."""
    # Read one byte past the cap rather than trusting Content-Length.
    data = await audio.read(settings.speech_max_bytes + 1)
    if len(data) > settings.speech_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Recordings are limited to {settings.speech_max_bytes // (1024 * 1024)} MB.",
        )
    try:
        return await speech.transcribe(data)
    except SpeechError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post(
    "/synthesize",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}}},
    dependencies=[Depends(limit_tts_requests)],
)
async def synthesize(body: SynthesisRequest, principal: CurrentUser, voice: VoiceDep) -> Response:
    """Speak `text` with the server's local voice. Returns WAV; nothing is stored."""
    try:
        audio = await voice.synthesize(body.text, voice=body.voice, speed=body.speed)
    except SpeechError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    # Answers are personal: never let a proxy or the browser cache keep them.
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
