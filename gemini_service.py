"""Gemini client for TM-bot.

The service keeps the public async API used by main.py and
 daily_consolidation.py, while automatically falling back to another configured
model when a model is out of quota or unavailable.
"""
import asyncio
import logging
from typing import Iterable

from google import genai
from google.genai import types

from config import Config

logger = logging.getLogger(__name__)

client = genai.Client(api_key=Config.GEMINI_API_KEY)


def _model_candidates() -> Iterable[str]:
    """Return unique models in configured priority order."""
    models = [Config.GEMINI_MODEL, *getattr(Config, "GEMINI_FALLBACK_MODELS", ())]
    seen = set()
    for model in models:
        if model and model not in seen:
            seen.add(model)
            yield model


def _error_code(error: Exception):
    """Extract an SDK/HTTP error code without depending on one SDK version."""
    for attribute in ("code", "status_code", "http_status"):
        value = getattr(error, attribute, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return value
    return None


def _generate_once(prompt: str, model: str) -> str:
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.7),
    )
    text = getattr(response, "text", None)
    return text.strip() if text else ""


def _generate_with_fallback(prompt: str) -> str:
    last_error = None

    for model in _model_candidates():
        try:
            logger.info("Calling Gemini model: %s", model)
            response = _generate_once(prompt, model)
            if response:
                if model != Config.GEMINI_MODEL:
                    logger.warning("Primary model unavailable; response came from %s", model)
                return response
            logger.warning("Gemini model %s returned an empty response", model)
        except Exception as error:
            last_error = error
            code = _error_code(error)
            message = str(error)

            if code == 429 or "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
                logger.warning("Quota/rate limit for %s; switching to next model", model)
                continue

            if code in (400, 404) or "not found" in message.lower():
                logger.warning("Model %s is unavailable; switching to next model", model)
                continue

            logger.exception("Gemini error for model %s; trying fallback", model)

    if last_error is not None:
        raise last_error
    return ""


async def ask_gemini(prompt: str) -> str:
    """Generate a response using the first available configured model."""
    return await asyncio.to_thread(_generate_with_fallback, prompt)
