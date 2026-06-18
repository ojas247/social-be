"""Voice sample storage and ElevenLabs instant voice cloning."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.utils.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = PROJECT_ROOT / "data" / "voice_samples"
OUTPUT_DIR = PROJECT_ROOT / "data" / "voice_output"
PROFILE_PATH = PROJECT_ROOT / "data" / "voice_profile.json"

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
ALLOWED_EXTENSIONS = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".webm", ".flac"}


def ensure_voice_dirs() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _api_headers() -> dict[str, str]:
    if not settings.ELEVENLABS_API_KEY:
        raise ValueError(
            "ELEVENLABS_API_KEY is not set. Add it to your .env file."
        )
    return {"xi-api-key": settings.ELEVENLABS_API_KEY}


def _elevenlabs_error_message(response: httpx.Response) -> str:
    """Turn ElevenLabs HTTP errors into actionable messages."""
    try:
        payload = response.json()
        detail = payload.get("detail")
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("status") or ""
            if detail.get("status") == "missing_permissions":
                return (
                    f"ElevenLabs API key is missing permission: {message}\n\n"
                    "Fix: elevenlabs.io → Profile → API keys → create a key with "
                    "'Instant Voice Cloning' enabled (or use an unrestricted key)."
                )
            if message:
                return f"ElevenLabs API error: {message}"
        if isinstance(detail, str):
            return f"ElevenLabs API error: {detail}"
    except Exception:
        pass
    return f"ElevenLabs API error ({response.status_code}): {response.text[:200]}"


def _raise_for_elevenlabs(response: httpx.Response) -> None:
    if response.is_success:
        return
    raise ValueError(_elevenlabs_error_message(response))


def verify_elevenlabs_api() -> dict[str, Any]:
    """Check whether the configured key can create instant voice clones."""
    if not settings.ELEVENLABS_API_KEY:
        return {
            "ok": False,
            "configured": False,
            "can_clone": False,
            "message": "ELEVENLABS_API_KEY is not set in .env",
        }

    try:
        response = httpx.get(
            f"{ELEVENLABS_BASE}/user",
            headers=_api_headers(),
            timeout=30.0,
        )
        if response.status_code == 401:
            body = _elevenlabs_error_message(response)
            return {
                "ok": False,
                "configured": True,
                "can_clone": False,
                "message": body,
            }
        if response.status_code == 200:
            return {
                "ok": True,
                "configured": True,
                "can_clone": True,
                "message": "API key is valid for voice cloning.",
            }
        return {
            "ok": False,
            "configured": True,
            "can_clone": False,
            "message": _elevenlabs_error_message(response),
        }
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "can_clone": False,
            "message": str(exc),
        }


def list_samples() -> list[dict[str, Any]]:
    ensure_voice_dirs()
    samples: list[dict[str, Any]] = []
    for path in sorted(SAMPLES_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
            stat = path.stat()
            samples.append(
                {
                    "id": path.stem,
                    "filename": path.name,
                    "size_bytes": stat.st_size,
                    "uploaded_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
    return samples


def save_sample(filename: str, content: bytes) -> dict[str, Any]:
    ensure_voice_dirs()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported format '{ext}'. Use one of: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    sample_id = str(uuid.uuid4())
    dest = SAMPLES_DIR / f"{sample_id}{ext}"
    dest.write_bytes(content)

    prepared = _prepare_sample_for_clone(dest)
    return {
        "id": sample_id,
        "filename": dest.name,
        "prepared_path": prepared.name,
        "size_bytes": dest.stat().st_size,
    }


def delete_sample(sample_id: str) -> bool:
    ensure_voice_dirs()
    deleted = False
    for path in SAMPLES_DIR.glob(f"{sample_id}.*"):
        path.unlink(missing_ok=True)
        deleted = True
    return deleted


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _convert_to_mp3(source: Path) -> Path:
    target = source.with_suffix(".mp3")
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target

    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is required to convert WhatsApp .ogg/.opus files. "
            "Install ffmpeg and ensure it is on your PATH."
        )

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-ar",
            "44100",
            "-ac",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


def _prepare_sample_for_clone(path: Path) -> Path:
    if path.suffix.lower() in {".ogg", ".opus", ".webm"}:
        return _convert_to_mp3(path)
    return path


def load_profile() -> dict[str, Any] | None:
    ensure_voice_dirs()
    if not PROFILE_PATH.exists():
        return None
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    ensure_voice_dirs()
    PROFILE_PATH.write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )
    return profile


def create_voice_clone(name: str = "mother") -> dict[str, Any]:
    samples = list(SAMPLES_DIR.iterdir())
    audio_files = [
        p
        for p in samples
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
    ]
    if not audio_files:
        raise ValueError(
            "No voice samples found. Upload WhatsApp voice notes first via POST /voice/samples."
        )

    prepared_paths = [_prepare_sample_for_clone(p) for p in audio_files]

    files_payload = []
    for path in prepared_paths:
        files_payload.append(
            ("files", (path.name, path.read_bytes(), "audio/mpeg"))
        )

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{ELEVENLABS_BASE}/voices/add",
            headers=_api_headers(),
            data={"name": name, "description": "Cloned from WhatsApp voice notes"},
            files=files_payload,
        )
        _raise_for_elevenlabs(response)
        payload = response.json()

    profile = {
        "voice_id": payload.get("voice_id"),
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(audio_files),
        "provider": "elevenlabs",
    }
    save_profile(profile)
    return profile


def synthesize_speech(text: str, voice_id: str | None = None) -> tuple[Path, str]:
    profile = load_profile()
    resolved_voice_id = voice_id or (profile or {}).get("voice_id")
    if not resolved_voice_id:
        raise ValueError(
            "No cloned voice yet. Upload samples and call POST /voice/clone first."
        )

    model_id = settings.ELEVENLABS_MODEL_ID
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.85,
            "style": 0.35,
            "use_speaker_boost": True,
        },
    }

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{ELEVENLABS_BASE}/text-to-speech/{resolved_voice_id}",
            headers={**_api_headers(), "Accept": "audio/mpeg"},
            json=body,
        )
        _raise_for_elevenlabs(response)
        audio_bytes = response.content

    ensure_voice_dirs()
    file_id = str(uuid.uuid4())
    out_path = OUTPUT_DIR / f"{file_id}.mp3"
    out_path.write_bytes(audio_bytes)
    return out_path, file_id


def _diarization_status() -> dict[str, Any]:
    try:
        from app.services.speaker_separation_service import (
            check_hf_gated_access,
            diarization_ready,
        )

        pyannote_installed = diarization_ready()
        hf_access = check_hf_gated_access() if settings.HF_TOKEN else None
    except Exception:
        pyannote_installed = False
        hf_access = None

    return {
        "hf_token_set": bool(settings.HF_TOKEN),
        "pyannote_installed": pyannote_installed,
        "hf_gated_access": hf_access,
    }


def get_voice_status() -> dict[str, Any]:
    profile = load_profile()
    samples = list_samples()
    total_seconds_hint = None
    if samples:
        total_bytes = sum(s["size_bytes"] for s in samples)
        # Rough hint: ~16 KB/s for compressed voice notes
        total_seconds_hint = round(total_bytes / 16_000)

    elevenlabs_check = verify_elevenlabs_api()
    elevenlabs_ok = elevenlabs_check.get("can_clone", False)
    sample_count = len(samples)
    has_clone = profile is not None and bool(profile.get("voice_id"))

    if sample_count == 0:
        recommendation = (
            "Upload voice notes or pick a speaker from a two-person recording below."
        )
        clone_blocked_reason = "Add at least one voice sample first."
    elif not settings.ELEVENLABS_API_KEY:
        recommendation = (
            "Samples ready. Add ELEVENLABS_API_KEY to .env, restart the server, "
            "then create the clone."
        )
        clone_blocked_reason = "ElevenLabs API key missing in .env"
    elif not elevenlabs_ok:
        recommendation = elevenlabs_check.get("message", "Fix ElevenLabs API key permissions.")
        clone_blocked_reason = "ElevenLabs key lacks Instant Voice Cloning permission"
    elif has_clone:
        recommendation = "Clone exists. Re-create it or open Talk to Amma."
        clone_blocked_reason = None
    else:
        recommendation = "Ready — create the voice clone below."
        clone_blocked_reason = None

    return {
        "has_clone": has_clone,
        "profile": profile,
        "sample_count": sample_count,
        "samples": samples,
        "estimated_audio_seconds": total_seconds_hint,
        "elevenlabs_configured": bool(settings.ELEVENLABS_API_KEY),
        "elevenlabs_verify": elevenlabs_check,
        "can_clone": sample_count > 0,
        "clone_ready": sample_count > 0 and elevenlabs_ok,
        "clone_blocked_reason": clone_blocked_reason,
        "ffmpeg_available": _ffmpeg_available(),
        "diarization_ready": _diarization_status(),
        "recommendation": recommendation,
    }
