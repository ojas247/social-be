"""Voice clone + mother chat API. Lean entrypoint for App Engine / production."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.routes.voice_routes import router as voice_router
from app.services.speaker_separation_service import ensure_speaker_dirs
from app.services.voice_clone_service import ensure_voice_dirs

logger = logging.getLogger(__name__)

app = FastAPI(title="Voice Clone")

ensure_voice_dirs()
ensure_speaker_dirs()
app.include_router(voice_router)

_cors_origins = os.getenv("CORS_ORIGINS", "").strip()
_cors_kw: dict = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if _cors_origins:
    _cors_kw["allow_origins"] = [
        o.strip() for o in _cors_origins.split(",") if o.strip()
    ]
else:
    _cors_kw["allow_origin_regex"] = r"https?://.*|http://localhost:\d+"

app.add_middleware(CORSMiddleware, **_cors_kw)


@app.get("/health")
def health():
    return {"status": "ok"}


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIR), html=True),
        name="frontend",
    )
