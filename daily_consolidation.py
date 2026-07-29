
import asyncio
import logging
import json
import os
from datetime import datetime, timedelta, timezone

# Import shared logic from main.py (or copy essential parts to keep it standalone)
from config import Config
from gemini_service import ask_gemini
from notion_service import get_today_tasks
from telegram_service import TelegramService

# ==========================================================
# KHỞI TẠO
# ==========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
VN = timezone(timedelta(hours=7))

# ==========================================================
# UTILITIES (Re-used from main.py logic)
# ==========================================================
def load_file(file_name: str) -> str:
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""

def load_json(file_name: str) -> list:
    try:
        if not os.path.exists(file_name): return []
        with open(file_name, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except:
        return []

def save_message_to_history(role: str, content: str):
    path = Config.HISTORY_FILE
    history = load_json(path)
    message = {
        "date": datetime.now(VN).strftime("%Y-%m-%d"),
        "timestamp": datetime.now(VN).isoformat(),
        "role": role,
        "content": content,
    }
    history.append(message)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history[-Config.HISTORY_LIMIT:], f, ensure_ascii=False, indent=2)

# ==========================================================
# PROMPT ENGINE (Minimal version for Consolidation)
# ==========================================================
async def build_consolidation_prompt(task_type: str) -> str:
    system_prompt = load_file("prompts/system/tm-core.md")
    adaptive_rules = load_file("prompts/system/tm-adaptive-rules.md")
    user_profile = load_file(Config.USER_PROFILE_FILE)
    
    # Load task specific prompt
    task_path = f"prompts/tasks/{task_type}.md"
    if task_type == "evening": task_path = "prompts/tasks/sleep.md"
    task_prompt = load_file(task_path)
    
    # Load context
    history = load_json(Config.HISTORY_FILE)
    today = datetime.now(VN).strftime("%Y-%m-%d")
    today_history = [h for h in history if h.get("date") == today]
    
    # Load tasks from Notion
    tasks = get_today_tasks()
    task_lines = []
    for t in tasks:
        try: title = t["properties"]["Task"]["title"][0]["plain_text"]; task_lines.append(f"- {title}")
        except: continue
    tasks_str = "\n".join(task_lines) if task_lines else "Không có task nào."

    prompt = f"""
{system_prompt}
{adaptive_rules}

USER PROFILE:
{user_profile}

TODAY'S CONTEXT:
Type: {task_type}
Tasks: {tasks_str}
History: {json.dumps(today_history, ensure_ascii=False)}

INSTRUCTION:
Đây là tin nhắn chủ động từ bạn gửi cho người dùng vào buổi {task_type}. 
Hãy phản hồi tự nhiên, gần gũi như một người bạn đồng hành.
{task_prompt}
"""
    return prompt

async def generate_daily_summary():
    history = load_json(Config.HISTORY_FILE)
    today = datetime.now(VN).strftime("%Y-%m-%d")
    today_history = [h for h in history if h.get("date") == today]
    
    if not today_history: return None
    
    prompt = f"""
Dựa trên lịch sử trò chuyện hôm nay:
{json.dumps(today_history, ensure_ascii=False)}

Hãy tạo bản tóm tắt ngắn gọn, chân thực về ngày hôm nay (dưới 200 từ).
Người dùng sẽ đọc bản này để duyệt lưu vào bộ nhớ dài hạn.
"""
    summary = await ask_gemini(prompt)
    if summary:
        return f"[TÓM TẮT HÔM NAY]\n{summary}\n\nBạn thấy bản tóm tắt này thế nào? Nhắn \"Duyệt\" để mình lưu vào bộ nhớ nhé!"
    return None

# ==========================================================
# MAIN EXECUTION
# ==========================================================
async def main():
    Config.validate()

    tg = TelegramService()
    chat_id = Config.CHAT_ID

    if not chat_id:
        logger.error("CHAT_ID not found.")
        return

    now = datetime.now(VN)
    hour = now.hour
    minute = now.minute

    # ==========================================================
    # Xác định loại tin nhắn theo giờ chạy
    # ==========================================================

    if hour == 9:
        task_type = "morning"

    elif hour == 15:
        task_type = "focus"

    elif hour == 22:
        task_type = "evening"

    else:
        logger.info(
            f"Skip automation at {hour:02d}:{minute:02d}"
        )
        return

    logger.info(
        f"Running {task_type} automation "
        f"({hour:02d}:{minute:02d})"
    )

    # ==========================================================
    # Tạo prompt
    # ==========================================================

    prompt = await build_consolidation_prompt(task_type)

    response = await ask_gemini(prompt)

    if response:

        tg.send_message(chat_id, response)

        save_message_to_history(
            "bot",
            response,
        )

        logger.info(f"Sent {task_type} message.")

    # ==========================================================
    # Gửi báo cáo cuối ngày
    # ==========================================================

    if task_type == "evening":

        summary = await generate_daily_summary()

        if summary:

            tg.send_message(
                chat_id,
                summary,
            )

            save_message_to_history(
                "bot",
                summary,
            )

            logger.info("Sent daily summary.")


if __name__ == "__main__":
    asyncio.run(main())

if __name__ == "__main__":
    asyncio.run(main())
