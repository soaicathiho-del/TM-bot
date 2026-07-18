import os
import requests
from google import genai

# ===== Gemini Client =====
client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"]
)

# ===== Đọc Prompt =====
with open("prompt.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

# ===== Gọi AI =====
response = client.models.generate_content(
    model="gemini-2.5-flash-lite",
    contents=prompt,
)

message = response.text.strip()

# ===== Gửi Telegram =====
requests.post(
    f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
    json={
        "chat_id": os.environ["CHAT_ID"],
        "text": message,
    },
)
