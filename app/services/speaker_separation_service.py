"""Split WhatsApp-style .ogg/.opus by speaker (diarization) for voice cloning."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEAKER_JOBS_DIR = PROJECT_ROOT / "data" / "speaker_jobs"
PREVIEW_MAX_SECONDS = 45.0
ALLOWED_INPUT_EXTENSIONS = {".ogg", ".opus"}

# Gated models — you must accept terms on each page while logged into HF.
PYANNOTE_GATED_MODELS = (
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
)

HF_ACCESS_HELP = """
Hugging Face blocked model download (gated repo). Fix:

1. Log in at huggingface.co and open each link — click "Agree and access":
   • https://huggingface.co/pyannote/speaker-diarization-3.1
   • https://huggingface.co/pyannote/segmentation-3.0

2. Create or fix your token at https://huggingface.co/settings/tokens
   • Classic token: type "Read" is enough, OR
   • Fine-grained token: enable "Read access to contents of all public gated
     repos you can access" (required — without this you get 403 Forbidden)

3. Put the token in .env as HF_TOKEN=hf_...

4. Restart uvicorn.
""".strip()


def ensure_speaker_dirs() -> None:
    SPEAKER_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_video_dirs() -> None:
    """Backward-compatible alias."""
    ensure_speaker_dirs()


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _job_dir(job_id: str) -> Path:
    return SPEAKER_JOBS_DIR / job_id


def _meta_path(job_id: str) -> Path:
    return _job_dir(job_id) / "meta.json"


def _load_meta(job_id: str) -> dict[str, Any]:
    path = _meta_path(job_id)
    if not path.is_file():
        raise FileNotFoundError(f"Job not found: {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_meta(job_id: str, meta: dict[str, Any]) -> None:
    path = _meta_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _run_ffmpeg(args: list[str]) -> None:
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is required. Install ffmpeg and add it to PATH.")
    result = subprocess.run(
        ["ffmpeg", "-y", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[-800:]
        raise RuntimeError(f"ffmpeg failed: {stderr or 'unknown error'}")


def _extract_wav_from_audio(audio_path: Path, wav_path: Path) -> None:
    _run_ffmpeg(
        [
            "-i",
            str(audio_path),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
        ]
    )


def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    _run_ffmpeg(
        [
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "2",
            str(mp3_path),
        ]
    )


def _concat_wav_segments(
    source_wav: Path, segments: list[tuple[float, float]], out_wav: Path
) -> float:
    """Concatenate time ranges; returns total duration exported."""
    if not segments:
        raise ValueError("No speech segments for this speaker.")

    job_dir = out_wav.parent
    part_files: list[Path] = []
    total = 0.0

    for idx, (start, end) in enumerate(segments):
        if end <= start:
            continue
        part = job_dir / f"_part_{out_wav.stem}_{idx}.wav"
        _run_ffmpeg(
            [
                "-i",
                str(source_wav),
                "-ss",
                f"{start:.3f}",
                "-to",
                f"{end:.3f}",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "44100",
                "-ac",
                "1",
                str(part),
            ]
        )
        part_files.append(part)
        total += end - start

    if not part_files:
        raise ValueError("No valid segments to concatenate.")

    if len(part_files) == 1:
        shutil.move(str(part_files[0]), str(out_wav))
        return total

    list_file = job_dir / f"_concat_{out_wav.stem}.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in part_files),
        encoding="utf-8",
    )
    _run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(out_wav),
        ]
    )

    for p in part_files:
        p.unlink(missing_ok=True)
    list_file.unlink(missing_ok=True)
    return total


def _segments_for_preview(
    segments: list[tuple[float, float]], max_seconds: float
) -> list[tuple[float, float]]:
    picked: list[tuple[float, float]] = []
    total = 0.0
    for start, end in segments:
        duration = end - start
        if duration <= 0:
            continue
        if total + duration > max_seconds:
            remaining = max_seconds - total
            if remaining > 0.3:
                picked.append((start, start + remaining))
            break
        picked.append((start, end))
        total += duration
    return picked or segments[:1]


def _iter_exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_hf_gated_error(exc: BaseException) -> bool:
    for err in _iter_exception_chain(exc):
        msg = str(err).lower()
        if any(
            x in msg
            for x in (
                "403",
                "forbidden",
                "gated",
                "localentrynotfound",
                "cannot access",
                "public gated repositories",
            )
        ):
            return True
    return False


def _raise_hf_access_error(exc: BaseException) -> None:
    if _is_hf_gated_error(exc):
        raise ValueError(f"{HF_ACCESS_HELP}\n\nDetails: {exc}") from exc
    raise exc


_HF_PROBE_TIMEOUT = 12.0


def _hf_auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.HF_TOKEN}"}


def _hf_can_download_gated_files() -> bool:
    """Lightweight probe — no hub cache writes (App Engine only allows /tmp)."""
    if not settings.HF_TOKEN:
        return False
    import httpx

    model_id = settings.PYANNOTE_DIARIZATION_MODEL
    url = f"https://huggingface.co/{model_id}/resolve/main/config.yaml"
    try:
        with httpx.Client(timeout=_HF_PROBE_TIMEOUT, follow_redirects=True) as client:
            resp = client.head(url, headers=_hf_auth_headers())
            if resp.status_code == 405:
                resp = client.get(url, headers=_hf_auth_headers())
            return resp.status_code == 200
    except Exception:
        return False


def _hf_model_metadata_visible(model_id: str) -> tuple[bool, str | None]:
    import httpx

    url = f"https://huggingface.co/api/models/{model_id}"
    try:
        with httpx.Client(timeout=_HF_PROBE_TIMEOUT) as client:
            resp = client.get(url, headers=_hf_auth_headers())
            if resp.status_code == 200:
                return True, None
            return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


def check_hf_gated_access() -> dict[str, Any]:
    """Verify HF token can access pyannote gated models (HTTP probe, safe on App Engine)."""
    backend = settings.DIARIZATION_BACKEND
    if backend in ("local", "resemblyzer"):
        return {
            "ok": True,
            "skipped": True,
            "diarization_backend": backend,
            "message": (
                "This server uses local speaker separation. "
                "Pyannote/HF download check is not required here."
            ),
            "can_download_files": False,
            "pyannote_on_server": False,
            "models": [],
            "hf_token_set": bool(settings.HF_TOKEN),
        }

    try:
        if not settings.HF_TOKEN:
            return {
                "ok": False,
                "error": "HF_TOKEN is not set",
                "models": [],
                "can_download_files": False,
                "help": HF_ACCESS_HELP,
                "fallback": "local",
            }

        can_download = _hf_can_download_gated_files()
        model_results: list[dict[str, Any]] = []

        for model_id in PYANNOTE_GATED_MODELS:
            visible, err = _hf_model_metadata_visible(model_id)
            entry: dict[str, Any] = {
                "model": model_id,
                "metadata_visible": visible,
                "file_download_ok": can_download
                and model_id == settings.PYANNOTE_DIARIZATION_MODEL,
            }
            if err:
                entry["error"] = err
            if not visible:
                entry["hint"] = (
                    "Accept model terms while logged in on huggingface.co"
                )
            elif not can_download:
                entry["hint"] = (
                    "Metadata OK but downloads blocked — use a classic Read token OR "
                    "enable 'Read access to all public gated repos' on fine-grained token"
                )
            model_results.append(entry)

        return {
            "ok": can_download,
            "can_download_files": can_download,
            "diarization_backend": backend,
            "models": model_results,
            "help": None if can_download else HF_ACCESS_HELP,
            "fallback": None if can_download else "local clustering (used automatically)",
        }
    except Exception as exc:
        logger.exception("HF access check failed")
        return {
            "ok": False,
            "error": str(exc),
            "models": [],
            "can_download_files": False,
            "help": HF_ACCESS_HELP,
            "fallback": "local",
        }


_pyannote_pipeline = None


def _load_wav_tensor(wav_path: Path, *, target_sr: int = 16_000):
    """
    Load PCM WAV without torchaudio.load (torchaudio 2.9+ requires broken torchcodec on Windows).
    """
    import numpy as np
    import torch
    from scipy.io import wavfile

    sample_rate, data = wavfile.read(str(wav_path))

    if np.issubdtype(data.dtype, np.integer):
        max_val = float(np.iinfo(data.dtype).max)
        samples = data.astype(np.float32) / max_val
    else:
        samples = data.astype(np.float32)

    if samples.ndim == 1:
        waveform = torch.from_numpy(samples).unsqueeze(0)
    else:
        waveform = torch.from_numpy(samples.T)
        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)

    if sample_rate != target_sr:
        from torchaudio.functional import resample

        waveform = resample(waveform, sample_rate, target_sr)
        sample_rate = target_sr

    return waveform, sample_rate


def _load_audio_for_pyannote(wav_path: Path) -> dict[str, Any]:
    """Load mono 16 kHz tensor for pyannote (no torchcodec)."""
    waveform, sample_rate = _load_wav_tensor(wav_path)
    return {"waveform": waveform, "sample_rate": sample_rate}


def _get_pyannote_pipeline():
    """Load and cache the pyannote diarization pipeline."""
    global _pyannote_pipeline
    if _pyannote_pipeline is not None:
        return _pyannote_pipeline

    if not settings.HF_TOKEN:
        raise ValueError(f"HF_TOKEN is not set in .env.\n\n{HF_ACCESS_HELP}")

    if not _hf_can_download_gated_files():
        raise ValueError(HF_ACCESS_HELP)

    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeError(
            "pyannote.audio is not installed. Run: "
            "pip install pyannote.audio torch torchaudio"
        ) from exc

    model_id = settings.PYANNOTE_DIARIZATION_MODEL
    logger.info("Loading pyannote pipeline %s…", model_id)
    try:
        _pyannote_pipeline = Pipeline.from_pretrained(
            model_id, token=settings.HF_TOKEN
        )
    except Exception as exc:
        _raise_hf_access_error(exc)
    return _pyannote_pipeline


def _annotation_from_diarization(diarization: Any) -> Any:
    """pyannote 4.x returns DiarizeOutput; 3.x returns Annotation directly."""
    if hasattr(diarization, "exclusive_speaker_diarization"):
        return diarization.exclusive_speaker_diarization
    if hasattr(diarization, "speaker_diarization"):
        return diarization.speaker_diarization
    return diarization


def _segments_from_diarization(diarization) -> dict[str, list[tuple[float, float]]]:
    annotation = _annotation_from_diarization(diarization)
    by_speaker: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for segment, _, speaker in annotation.itertracks(yield_label=True):
        by_speaker[speaker].append((segment.start, segment.end))

    if not by_speaker:
        raise ValueError(
            "No speakers detected. Try a clearer recording with speech."
        )

    durations = {
        spk: sum(end - start for start, end in segs)
        for spk, segs in by_speaker.items()
    }
    top_two = sorted(durations, key=durations.get, reverse=True)[:2]
    return {spk: by_speaker[spk] for spk in top_two}


def _diarize_pyannote(wav_path: Path) -> dict[str, list[tuple[float, float]]]:
    pipeline = _get_pyannote_pipeline()
    audio = _load_audio_for_pyannote(wav_path)

    logger.info("Running pyannote diarization on %s", wav_path)
    diarization = pipeline(audio, num_speakers=2)
    return _segments_from_diarization(diarization)


def _diarize_local(wav_path: Path) -> dict[str, list[tuple[float, float]]]:
    """Local window clustering — no Hugging Face gated downloads."""
    try:
        import numpy as np
        import torch
        from sklearn.cluster import AgglomerativeClustering
    except ImportError as exc:
        raise RuntimeError(
            "Local diarization needs scikit-learn. Run: pip install scikit-learn"
        ) from exc

    logger.info("Running local speaker separation on %s", wav_path)
    waveform, sample_rate = _load_wav_tensor(wav_path)
    wav = waveform.squeeze().numpy()

    window_s, hop_s, energy_floor = 1.5, 0.75, 0.008
    win_samples = int(window_s * sample_rate)
    hop_samples = int(hop_s * sample_rate)
    from torchaudio.transforms import MelSpectrogram

    mel_transform = MelSpectrogram(sample_rate=sample_rate, n_mels=40)

    features: list = []
    spans: list[tuple[float, float]] = []
    for start in range(0, max(1, len(wav) - win_samples), hop_samples):
        chunk = wav[start : start + win_samples]
        if chunk.size < win_samples:
            chunk = np.pad(chunk, (0, win_samples - chunk.size))
        energy = float(np.sqrt(np.mean(chunk**2) + 1e-12))
        if energy < energy_floor:
            continue
        tensor = torch.from_numpy(chunk).float().unsqueeze(0)
        mel = mel_transform(tensor).clamp(min=1e-10).log()
        features.append(mel.mean(dim=-1).squeeze().numpy())
        spans.append((start / sample_rate, (start + win_samples) / sample_rate))

    if len(features) < 2:
        raise ValueError("Audio too short or too quiet to separate two speakers.")

    labels = AgglomerativeClustering(n_clusters=2).fit_predict(np.stack(features))

    by_label: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for label, span in zip(labels, spans):
        by_label[int(label)].append(span)

    if len(by_label) < 2:
        raise ValueError("Could not find two distinct speakers in this recording.")

    return {
        f"SPEAKER_{label:02d}": segments
        for label, segments in sorted(by_label.items())
    }


def _diarize(wav_path: Path) -> tuple[dict[str, list[tuple[float, float]]], str]:
    backend = settings.DIARIZATION_BACKEND

    if backend in ("local", "resemblyzer"):
        return _diarize_local(wav_path), "local"

    if backend in ("pyannote", "auto"):
        try:
            return _diarize_pyannote(wav_path), "pyannote"
        except Exception as exc:
            if backend == "pyannote":
                raise
            if not _is_hf_gated_error(exc):
                logger.warning("pyannote failed (%s), using local separation", exc)
            else:
                logger.warning(
                    "pyannote unavailable (%s), using local separation", exc
                )

    logger.info("Using local diarization fallback")
    return _diarize_local(wav_path), "local"


def _audio_channels(audio_path: Path) -> int:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=channels",
            "-of",
            "csv=p=0",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return 1
    try:
        return int(probe.stdout.strip() or "1")
    except ValueError:
        return 1


def _try_stereo_split(audio_path: Path, job_dir: Path) -> tuple[Path, Path] | None:
    """Fallback when each stereo channel is a separate speaker (rare)."""
    if _audio_channels(audio_path) < 2:
        return None

    stereo_wav = job_dir / "audio_stereo.wav"
    _run_ffmpeg(
        [
            "-i",
            str(audio_path),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(stereo_wav),
        ]
    )

    left = job_dir / "_stereo_left.wav"
    right = job_dir / "_stereo_right.wav"
    _run_ffmpeg(["-i", str(stereo_wav), "-map_channel", "0.0.0", str(left)])
    _run_ffmpeg(["-i", str(stereo_wav), "-map_channel", "0.0.1", str(right)])
    if not left.is_file() or not right.is_file():
        return None
    return left, right


def _build_speaker_outputs(
    job_id: str,
    job_dir: Path,
    source_wav: Path,
    speaker_segments: dict[str, list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    speakers_out: list[dict[str, Any]] = []
    for idx, (label, segments) in enumerate(
        sorted(speaker_segments.items()), start=1
    ):
        speaker_id = f"speaker_{idx}"
        full_wav = job_dir / f"{speaker_id}_full.wav"
        preview_wav = job_dir / f"{speaker_id}_preview.wav"
        full_mp3 = job_dir / f"{speaker_id}_full.mp3"
        preview_mp3 = job_dir / f"{speaker_id}_preview.mp3"

        preview_segs = _segments_for_preview(segments, PREVIEW_MAX_SECONDS)
        full_duration = _concat_wav_segments(source_wav, segments, full_wav)
        preview_duration = _concat_wav_segments(
            source_wav, preview_segs, preview_wav
        )
        _wav_to_mp3(full_wav, full_mp3)
        _wav_to_mp3(preview_wav, preview_mp3)

        speakers_out.append(
            {
                "id": speaker_id,
                "label": f"Speaker {idx}",
                "diarization_label": label,
                "speech_seconds": round(full_duration, 1),
                "preview_seconds": round(preview_duration, 1),
                "preview_url": f"/voice/speakers/jobs/{job_id}/preview/{speaker_id}",
                "full_url": f"/voice/speakers/jobs/{job_id}/full/{speaker_id}",
            }
        )
    return speakers_out


def _build_speaker_outputs_from_files(
    job_id: str, job_dir: Path, channel_wavs: list[Path]
) -> list[dict[str, Any]]:
    speakers_out: list[dict[str, Any]] = []
    for idx, wav_file in enumerate(channel_wavs, start=1):
        speaker_id = f"speaker_{idx}"
        full_wav = job_dir / f"{speaker_id}_full.wav"
        preview_wav = job_dir / f"{speaker_id}_preview.wav"
        full_mp3 = job_dir / f"{speaker_id}_full.mp3"
        preview_mp3 = job_dir / f"{speaker_id}_preview.mp3"

        shutil.copy(wav_file, full_wav)
        duration_probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(full_wav),
            ],
            capture_output=True,
            text=True,
        )
        duration = float(duration_probe.stdout.strip() or "0")
        preview_end = min(duration, PREVIEW_MAX_SECONDS)
        _run_ffmpeg(
            [
                "-i",
                str(full_wav),
                "-t",
                f"{preview_end:.3f}",
                str(preview_wav),
            ]
        )
        _wav_to_mp3(full_wav, full_mp3)
        _wav_to_mp3(preview_wav, preview_mp3)

        speakers_out.append(
            {
                "id": speaker_id,
                "label": f"Speaker {idx}",
                "diarization_label": f"channel_{idx}",
                "speech_seconds": round(duration, 1),
                "preview_seconds": round(preview_end, 1),
                "preview_url": f"/voice/speakers/jobs/{job_id}/preview/{speaker_id}",
                "full_url": f"/voice/speakers/jobs/{job_id}/full/{speaker_id}",
            }
        )
    return speakers_out


def process_ogg(filename: str, content: bytes) -> dict[str, Any]:
    """Diarize a WhatsApp voice note (.ogg or .opus) into two speaker tracks."""
    ensure_speaker_dirs()
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_INPUT_EXTENSIONS:
        raise ValueError(
            f"Only {', '.join(sorted(ALLOWED_INPUT_EXTENSIONS))} files are supported."
        )

    job_id = str(uuid.uuid4())
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path = job_dir / f"input{ext}"
    wav_path = job_dir / "audio_16k.wav"
    input_path.write_bytes(content)

    meta: dict[str, Any] = {
        "job_id": job_id,
        "status": "processing",
        "source_filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "speakers": [],
        "error": None,
    }
    _save_meta(job_id, meta)

    try:
        _extract_wav_from_audio(input_path, wav_path)

        try:
            speaker_segments, diarization_backend = _diarize(wav_path)
            meta["diarization_backend"] = diarization_backend
            if diarization_backend == "pyannote":
                meta["diarization_note"] = "Separated with pyannote speaker diarization."
            elif diarization_backend == "local":
                meta["diarization_note"] = (
                    "Used local fallback. Set DIARIZATION_BACKEND=pyannote in .env "
                    "for pyannote (requires HF gated model access)."
                )
            speakers_out = _build_speaker_outputs(
                job_id, job_dir, wav_path, speaker_segments
            )
        except (RuntimeError, ValueError) as diarize_err:
            stereo = _try_stereo_split(input_path, job_dir)
            if stereo:
                speakers_out = _build_speaker_outputs_from_files(
                    job_id, job_dir, list(stereo)
                )
                meta["diarization_note"] = "Used stereo channel split (fallback)."
            else:
                raise diarize_err

        meta["status"] = "ready"
        meta["speakers"] = speakers_out
        _save_meta(job_id, meta)
        return meta

    except Exception as exc:
        logger.exception("Speaker separation failed for job %s", job_id)
        meta["status"] = "failed"
        meta["error"] = str(exc)
        _save_meta(job_id, meta)
        raise


def get_job(job_id: str) -> dict[str, Any]:
    return _load_meta(job_id)


def get_speaker_audio_path(
    job_id: str, speaker_id: str, *, preview: bool
) -> Path:
    suffix = "preview" if preview else "full"
    path = _job_dir(job_id) / f"{speaker_id}_{suffix}.mp3"
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found for {speaker_id}")
    return path


def import_speaker_to_voice_samples(job_id: str, speaker_id: str) -> dict[str, Any]:
    from app.services.voice_clone_service import save_sample

    meta = _load_meta(job_id)
    if meta.get("status") != "ready":
        raise ValueError("Job is not ready yet.")

    valid_ids = {s["id"] for s in meta.get("speakers", [])}
    if speaker_id not in valid_ids:
        raise ValueError(f"Unknown speaker_id. Choose one of: {sorted(valid_ids)}")

    full_mp3 = get_speaker_audio_path(job_id, speaker_id, preview=False)
    content = full_mp3.read_bytes()
    saved = save_sample(f"{speaker_id}_{job_id[:8]}.mp3", content)

    meta["selected_speaker_id"] = speaker_id
    meta["imported_sample_id"] = saved["id"]
    _save_meta(job_id, meta)

    return {
        "job_id": job_id,
        "speaker_id": speaker_id,
        "sample": saved,
    }


def diarization_ready() -> bool:
    if not settings.HF_TOKEN:
        return False
    try:
        import pyannote.audio  # noqa: F401

        return True
    except ImportError:
        return False
