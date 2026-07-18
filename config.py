import os


class Config:
    """
    Đọc toàn bộ cấu hình từ GitHub Secrets
    """

    BOT_TOKEN = os.getenv("BOT_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    # Model Gemini (chỉ sửa dòng này nếu Google đổi model)
    MODEL = "gemini-3.5-flash"

    # Telegram
    TELEGRAM_API = "https://api.telegram.org"

    # Đường dẫn dữ liệu
    PROMPT_FILE = "prompt.txt"
    HISTORY_FILE = "data/history.json"
