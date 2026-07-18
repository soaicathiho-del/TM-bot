from google import genai

from config import Config


def ask_gemini(prompt: str) -> str:
    """
    Gửi prompt tới Gemini và trả về kết quả.
    """

    client = genai.Client(
        api_key=Config.GEMINI_API_KEY
    )

    response = client.models.generate_content(
        model=Config.MODEL,
        contents=prompt
    )

    return response.text.strip()
