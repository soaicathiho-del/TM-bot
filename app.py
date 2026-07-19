from datetime import datetime, timedelta, timezone

from config import Config
from gemini_service import ask_gemini
from telegram_service import send_message
from notion_service import get_today_tasks


def load_file(file_name):
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


# =========================
# TIME
# =========================

VN = timezone(timedelta(hours=7))
now = datetime.now(VN)
hour = now.hour

# =========================
# SELECT PROMPT
# =========================

if hour < 12:
    prompt_file = "prompts/morning.txt"

elif hour < 18:
    prompt_file = "prompts/focus.txt"

else:
    prompt_file = "prompts/sleep.txt"

system_prompt = load_file("system_prompt.md")
user_profile = load_file("user_profile.md")
daily_prompt = load_file(prompt_file)

# =========================
# NOTION
# =========================

tasks = get_today_tasks()

task_text = ""

for task in tasks:

    props = task["properties"]

    title = props["Task"]["title"][0]["plain_text"]
    task_type = props["Type"]["select"]["name"]

    task_text += f"- {title} ({task_type})\n"

if task_text == "":
    task_text = "Không có task hôm nay."

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
TODAY TASKS
=========================

{task_text}

=========================
MESSAGE STYLE
=========================

{daily_prompt}
"""

# =========================
# GEMINI
# =========================

response = ask_gemini(prompt)

# =========================
# TELEGRAM
# =========================

send_message(response)

print("Done.")
