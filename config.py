"""
TM-Bot v2
Central Configuration
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ==========================================================
    # TELEGRAM
    # ==========================================================
    BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
    CHAT_ID = os.getenv("CHAT_ID", "").strip()

    # ==========================================================
    # GEMINI
    # ==========================================================
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL = "gemini-3.6-flash"  # ⚠️ GIỮ NGUYÊN MODEL

    # ==========================================================
    # NOTION
    # ==========================================================
    NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
    TM_DAILY_DATABASE_ID = os.getenv("TM_DAILY_DATABASE_ID", "").strip()
    RULES_POINT_DATABASE_ID = os.getenv("RULES_POINT_DATABASE_ID", "").strip()

    # ==========================================================
    # APP
    # ==========================================================
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    TIMEZONE = "Asia/Ho_Chi_Minh"
    HISTORY_LIMIT = 300

    # ==========================================================
    # DATA
    # ==========================================================
    HISTORY_FILE = "data/history.json"
    MEMORY_FILE = "data/memories.json"
    USER_PROFILE_FILE = "data/user-profile.md"
    STATE_FILE = "data/session_state.json"

    # ==========================================================
    # AUTOMATION — khớp đúng giờ cron cũ (06:07 / 13:14 / 22:07 GMT+7)
    # ==========================================================
    MORNING_HOUR = 6
    MORNING_MINUTE = 7
    AFTERNOON_HOUR = 13
    AFTERNOON_MINUTE = 14
    EVENING_HOUR = 22
    EVENING_MINUTE = 7

    # ==========================================================
    # GIỜ VÀNG — khớp đúng user-profile.md
    # ==========================================================
    WORKING_HOUR_RANGES = [(10, 12), (14, 17)]

    # ==========================================================
    # NGHI THỨC NGỦ — chỉ chạy full ritual sau giờ này
    # ==========================================================
    SLEEP_RITUAL_HOUR = 20

    # ==========================================================
    # STATE / ANTI-NAG
    # ==========================================================
    REMINDER_GAP_MINUTES = 60      # giãn cách nhắc task
    PUSH_LIMIT = 2                 # tối đa 2 lần Push liên tiếp -> ép Probe
    SMALL_TALK_LIMIT = 3
    WORKING_TIMEOUT_MINUTES = 90
    WORKING_TIMEOUT_CHECK_SECONDS = 900

    # ==========================================================
    # FOCUS MODE
    # ==========================================================
    FOCUS_DEFAULT_MINUTES = 25

    # ==========================================================
    # VALIDATION
    # ==========================================================
    @classmethod
    def validate(cls):
        required = {
            "BOT_TOKEN": cls.BOT_TOKEN,
            "CHAT_ID": cls.CHAT_ID,
            "GEMINI_API_KEY": cls.GEMINI_API_KEY,
            "NOTION_TOKEN": cls.NOTION_TOKEN,
            "TM_DAILY_DATABASE_ID": cls.TM_DAILY_DATABASE_ID,
        }
        missing = [k for k, v in required.items() if v == ""]
        if missing:
            print("\n==============================")
            print(" TM-Bot Configuration Error")
            print("==============================")
            for item in missing:
                print(f"❌ {item}")
            print("==============================\n")
            raise RuntimeError("Missing environment variables.")
