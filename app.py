from datetime import datetime, timezone, timedelta

from gemini_service import ask_gemini
from telegram_service import send_message


def load_file(file_name):
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


# =========================
# TIME
# =========================

VN_TIMEZONE = timezone(timedelta(hours=7))
current_time = datetime.now(VN_TIMEZONE)
current_hour = current_time.hour

# =========================
# LOAD SYSTEM
# =========================

system_prompt = load_file("system_prompt.md")
user_profile = load_file("user_profile.md")

# =========================
# SELECT PROMPT
# =========================

if current_hour < 12:
    prompt_file = "prompts/morning.txt"

elif current_hour < 21:
    prompt_file = "prompts/focus.txt"

else:
    prompt_file = "prompts/sleep.txt"

daily_prompt = load_file(prompt_file)

print(f"Current hour : {current_hour}")
print(f"Prompt file  : {prompt_file}")

# =========================
# BUILD PROMPT
# =========================

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

# =========================
# ASK GEMINI
# =========================

response = ask_gemini(prompt)

# =========================
# SEND TELEGRAM
# =========================

send_message(response)

print("Done.")
