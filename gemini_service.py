from google import genai

from config import Config


def test_gemini() -> str:
    """
    Kiểm tra Gemini API hoạt động.
    """

    client = genai.Client(
        api_key=Config.GEMINI_API_KEY
    )

    response = client.models.generate_content(
        model=Config.MODEL,
        contents="Reply exactly: Hello Morning Coach!"
    )

    return response.text.strip()
