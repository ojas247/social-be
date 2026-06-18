"""APIs for voice sample upload, cloning, TTS, and mother-voice chat."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agents.mother_agent import (
    ensure_session,
    new_session_id,
    run_mother_chat,
)
from app.services import voice_clone_service as voice
from app.services import speaker_separation_service as separation
from app.utils.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


@router.get("/platform")
def platform_info():
    """Product metadata, feature flags, and server configuration (read-only)."""
    return {
        "product": {
            "name": "Amma Voice",
            "tagline": "Preserve a voice. Continue the conversation.",
            "version": "1.0.0",
        },
        "features": [
            {
                "id": "voice_library",
                "name": "Voice Library",
                "description": "Upload and manage WhatsApp voice notes for cloning.",
            },
            {
                "id": "voice_clone",
                "name": "Instant Voice Clone",
                "description": "Create an ElevenLabs voice from your samples.",
            },
            {
                "id": "speaker_split",
                "name": "Speaker Separation",
                "description": "Split two-speaker recordings and pick the right voice.",
            },
            {
                "id": "conversations",
                "name": "AI Conversations",
                "description": "Chat with the Amma persona and hear replies in her voice.",
            },
        ],
        "settings": {
            "mother_reply_language": settings.MOTHER_REPLY_LANGUAGE,
            "diarization_backend": settings.DIARIZATION_BACKEND,
            "elevenlabs_model": settings.ELEVENLABS_MODEL_ID,
        },
    }


class CloneRequest(BaseModel):
    name: str = Field(default="mother", description="Label for the cloned voice")


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice_id: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    user_id: str = "user"
    session_id: str | None = None


class SelectSpeakerRequest(BaseModel):
    speaker_id: str = Field(..., description="speaker_1 or speaker_2")


@router.get("/status")
def voice_status():
    """Check samples, clone profile, and tooling (ffmpeg, API key)."""
    return voice.get_voice_status()


@router.get("/speakers/hf-check")
def check_huggingface_access():
    """Verify HF token can download pyannote gated models."""
    try:
        return separation.check_hf_gated_access()
    except Exception as exc:
        logger.exception("hf-check endpoint failed")
        return {
            "ok": False,
            "error": str(exc),
            "models": [],
            "can_download_files": False,
            "fallback": "local",
        }


@router.get("/samples")
def list_voice_samples():
    return {"samples": voice.list_samples()}


@router.post("/samples")
async def upload_voice_sample(file: UploadFile = File(...)):
    """Upload a WhatsApp voice note (.ogg/.opus) or other supported audio."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        saved = voice.save_sample(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"ok": True, "sample": saved}


@router.delete("/samples/{sample_id}")
def remove_voice_sample(sample_id: str):
    if not voice.delete_sample(sample_id):
        raise HTTPException(status_code=404, detail="Sample not found")
    return {"ok": True, "deleted_id": sample_id}


@router.post("/clone")
def clone_voice(body: CloneRequest | None = None):
    """
    Create an ElevenLabs instant voice clone from all uploaded samples.
    Needs ELEVENLABS_API_KEY in .env and roughly 30+ seconds of clear audio.
    """
    name = (body.name if body else None) or "mother"
    try:
        profile = voice.create_voice_clone(name=name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Voice clone failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, "profile": profile}


@router.get("/profile")
def get_voice_profile():
    profile = voice.load_profile()
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="No voice profile yet. Upload samples and POST /voice/clone.",
        )
    return profile


@router.post("/speak")
def speak_text(body: SpeakRequest):
    """Convert text to speech using the cloned voice."""
    try:
        out_path, file_id = voice.synthesize_speech(
            text=body.text,
            voice_id=body.voice_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("TTS failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "ok": True,
        "audio_id": file_id,
        "audio_url": f"/voice/audio/{file_id}",
        "filename": out_path.name,
    }


@router.get("/audio/{audio_id}")
def get_generated_audio(audio_id: str):
    path = voice.OUTPUT_DIR / f"{audio_id}.mp3"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@router.post("/chat")
async def chat_with_mother_voice(body: ChatRequest):
    """
    Chat with the mother persona agent and return her reply as cloned speech.
    """
    session_id = body.session_id or new_session_id()
    await ensure_session(body.user_id, session_id)

    try:
        reply_text = run_mother_chat(
            user_id=body.user_id,
            session_id=session_id,
            message=body.message,
        )
        out_path, audio_id = voice.synthesize_speech(text=reply_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Mother voice chat failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "session_id": session_id,
        "text": reply_text,
        "audio_id": audio_id,
        "audio_url": f"/voice/audio/{audio_id}",
    }


@router.post("/speakers/separate")
async def separate_speakers(file: UploadFile = File(...)):
    """
    Upload a WhatsApp .ogg/.opus voice note, diarize two speakers, return previews.
    Requires ffmpeg, HF_TOKEN, and pyannote.audio.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    ext = Path(file.filename).suffix.lower()
    if ext not in separation.ALLOWED_INPUT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Upload a single .ogg or .opus file",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        job = separation.process_ogg(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Speaker separation failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, "job": job}


@router.get("/speakers/jobs/{job_id}")
def get_speaker_job(job_id: str):
    try:
        return separation.get_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/speakers/jobs/{job_id}/preview/{speaker_id}")
def get_speaker_preview(job_id: str, speaker_id: str):
    try:
        path = separation.get_speaker_audio_path(
            job_id, speaker_id, preview=True
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@router.get("/speakers/jobs/{job_id}/full/{speaker_id}")
def get_speaker_full(job_id: str, speaker_id: str):
    try:
        path = separation.get_speaker_audio_path(
            job_id, speaker_id, preview=False
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@router.post("/speakers/jobs/{job_id}/select")
def select_speaker_for_cloning(job_id: str, body: SelectSpeakerRequest):
    """Import the chosen speaker track into voice samples for cloning."""
    try:
        result = separation.import_speaker_to_voice_samples(
            job_id, body.speaker_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"ok": True, **result}
