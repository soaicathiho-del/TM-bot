"""
TM-Bot v2
Gemini Service

Chỉ chịu trách nhiệm:
- Kết nối Gemini
- Sinh phản hồi
- Sinh JSON (cho tương lai)

Không chứa:
- Telegram
- Notion
- Memory
- Prompt Logic
"""

from google import genai
from config import Config


class GeminiService:

    def __init__(self):

        self.client = genai.Client(
            api_key=Config.GEMINI_API_KEY
        )

    async def generate(self, prompt: str) -> str:

        response = self.client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt,
        )

        if hasattr(response, "text"):
            return response.text

        return ""

    async def generate_json(self, prompt: str) -> str:
        """
        Chuẩn bị cho:
        - Intent Detection
        - Structured Output
        - Analytics
        """

        response = self.client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt,
        )

        if hasattr(response, "text"):
            return response.text

        return ""


# ==========================================================
# Singleton
# ==========================================================

gemini = GeminiService()


async def ask_gemini(prompt: str) -> str:
    return await gemini.generate(prompt)
