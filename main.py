import logging
import json
import os
from datetime import datetime, timedelta, timezone, time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    JobQueue
)

from config import Config
from gemini_service import ask_gemini
from notion_service import (
    get_today_tasks,
    update_task_status,
    find_task_by_title,
    update_status_note,
)

# ==========================================================
# PHẦN 1: KHỞI TẠO (Imports, Logger, Timezone)
# ==========================================================
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
    "last_sent": {},   # {"morning": "2026-07-31", "focus": "...", "sleep": "..."}
}

# ==========================================================
# PHẦN 2: TIỆN ÍCH (Utilities)
# ==========================================================
def load_file(file_name: str) -> str:
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except Exception as e:
        logger.exception(e)
        return ""


def load_json(file_name: str) -> list:
    try:
        if not os.path.exists(file_name):
            return []
        with open(file_name, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        logger.warning(f"Error decoding JSON from {file_name}. Returning empty list.")
        return []
    except Exception as e:
        logger.exception(e)
        return []


def save_json(file_name: str, data: list):
    try:
        os.makedirs(os.path.dirname(file_name), exist_ok=True)
        with open(file_name, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception(e)


# ==========================================================
# PHẦN 3: LỊCH SỬ (History)
# ==========================================================
def get_daily_history() -> list:
    path = Config.HISTORY_FILE
    history = load_json(path)
    today = datetime.now(VN).strftime("%Y-%m-%d")
    return [item for item in history if item.get("date") == today]


def save_message(role: str, content: str):
    path = Config.HISTORY_FILE
    history = load_json(path)
    message = {
        "date": datetime.now(VN).strftime("%Y-%m-%d"),
        "timestamp": datetime.now(VN).isoformat(),
        "role": role,
        "content": content,
    }
    history.append(message)
    save_json(path, history[-Config.HISTORY_LIMIT:])


# ==========================================================
# PHẦN 3.5: SESSION STATE (MỚI — dùng chung cho toàn bộ logic)
# ==========================================================
def load_state() -> dict:
    path = Config.STATE_FILE
    try:
        if not os.path.exists(path):
            save_state(dict(DEFAULT_STATE))
            return dict(DEFAULT_STATE)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_STATE)
        merged.update(data)
        return merged
    except Exception as e:
        logger.exception(e)
        return dict(DEFAULT_STATE)


def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(Config.STATE_FILE), exist_ok=True)
        with open(Config.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception(e)


def update_state(**kwargs) -> dict:
    state = load_state()
    state.update(kwargs)
    save_state(state)
    return state


def mark_sent(task_type: str, date_str: str):
    state = load_state()
    state.setdefault("last_sent", {})[task_type] = date_str
    save_state(state)


def already_sent_today(task_type: str) -> bool:
    today = datetime.now(VN).strftime("%Y-%m-%d")
    state = load_state()
    return state.get("last_sent", {}).get(task_type) == today


# ==========================================================
# PHẦN 4: BỘ NHỚ (Memory)
# ==========================================================
def load_memory() -> list:
    path = Config.MEMORY_FILE
    return load_json(path)


def append_memory(summary: str):
    memories = load_memory()
    new_memory = {
        "date": datetime.now(VN).strftime("%Y-%m-%d"),
        "summary": summary,
    }
    memories.append(new_memory)
    save_json(Config.MEMORY_FILE, memories)


def approve_memory(summary: str) -> bool:
    try:
        append_memory(summary)
        return True
    except Exception as e:
        logger.exception(e)
        return False


# ==========================================================
# PHẦN 5: PROMPT ENGINE (Có Cache)
# ==========================================================
PROMPT_CACHE = {}


def _load_prompt_file(file_path: str) -> str:
    if file_path in PROMPT_CACHE:
        return PROMPT_CACHE[file_path]
    content = load_file(file_path)
    if content:
        PROMPT_CACHE[file_path] = content
    return content


def load_system_prompt() -> str:
    return _load_prompt_file("prompts/system/tm-core.md")


def load_adaptive_rules_prompt() -> str:
    return _load_prompt_file("prompts/system/tm-adaptive-rules.md")


def load_user_profile_prompt() -> str:
    return _load_prompt_file(Config.USER_PROFILE_FILE)


def load_long_term_memory_prompt() -> str:
    memories = load_memory()
    if not memories:
        return "Không có bộ nhớ dài hạn."
    formatted_memories = "\n".join(
        [f"- {m['date']}: {m['summary']}" for m in memories]
    )
    return f"LONG TERM MEMORY:\n{formatted_memories}"


def load_today_history_prompt() -> str:
    history = get_daily_history()
    if not history:
        return "Không có lịch sử trò chuyện hôm nay."

    now = datetime.now(VN)
    lines = []
    for h in history:
        try:
            ts = datetime.fromisoformat(h["timestamp"])
            delta_min = int((now - ts).total_seconds() // 60)
            if delta_min < 1:
                delta_str = "vừa xong"
            elif delta_min < 60:
                delta_str = f"{delta_min} phút trước"
            else:
                delta_str = f"{delta_min // 60} giờ {delta_min % 60} phút trước"
        except Exception:
            delta_str = "?"
        lines.append(f"- [{delta_str}] {h['role'].upper()}: {h['content']}")

    return "TODAY'S HISTORY (kèm khoảng cách thời gian tới hiện tại):\n" + "\n".join(lines)


def load_today_tasks_prompt() -> str:
    try:
        tasks = get_today_tasks()
        task_lines = []
        for task in tasks:
            try:
                title = task["properties"]["Task"]["title"][0]["plain_text"]
                task_lines.append(f"- {title}")
            except Exception:
                continue
        if task_lines:
            return f"TODAY'S TASKS:\n{os.linesep.join(task_lines)}"
    except Exception:
        pass
    return "Không có task nào chưa hoàn thành."


def load_task_specific_prompt(interaction_type: str) -> str:
    mapping = {
        "morning": "prompts/tasks/morning.md",
        "focus": "prompts/tasks/focus.md",
        "sleep": "prompts/tasks/sleep.md"
    }
    path = mapping.get(interaction_type)
    return _load_prompt_file(path) if path else ""


def load_state_prompt() -> str:
    """MỚI: cho model biết TRẠNG THÁI hiện tại, không chỉ nội dung chat."""
    state = load_state()
    lines = ["CONVERSATION STATE:"]

    if state.get("working"):
        since = state.get("working_since")
        try:
            mins = int((datetime.now(VN) - datetime.fromisoformat(since)).total_seconds() // 60)
        except Exception:
            mins = "?"
        lines.append(f"- Người dùng ĐANG trong phiên làm việc/tập trung (bắt đầu {mins} phút trước).")
        lines.append("- KHÔNG được nhắc mở task, KHÔNG coaching lại, KHÔNG hỏi tiến độ dồn dập.")
        lines.append("- Nếu người dùng chủ động nhắn, chỉ trò chuyện ngắn gọn, tự nhiên, rồi để họ quay lại làm việc.")
    else:
        lines.append("- Người dùng hiện KHÔNG trong phiên làm việc nào được xác nhận.")

    smalltalk_count = state.get("smalltalk_count", 0)
    if smalltalk_count >= Config.SMALL_TALK_LIMIT:
        lines.append(f"- Đã trò chuyện phiếm {smalltalk_count} lượt liên tiếp. "
                      "Có thể nhẹ nhàng gợi ý quay lại việc, KHÔNG ép buộc, KHÔNG lặp lại nhàm chán.")

    return "\n".join(lines)


def build_role_prompt() -> str:
    """Role prompt giờ PHỤ THUỘC vào state — đây là chỗ sửa lỗi 'vừa chúc ngủ ngon xong lại nhắc task'."""
    state = load_state()
    if state.get("working"):
        return (
            "ROLE:\nBạn đồng thời là Planner, Coach, Accountability Partner. "
            "Hãy phản hồi tự nhiên, gần gũi như con người. Tuyệt đối không nói mình là AI. "
            "NGƯỜI DÙNG ĐANG LÀM VIỆC — bây giờ bạn ở SUPPORT MODE: không thúc giục, "
            "không nhắc task, không coaching. Chỉ đồng hành nhẹ nhàng nếu họ chủ động nói chuyện."
        )
    return (
        "ROLE:\nBạn đồng thời là Planner, Coach, Accountability Partner. "
        "Hãy phản hồi tự nhiên, gần gũi như con người. Tuyệt đối không nói mình là AI. "
        "Ưu tiên hành động và động viên người dùng."
    )


def build_prompt(interaction_type: str, user_message: str, extra_instruction: str = "") -> str:
    parts = [
        load_system_prompt(),
        load_adaptive_rules_prompt(),
        load_task_specific_prompt(interaction_type),
        load_user_profile_prompt(),
        load_long_term_memory_prompt(),
        load_state_prompt(),
        load_today_history_prompt(),
        load_today_tasks_prompt(),
        f"CURRENT USER MESSAGE:\nInteraction: {interaction_type}\nUser: {user_message}",
        f"SYSTEM INSTRUCTION:\n{extra_instruction}" if extra_instruction else "",
        build_role_prompt(),
    ]
    return "\n\n".join([p for p in parts if p])


# ==========================================================
# PHẦN 6: AI ENGINE (Atomic)
# ==========================================================
async def generate_ai_response(prompt: str, _retry_once: bool = True) -> str:
    try:
        response = await ask_gemini(prompt)
        response = response.strip() if response else ""
        if not response and _retry_once:
            # Sửa lỗi: fallback cũ chỉ trigger khi rỗng, không phải khi trùng câu cũ.
            logger.warning("Empty response from Gemini, retrying once...")
            return await generate_ai_response(prompt, _retry_once=False)
        return response
    except Exception as e:
        logger.error(f"AI Engine Error: {e}")
        return ""


# ==========================================================
# PHẦN 7: NHẬN DIỆN Ý ĐỊNH (Intent Detection)
# ==========================================================
WORKING_KEYWORDS = [
    "đang làm", "đang viết", "đang code", "đang tập trung",
    "để t làm đã", "để tao làm đã", "ok đang làm", "ừ đang làm", "đang cày",
]
SMALLTALK_KEYWORDS = [
    "haha", "hihi", ":))", ":v", "=))", "á đù", "ạ đù", "hehe", "khakha", ":)))",
]


async def detect_intent(user_message: str) -> str:
    text = user_message.lower()
    if any(kw in text for kw in ["duyệt", "approve"]): return "approve"
    if any(kw in text for kw in ["xong rồi", "hoàn thành", "done"]): return "done"
    if any(kw in text for kw in ["mệt", "đuối", "kiệt sức", "nản"]): return "energy"
    if any(kw in text for kw in ["ngủ", "đi ngủ", "ngủ đây"]): return "sleep"
    if any(kw in text for kw in ["chào buổi sáng", "morning"]): return "morning"
    if any(kw in text for kw in WORKING_KEYWORDS): return "working"
    if any(kw in text for kw in SMALLTALK_KEYWORDS): return "smalltalk"
    return "chat"


# ==========================================================
# PHẦN 8: NOTION
# ==========================================================
async def process_done(task_name: str) -> str:
    try:
        task = find_task_by_title(task_name)
        if not task: return f"Lưu ý: Không tìm thấy task '{task_name}' trên Notion để cập nhật tự động."
        update_task_status(task["id"], done=True)
        update_status_note(task["id"], f"Hoàn thành lúc {datetime.now(VN).strftime('%H:%M')}")
        return f"Hệ thống đã cập nhật xong task '{task_name}' trên Notion."
    except Exception as e:
        logger.error(f"Notion Error: {e}")
        return "Lưu ý: Gặp lỗi khi cập nhật Notion."


# ==========================================================
# PHẦN 9: TELEGRAM HANDLERS
# ==========================================================
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "Chào TM, tôi bắt đầu phiên làm việc."
    save_message("user", msg)
    prompt = build_prompt("morning", msg)
    resp = await generate_ai_response(prompt) or "Chào bạn! Mình đã sẵn sàng đồng hành cùng bạn hôm nay. Bắt đầu thôi!"
    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE, task_name: str = ""):
    if not task_name and context.args: task_name = " ".join(context.args)
    if not task_name:
        prompt = build_prompt("chat", "Tôi vừa xong việc nhưng quên nói tên task.")
        resp = await generate_ai_response(prompt) or "Tuyệt! Mà bạn vừa hoàn thành task nào thế để mình ghi nhận?"
        await
