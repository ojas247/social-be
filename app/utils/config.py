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


settings = Settings()

