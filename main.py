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
        await update.message.reply_text(resp)
        return

    info = await process_done(task_name)
    save_message("user", f"Hoàn thành task: {task_name}")
    # Xong task -> chắc chắn không còn "đang làm" nữa
    update_state(working=False, working_since=None)
    prompt = build_prompt("done", f"Tôi đã xong task {task_name}", extra_instruction=info)
    resp = await generate_ai_response(prompt) or f"Ghi nhận nhé! Bạn làm tốt lắm khi xong {task_name}."
    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_daily_history()
    summary = ""
    for item in reversed(history):
        if item.get("role") == "bot" and "[TÓM TẮT HÔM NAY]" in item.get("content", ""):
            content = item["content"]
            try:
                summary = content.split("[TÓM TẮT HÔM NAY]")[1].split("Sau khi hiển thị")[0].strip()
            except: summary = content
            break

    save_message("user", "Duyệt bản tóm tắt.")
    if summary and approve_memory(summary):
        prompt = build_prompt("chat", "Tôi đã duyệt bản tóm tắt ngày hôm nay.", extra_instruction="Hệ thống đã lưu xong memory.")
        resp = await generate_ai_response(prompt) or "Đã nhớ! Mình đã lưu lại những điều quan trọng của hôm nay rồi nhé."
    else:
        prompt = build_prompt("chat", "Tôi muốn duyệt nhưng không thấy tóm tắt.", extra_instruction="Lỗi: Không tìm thấy tóm tắt.")
        resp = await generate_ai_response(prompt) or "Ơ, mình chưa thấy bản tóm tắt nào để duyệt cả. Để mình kiểm tra lại nhé."

    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, interaction_type: str = "chat", extra: str = ""):
    user_text = update.message.text
    save_message("user", user_text)

    hour = datetime.now(VN).hour
    energy_status = "Giờ vàng, ưu tiên Deep Work." if (8 <= hour <= 11 or 14 <= hour <= 17) else "Năng lượng có thể thấp, ưu tiên task nhẹ hoặc nghỉ ngơi."
    instruction = f"[ENERGY ADVICE]: {energy_status}"

    if load_state().get("working"):
        instruction += "\n[WORKING MODE]: Người dùng đang làm việc. Không nhắc task, không coaching, chỉ trò chuyện ngắn nếu cần."

    if extra: instruction += f"\n{extra}"

    prompt = build_prompt(interaction_type, user_text, extra_instruction=instruction)
    resp = await generate_ai_response(prompt) or "Mình đang nghe đây, bạn cứ nói tiếp đi."
    await update.message.reply_text(resp)
    save_message("bot", resp)


async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("==============================")
    logger.info("MESSAGE RECEIVED")
    logger.info(update.message.text if update.message else "NO MESSAGE")
    logger.info("==============================")

    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    intent = await detect_intent(user_text)

    try:
        if intent == "approve":
            update_state(smalltalk_count=0)
            await handle_approve(update, context)

        elif intent == "done":
            task = user_text.lower().replace("done", "").replace("xong rồi", "").replace("hoàn thành", "").strip()
            update_state(smalltalk_count=0)
            await handle_done(update, context, task)

        elif intent == "energy":
            await handle_chat(update, context, "chat", extra="Người dùng đang cảm thấy mệt mỏi/nản. Hãy phản hồi như một người bạn đồng hành, thấu hiểu và đưa ra lời khuyên phù hợp. Đồng cảm trước, gợi ý sau, KHÔNG dồn dập coaching.")

        elif intent == "working":
            update_state(
                working=True,
                working_since=datetime.now(VN).isoformat(),
                last_topic=user_text[:80],
                smalltalk_count=0,
            )
            await handle_chat(update, context, "chat", extra="Người dùng vừa xác nhận đang làm việc/tập trung.")

        elif intent == "smalltalk":
            state = load_state()
            update_state(smalltalk_count=state.get("smalltalk_count", 0) + 1)
            await handle_chat(update, context, "chat", extra="Người dùng đang chỉ trò chuyện phiếm/đùa vui. Phản hồi kiểu bạn bè, KHÔNG biến thành coaching.")

        elif intent in ["morning", "sleep"]:
            update_state(smalltalk_count=0)
            await handle_chat(update, context, intent)

        else:
            await handle_chat(update, context, "chat")

    except Exception as e:
        logger.exception(e)
        prompt = build_prompt("chat", user_text, extra_instruction="Hệ thống gặp lỗi kỹ thuật nhẹ.")
        resp = await generate_ai_response(prompt) or "Hình như mình gặp chút trục trặc, bạn nói lại được không?"
        await update.message.reply_text(resp)


