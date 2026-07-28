import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")

    # Gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    MODEL = "gemini-1.5-flash"

    # Notion
    NOTION_TOKEN = os.getenv("NOTION_TOKEN")
    TM_DAILY_DATABASE_ID = os.getenv("TM_DAILY_DATABASE_ID")
    RULES_POINT_DATABASE_ID = os.getenv("RULES_POINT_DATABASE_ID")

    # App Settings
    FOCUS_CHECK_IN_INTERVAL_MINUTES = 25
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
