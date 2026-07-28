import asyncio
import logging

from google import genai

from config import Config


logger = logging.getLogger(__name__)


# ==========================================================
# Gemini Client
# ==========================================================

client = genai.Client(
    api_key=Config.GEMINI_API_KEY
)


# ==========================================================
# Internal
# ==========================================================

def _generate(prompt: str) -> str:
    """
    Hàm sync chạy trong thread.
    """

    try:

        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt
        )

        if response is None:
            return "TM: Không nhận được phản hồi từ Gemini."

        if getattr(response, "text", None):
            return response.text.strip()

        return "TM: Gemini không trả về nội dung."

    except Exception as e:

        logger.exception(e)

        return (
            "TM: Hiện tại mình chưa thể kết nối tới Gemini.\n"
            "Hãy thử lại sau vài phút."
        )


# ==========================================================
# Public API
# ==========================================================

async def ask_gemini(prompt: str) -> str:
    """
    Async wrapper.

    Telegram Handler chỉ gọi hàm này.
    """

    return await asyncio.to_thread(
        _generate,
        prompt
    )


# ==========================================================
# Intent Detection
# ==========================================================

async def detect_intent(text: str):

    prompt = f"""
Bạn là bộ phân loại intent.

Chỉ trả về đúng MỘT từ.

finish_task
update_status
other

Message:

{text}
"""

    result = await ask_gemini(prompt)

    return result.strip().lower()


# ==========================================================
# Health Check
# ==========================================================

async def health_check():

    result = await ask_gemini("Reply OK only.")

    return result