# ==========================================================
# PHẦN 10: SCHEDULER (MỚI — thật sự được đăng ký, có dedup)
# ==========================================================
async def run_scheduled_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    if not job or not job.chat_id:
        return

    task_type = job.data
    today = datetime.now(VN).strftime("%Y-%m-%d")

    if already_sent_today(task_type):
        logger.info(f"[Scheduler] Bỏ qua {task_type}, đã gửi hôm nay rồi.")
        return

    prompt = build_prompt(task_type, f"Đến giờ {task_type} rồi.")
    resp = await generate_ai_response(prompt) or f"Đến giờ {task_type} rồi, chúng ta bắt đầu chứ?"
    await context.bot.send_message(chat_id=job.chat_id, text=resp)
    save_message("bot", resp)
    mark_sent(task_type, today)

    if task_type == "sleep":
        history = get_daily_history()
        sum_prompt = f"Hãy tóm tắt ngày hôm nay một cách tự nhiên, chân thực (dưới 200 từ) dựa trên lịch sử này:\n{json.dumps(history, ensure_ascii=False)}"
        summary = await generate_ai_response(sum_prompt)
        if summary:
            msg = f"[TÓM TẮT HÔM NAY]\n{summary}\n\nBạn thấy bản tóm tắt này thế nào? Nhắn \"Duyệt\" để mình lưu vào bộ nhớ nhé!"
            await context.bot.send_message(chat_id=job.chat_id, text=msg)
            save_message("bot", msg)


async def working_timeout_check(context: ContextTypes.DEFAULT_TYPE):
    """MỚI: tự reset working nếu quá lâu người dùng quên báo 'xong rồi'."""
    state = load_state()
    if not state.get("working") or not state.get("working_since"):
        return
    try:
        since = datetime.fromisoformat(state["working_since"])
    except Exception:
        update_state(working=False, working_since=None)
        return
    if datetime.now(VN) - since > timedelta(minutes=Config.WORKING_TIMEOUT_MINUTES):
        logger.info("[Scheduler] Auto-reset working state (timeout).")
        update_state(working=False, working_since=None)


def schedule_jobs(app: Application):
    jq: JobQueue = app.job_queue
    chat_id = int(Config.CHAT_ID)

    jq.run_daily(run_scheduled_job, time=time(Config.MORNING_HOUR, 0, tzinfo=VN),
                 chat_id=chat_id, name="job_morning", data="morning")
    jq.run_daily(run_scheduled_job, time=time(Config.AFTERNOON_HOUR, 0, tzinfo=VN),
                 chat_id=chat_id, name="job_focus", data="focus")
    jq.run_daily(run_scheduled_job, time=time(Config.EVENING_HOUR, 0, tzinfo=VN),
                 chat_id=chat_id, name="job_sleep", data="sleep")

    jq.run_repeating(working_timeout_check,
                      interval=Config.WORKING_TIMEOUT_CHECK_SECONDS,
                      first=60, name="job_working_timeout")

    logger.info("[Scheduler] Đã đăng ký job: morning/focus/sleep + working_timeout_check")


# ==========================================================
# PHẦN 11: MAIN
# ==========================================================
def main():
    Config.validate()
    app = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("done", handle_done))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_router,
        )
    )

    schedule_jobs(app)

    logger.info("==============================")
    logger.info("TM-Bot Started")
    logger.info("==============================")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
