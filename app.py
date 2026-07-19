from config import Config
from gemini_service import ask_gemini
from telegram_service import send_message


def load_file(file_name):
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


system_prompt = load_file("system_prompt.md")
user_profile = load_file("user_profile.md")
daily_prompt = load_file("prompt.txt")

prompt = f"""
{system_prompt}

=========================
USER PROFILE
=========================

{user_profile}

=========================
TODAY TASK
=========================

{daily_prompt}
"""

response = ask_gemini(prompt)

send_message(response)

print("Done.")
