"""
TM-Bot v2
Central Configuration

⚠️ QUY TẮC

- Chỉ file này được phép đọc os.getenv()
- Toàn bộ project sẽ dùng Config.xxx
- Không được gọi os.getenv() ở bất kỳ file nào khác
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

    # ⚠️ GIỮ NGUYÊN MODEL
    GEMINI_MODEL = "gemini-3.6-flash"

    # ==========================================================
    # NOTION
    # ==========================================================

    NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()

    TM_DAILY_DATABASE_ID = os.getenv(
        "TM_DAILY_DATABASE_ID",
        ""
    ).strip()

    RULES_POINT_DATABASE_ID = os.getenv(
        "RULES_POINT_DATABASE_ID",
        ""
    ).strip()

    # ==========================================================
    # APP
    # ==========================================================

    DEBUG = os.getenv(
        "DEBUG",
        "False"
    ).lower() == "true"

    TIMEZONE = "Asia/Ho_Chi_Minh"

    HISTORY_LIMIT = 300

    # ==========================================================
    # DATA
    # ==========================================================

    DATA_FOLDER = "data"

    HISTORY_FILE = "data/history.json"

    MEMORY_FILE = "data/memories.json"

    USER_PROFILE_FILE = "data/user-profile.md"

    # ==========================================================
    # PROMPTS
    # ==========================================================

    PROMPT_FOLDER = "prompts"

    SYSTEM_PROMPT = "prompts/system/tm-core.md"

    ADAPTIVE_RULES = "prompts/system/tm-adaptive-rules.md"

    MORNING_PROMPT = "prompts/tasks/morning.md"

    FOCUS_PROMPT = "prompts/tasks/focus.md"

    SLEEP_PROMPT = "prompts/tasks/sleep.md"

    # ==========================================================
    # AUTOMATION
    # ==========================================================

    MORNING_HOUR = 7

    AFTERNOON_HOUR = 13

    EVENING_HOUR = 21

    FOCUS_CHECK_INTERVAL_MINUTES = 25

    REMINDER_INTERVAL_MINUTES = 60

    # ==========================================================
    # LOG
    # ==========================================================

    LOG_FOLDER = "logs"

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

        missing = []

        for key, value in required.items():
            if value == "":
                missing.append(key)

        if missing:

            print("\n==============================")
            print(" TM-Bot Configuration Error")
            print("==============================")

            for item in missing:
                print(f"❌ {item}")

            print("==============================\n")

            raise RuntimeError(
                "Missing environment variables."
            )
