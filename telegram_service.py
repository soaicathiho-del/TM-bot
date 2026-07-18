import requests

from config import Config


def send_message(message: str) -> bool:
    """
    Gửi một tin nhắn Telegram.

    Returns:
        True nếu gửi thành công.
        False nếu thất bại.
    """

    url = f"{Config.TELEGRAM_API}/bot{Config.BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": Config.CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    response = requests.post(url, json=payload, timeout=30)

    if response.status_code != 200:
        raise Exception(
            f"Telegram Error {response.status_code}: {response.text}"
        )

    return True
