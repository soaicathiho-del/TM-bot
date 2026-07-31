import asyncio
import logging
import json
import os
from datetime import datetime, timedelta, timezone

from config import Config
from gemini_service import ask_gemini
from notion_service import get_today_tasks
from telegram_service import TelegramService

# ⚠️ LƯU Ý QUAN TRỌNG:
# File này chạy trên GitHub Actions (máy ảo tạm), KHÔNG chung ổ đĩa với worker (main.py).
# state/history đọc ở đây chỉ đúng NẾU workflow có commit data/ ngược lại vào git sau mỗi lần chạy.
# Nếu không chắc, hãy coi file này là LƯỚI AN TOÀN DỰ PHÒNG (phòng khi worker sập),
# không phải nguồn lịch chính — lịch chính giờ nằm ở JobQueue trong main.py.

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

VN = timezone(timedelta(hours=7))

DEFAULT_STATE = {
    "working": False,
    "working_since": None,
    "last_topic": "",
    "smalltalk_count": 0,
    "last_sent": {},
}


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


def load_state() -> dict:
    try:
        if not os.path.exists(Config.STATE_FILE):
            return dict(DEFAULT_STATE)
        with open(Config.STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_STATE)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_STATE)


def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(Config.STATE_FILE), exist_ok=True)
        with open(Config.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception(e)


def mark_sent(task_type: str, date_str: str):
    state = load_state()
    state.setdefault("last_sent", {})[task_type] = date_str
    save_state(state)


def already_sent_today(task_type: str) -> bool:
    today = datetime.now(VN).strftime("%Y-%m-%d")
    return load_state().get("last_sent", {}).get(task_type) == today


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


async def build_consolidation_prompt(task_type: str, working: bool) -> str:
    system_prompt = load_file("prompts/system/tm-core.md")
    adaptive_rules = load_file("prompts/system/tm-adaptive-rules.md")
    user_profile = load_file(Config.USER_PROFILE_FILE)

    task_path = f"prompts/tasks/{task_type}.md"
    if task_type == "evening": task_path = "prompts/tasks/sleep.md"
    task_prompt = load_file(task_path)

    history = load_json(Config.HISTORY_FILE)
    today = datetime.now(VN).strftime("%Y-%m-%d")
    today_history = [h for h in history if h.get("date") == today]

    tasks = get_today_tasks()
    task_lines = []
    for t in tasks:
        try: title = t["properties"]["Task"]["title"][0]["plain_text"]; task_lines.append(f"- {title}")
        except: continue
    tasks_str = "\n".join(task_lines) if task_lines else "Không có task nào."

    working_note = ""
    if working and task_type == "focus":
        working_note = (
            "LƯU Ý: Người dùng đang trong phiên làm việc (working=True). "
            "KHÔNG gửi kiểu nhắc nhở/coaching cứng ('Vào ca focus đi'). "
            "Thay vào đó hỏi thăm nhẹ nhàng kiểu bạn bè, ví dụ tinh thần: "
            "'Còn sống không :))' hoặc 'Tiến độ sao rồi'."
        )

    prompt = f"""
{system_prompt}

{adaptive_rules}

USER PROFILE:
{user_profile}

TODAY'S CONTEXT:
Type: {task_type}
Tasks: {tasks_str}
History: {json.dumps(today_history, ensure_ascii=False)}
{working_note}

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


async def main():
    Config.validate()
    tg = TelegramService()
    chat_id = Config.CHAT_ID
    if not chat_id:
        logger.error("CHAT_ID not found.")
        return

    now = datetime.now(VN)
    hour = now.hour

    if 6 <= hour < 12:
        task_type = "morning"
    elif 12 <= hour < 18:
        task_type = "focus"
    elif 18 <= hour < 24:
        task_type = "evening"
    else:
        logger.info(f"Outside automation window ({hour:02d}:{now.minute:02d})")
        return

    dedup_key = "sleep" if task_type == "evening" else task_type
    if already_sent_today(dedup_key):
        logger.info(f"[Fallback] Bỏ qua {task_type}, có vẻ main.py JobQueue đã gửi rồi hôm nay.")
        return

    logger.info(f"[Fallback] Running {task_type} automation ({hour:02d}:{now.minute:02d})")

    state = load_state()
    prompt = await build_consolidation_prompt(task_type, working=state.get("working", False))
    response = await ask_gemini(prompt)

    if response:
        tg.send_message(chat_id, response)
        save_message_to_history("bot", response)
        mark_sent(dedup_key, now.strftime("%Y-%m-%d"))
        logger.info(f"[Fallback] Sent {task_type} message.")

    if task_type == "evening":
        summary = await generate_daily_summary()
        if summary:
            tg.send_message(chat_id, summary)
            save_message_to_history("bot", summary)
            logger.info("[Fallback] Sent daily summary.")


if __name__ == "__main__":
    asyncio.run(main())
