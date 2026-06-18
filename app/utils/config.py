import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Settings:
    ## TWITTER
    CONSUMER_KEY = os.getenv("CONSUMER_KEY")
    CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
    ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
    ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET")
    REQUEST_URL = os.getenv("REQUEST_URL")
    XUSERID = os.getenv("XUSERID")

    ## OPEN AI
    LLM_API_KEY = os.getenv("GEMINI_API_KEY")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_OJ_CHATID = os.getenv("TELEGRAM_OJ_CHATID")

    ## VOICE CLONE (ElevenLabs)
    ELEVENLABS_API_KEY = (os.getenv("ELEVENLABS_API_KEY") or "").strip().strip('"').strip("'")
    ELEVENLABS_MODEL_ID = os.getenv(
        "ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"
    )

    ## SPEAKER DIARIZATION (Hugging Face / pyannote)
    HF_TOKEN = os.getenv("HF_TOKEN")
    PYANNOTE_DIARIZATION_MODEL = os.getenv(
        "PYANNOTE_DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1"
    )
    # pyannote (default) | auto (pyannote if HF ok, else local) | local
    DIARIZATION_BACKEND = os.getenv("DIARIZATION_BACKEND", "pyannote").lower()

    # Mother agent reply language: auto (match user) | marathi | hindi | english
    MOTHER_REPLY_LANGUAGE = os.getenv("MOTHER_REPLY_LANGUAGE", "auto").lower()


settings = Settings()

