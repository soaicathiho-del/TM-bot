import os
import requests
from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

with open("prompt.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
)

message = response.text.strip()

requests.post(
    f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
    json={
        "chat_id": os.environ["CHAT_ID"],
        "text": message,
    },
)
