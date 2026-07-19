from datetime import datetime, timezone, timedelta

from config import Config
from gemini_service import ask_gemini
from telegram_service import send_message


def load_file(file_name):
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


# =========================
# TIME DEBUG
# =========================

vn = timezone(timedelta(hours=7))

utc_now = datetime.now(timezone.utc)
vn_now = datetime.now(vn)
local_now = datetime.now()

# =========================
# LOAD PROMPT
# =========================

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

message = f"""🧪 TM TIME TEST

UTC:
{utc_now.strftime("%Y-%m-%d %H:%M:%S")}

VN:
{vn_now.strftime("%Y-%m-%d %H:%M:%S")}

LOCAL:
{local_now.strftime("%Y-%m-%d %H:%M:%S")}

=========================

{response}
"""

send_message(message)

print("Done.")
