import asyncio
import logging

from google import genai
from google.genai import types

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from config import Config

logger = logging.getLogger(__name__)

client = genai.Client(
    api_key=Config.GEMINI_API_KEY
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _generate(prompt: str) -> str:

    response = client.models.generate_content(
        model=Config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.7,
        ),
    )

    if response.text:
        return response.text.strip()

    return "TM: Tôi chưa tạo được phản hồi."


async def ask_gemini(prompt: str) -> str:

    try:

        return await asyncio.to_thread(
            _generate,
            prompt,
        )

    except Exception as e:

        logger.exception("Gemini error")

        raise
