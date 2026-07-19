import os


class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    # Model Gemini
    MODEL = "gemini-3.5-flash"

    TELEGRAM_API = "https://api.telegram.org"

NOTION_TOKEN = os.getenv("NOTION_TOKEN")

TM_DAILY_DATABASE_ID = os.getenv("TM_DAILY_DATABASE_ID")

RULES_POINT_DATABASE_ID = os.getenv("RULES_POINT_DATABASE_ID")
