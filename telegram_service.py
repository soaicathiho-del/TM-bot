import requests
from config import BOT_TOKEN

class TelegramService:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    def send_message(self, chat_id, text):
        url = f"{self.base_url}/sendMessage"
        data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        response = requests.post(url, json=data)
        return response.json()
