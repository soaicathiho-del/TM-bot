import google.generativeai as genai
from config import GEMINI_API_KEY

class GeminiService:
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    def generate_response(self, system_prompt, user_input, context=""):
        full_prompt = f"{system_prompt}\n\nContext:\n{context}\n\nUser: {user_input}"
        response = self.model.generate_content(full_prompt)
        return response.text

    def detect_intent(self, text):
        # Logic to detect intent: finish task, status update, etc.
        prompt = f"Analyze the following user message and detect intent (finish_task, update_status, other). Message: {text}"
        response = self.model.generate_content(prompt)
        return response.text.strip().lower()
