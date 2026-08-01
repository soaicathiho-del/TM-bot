import asyncio
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
    create_task,
    get_rules_point_map,
    get_tasks_by_date_range,
    get_task_type,
)

# ==========================================================
# PHẦN 1: KHỞI TẠO
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
    "focus_session": None,
    "last_task_reminder_at": None,
    "push_count": 0,
    "smalltalk_count": 0,
    "last_sent": {},
    "last_summary_at": None,
    "messages_since_summary": 0,
}


# ==========================================================
# PHẦN 2: TIỆN ÍCH
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
        logger.warning(f"Error decoding JSON from {file_name}.")
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


def is_golden_hour(hour: int) -> bool:
    return any(start <= hour <= end for start, end in Config.WORKING_HOUR_RANGES)


# ==========================================================
# PHẦN 3: LỊCH SỬ
# ==========================================================
def get_daily_history() -> list:
    history = load_json(Config.HISTORY_FILE)
    today = datetime.now(VN).strftime("%Y-%m-%d")
    return [item for item in history if item.get("date") == today]


def save_message(role: str, content: str):
    history = load_json(Config.HISTORY_FILE)
    message = {
        "date": datetime.now(VN).strftime("%Y-%m-%d"),
        "timestamp": datetime.now(VN).isoformat(),
        "role": role,
        "content": content,
    }
    history.append(message)
    save_json(Config.HISTORY_FILE, history[-Config.HISTORY_LIMIT:])

    # Mục 13: đếm tin nhắn mới sau khi đã gửi tóm tắt tối, để tự update lại
    if role == "user":
        state = load_state()
        if state.get("last_summary_at"):
            update_state(messages_since_summary=state.get("messages_since_summary", 0) + 1)


# ==========================================================
# PHẦN 3.5: SESSION STATE
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
    return load_state().get("last_sent", {}).get(task_type) == today


def record_push():
    state = load_state()
    update_state(
        push_count=state.get("push_count", 0) + 1,
        last_task_reminder_at=datetime.now(VN).isoformat(),
    )


def reset_push():
    update_state(push_count=0, last_task_reminder_at=None)


def reset_work_and_push():
    update_state(
        working=False, working_since=None, focus_session=None,
        push_count=0, last_task_reminder_at=None, smalltalk_count=0,
    )


# ==========================================================
# PHẦN 4: BỘ NHỚ
# ==========================================================
def load_memory() -> list:
    return load_json(Config.MEMORY_FILE)


def append_memory(summary: str):
    memories = load_memory()
    memories.append({"date": datetime.now(VN).strftime("%Y-%m-%d"), "summary": summary})
    save_json(Config.MEMORY_FILE, memories)


def approve_memory(summary: str) -> bool:
    try:
        append_memory(summary)
        return True
    except Exception as e:
        logger.exception(e)
        return False


# ==========================================================
# PHẦN 5: PROMPT ENGINE
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
    lines = "\n".join([f"- {m['date']}: {m['summary']}" for m in memories])
    return f"LONG TERM MEMORY:\n{lines}"


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
    return "TODAY'S HISTORY (kèm khoảng cách thời gian):\n" + "\n".join(lines)


async def load_today_tasks_prompt() -> str:
    try:
        tasks = await asyncio.to_thread(get_today_tasks)
        task_lines = []
        for task in tasks:
            try:
                title = task["properties"]["Task"]["title"][0]["plain_text"]
                task_lines.append(f"- {title}")
            except Exception:
                continue
        if task_lines:
            return "TODAY'S TASKS:\n" + "\n".join(task_lines)
    except Exception:
        pass
    return "Không có task nào chưa hoàn thành."


def load_task_specific_prompt(interaction_type: str) -> str:
    mapping = {
        "morning": "prompts/tasks/morning.md",
        "focus": "prompts/tasks/focus.md",
        "sleep": "prompts/tasks/sleep.md",
    }
    path = mapping.get(interaction_type)
    return _load_prompt_file(path) if path else ""


def load_state_prompt() -> str:
    state = load_state()
    lines = ["CONVERSATION STATE:"]

    if state.get("focus_session"):
        fs = state["focus_session"]
        lines.append(f"- Đang trong phiên FOCUS MODE {fs.get('minutes')} phút "
                      f"(đã check-in {fs.get('checkins', 0)} lần). Giọng nghiêm khắc, câu ngắn.")
    elif state.get("working"):
        since = state.get("working_since")
        try:
            mins = int((datetime.now(VN) - datetime.fromisoformat(since)).total_seconds() // 60)
        except Exception:
            mins = "?"
        lines.append(f"- Người dùng ĐANG làm việc tự do (bắt đầu {mins} phút trước). "
                      "KHÔNG nhắc task, KHÔNG coaching, chỉ trò chuyện ngắn nếu cần.")
    else:
        lines.append("- Người dùng hiện KHÔNG trong phiên làm việc nào được xác nhận.")

    last_reminder = state.get("last_task_reminder_at")
    if last_reminder:
        try:
            mins_since = int((datetime.now(VN) - datetime.fromisoformat(last_reminder)).total_seconds() // 60)
            if mins_since < Config.REMINDER_GAP_MINUTES:
                lines.append(f"- Vừa nhắc task {mins_since} phút trước. KHÔNG chủ động nhắc lại "
                              f"trừ khi người dùng chủ động hỏi (giãn cách {Config.REMINDER_GAP_MINUTES} phút).")
        except Exception:
            pass

    push_count = state.get("push_count", 0)
    if push_count >= Config.PUSH_LIMIT:
        lines.append(f"- Đã Push {push_count} lần liên tiếp mà chưa thấy hành động. "
                      "BẮT BUỘC chuyển sang PROBE lần này: hỏi nguyên nhân theo 3 trục "
                      "(kỹ năng / tâm trạng / thời gian). KHÔNG Push thêm.")

    if state.get("smalltalk_count", 0) >= Config.SMALL_TALK_LIMIT:
        lines.append("- Đã trò chuyện phiếm nhiều lượt liên tiếp. Có thể nhẹ nhàng gợi ý quay lại việc.")

    return "\n".join(lines)


def build_role_prompt() -> str:
    state = load_state()
    if state.get("working"):
        return (
            "ROLE:\nBạn đồng thời là Planner, Coach, Accountability Partner. "
            "Phản hồi tự nhiên, gần gũi như con người. Tuyệt đối không nói mình là AI. "
            "NGƯỜI DÙNG ĐANG LÀM VIỆC — SUPPORT MODE: không thúc giục, không nhắc task, "
            "không coaching. Chỉ đồng hành nhẹ nếu họ chủ động nói chuyện."
        )
    return (
        "ROLE:\nBạn đồng thời là Planner, Coach, Accountability Partner. "
        "Phản hồi tự nhiên, gần gũi như con người. Tuyệt đối không nói mình là AI. "
        "Ưu tiên hành động và động viên người dùng."
    )


async def build_prompt(interaction_type: str, user_message: str, extra_instruction: str = "") -> str:
    parts = [
        load_system_prompt(),
        load_adaptive_rules_prompt(),
        load_task_specific_prompt(interaction_type),
        load_user_profile_prompt(),
        load_long_term_memory_prompt(),
        load_state_prompt(),
        load_today_history_prompt(),
        await load_today_tasks_prompt(),
        f"CURRENT USER MESSAGE:\nInteraction: {interaction_type}\nUser: {user_message}",
        f"SYSTEM INSTRUCTION:\n{extra_instruction}" if extra_instruction else "",
        build_role_prompt(),
    ]
    return "\n\n".join([p for p in parts if p])


# ==========================================================
# PHẦN 6: AI ENGINE
# ==========================================================
async def generate_ai_response(prompt: str, _retry_once: bool = True) -> str:
    try:
        response = await ask_gemini(prompt)
        response = response.strip() if response else ""
        if not response and _retry_once:
            logger.warning("Empty response from Gemini, retrying once...")
            return await generate_ai_response(prompt, _retry_once=False)
        return response
    except Exception as e:
        logger.error(f"AI Engine Error: {e}")
        return ""


# ==========================================================
# PHẦN 7: NHẬN DIỆN Ý ĐỊNH
# ==========================================================
WORKING_KEYWORDS = [
    "đang làm", "đang viết", "đang code", "đang tập trung",
    "để t làm đã", "để tao làm đã", "ok đang làm", "ừ đang làm", "đang cày",
]
SMALLTALK_KEYWORDS = [
    "haha", "hihi", ":))", ":v", "=))", "á đù", "ạ đù", "hehe", "khakha", ":)))",
]
TASK_ADD_KEYWORDS = ["thêm task", "tạo task", "note task", "ghi task mới", "thêm việc", "tạo việc mới"]
SUGGEST_KEYWORDS = ["nên làm gì", "làm gì trước", "task nào ưu tiên", "việc gì quan trọng nhất", "gợi ý task"]


async def detect_intent(user_message: str) -> str:
    text = user_message.lower()
    if any(kw in text for kw in ["duyệt", "approve"]): return "approve"
    if any(kw in text for kw in ["xong rồi", "hoàn thành", "done"]): return "done"
    if any(kw in text for kw in TASK_ADD_KEYWORDS): return "add_task"
    if any(kw in text for kw in SUGGEST_KEYWORDS): return "suggest_task"
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
        task = await asyncio.to_thread(find_task_by_title, task_name)
        if not task:
            return f"Lưu ý: Không tìm thấy task '{task_name}' trên Notion để cập nhật tự động."
        await asyncio.to_thread(update_task_status, task["id"], True)
        await asyncio.to_thread(update_status_note, task["id"], f"Hoàn thành lúc {datetime.now(VN).strftime('%H:%M')}")
        return f"Hệ thống đã cập nhật xong task '{task_name}' trên Notion."
    except Exception as e:
        logger.error(f"Notion Error: {e}")
        return "Lưu ý: Gặp lỗi khi cập nhật Notion."


# ==========================================================
# PHẦN 9: TELEGRAM HANDLERS
# ==========================================================
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hour = datetime.now(VN).hour
    interaction = "morning" if hour < 12 else "chat"
    msg = "Chào TM, tôi bắt đầu phiên làm việc, cho tôi xem task hôm nay."
    save_message("user", msg)
    prompt = await build_prompt(interaction, msg)
    resp = await generate_ai_response(prompt) or "Chào bạn! Đây là task hôm nay, bắt đầu thôi!"
    await update.message.reply_text(resp)
    save_message("bot", resp)


def cancel_focus_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    for j in context.job_queue.get_jobs_by_name(f"focus_checkin_{chat_id}"):
        j.schedule_removal()


async def handle_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        minutes = int(context.args[0]) if context.args else Config.FOCUS_DEFAULT_MINUTES
    except (ValueError, IndexError):
        minutes = Config.FOCUS_DEFAULT_MINUTES

    chat_id = update.effective_chat.id
    cancel_focus_job(context, chat_id)
    update_state(
        focus_session={"minutes": minutes, "start": datetime.now(VN).isoformat(), "checkins": 0},
        working=True, working_since=datetime.now(VN).isoformat(),
        push_count=0, last_task_reminder_at=None,
    )
    context.job_queue.run_repeating(
        focus_checkin, interval=minutes * 60, first=minutes * 60,
        chat_id=chat_id, name=f"focus_checkin_{chat_id}",
    )
    prompt = await build_prompt("focus", f"Bắt đầu phiên Focus {minutes} phút.")
    resp = await generate_ai_response(prompt) or f"Focus {minutes} phút. Bắt đầu ngay."
    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_stop_focus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_focus_job(context, update.effective_chat.id)
    reset_work_and_push()
    msg = "Đã dừng phiên Focus."
    await update.message.reply_text(msg)
    save_message("bot", msg)


async def focus_checkin(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    state = load_state()
    session = state.get("focus_session")
    if not session:
        job.schedule_removal()
        return
    session["checkins"] = session.get("checkins", 0) + 1
    update_state(focus_session=session)
    prompt = await build_prompt(
        "focus",
        f"Đã {session['minutes']} phút trôi qua trong phiên Focus (check-in lần {session['checkins']}). "
        "Hỏi tiến độ ngắn gọn, giọng nghiêm khắc.",
    )
    resp = await generate_ai_response(prompt) or "Đang tới đâu rồi? Báo cáo nhanh."
    await context.bot.send_message(chat_id=chat_id, text=resp)
    save_message("bot", resp)


async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE, task_name: str = ""):
    if not task_name and context.args:
        task_name = " ".join(context.args)
    if not task_name:
        prompt = await build_prompt("chat", "Tôi vừa xong việc nhưng quên nói tên task.")
        resp = await generate_ai_response(prompt) or "Tuyệt! Bạn vừa hoàn thành task nào để mình ghi nhận?"
        await update.message.reply_text(resp)
        return

    info = await process_done(task_name)
    save_message("user", f"Hoàn thành task: {task_name}")
    cancel_focus_job(context, update.effective_chat.id)
    reset_work_and_push()
    prompt = await build_prompt("done", f"Tôi đã xong task {task_name}", extra_instruction=info)
    resp = await generate_ai_response(prompt) or f"Ghi nhận nhé! Bạn làm tốt khi xong {task_name}."
    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    lower = text.lower()
    trigger = next((kw for kw in TASK_ADD_KEYWORDS if kw in lower), None)
    idx = (lower.find(trigger) + len(trigger)) if trigger else 0
    title = text[idx:].strip(" :-").strip()

    save_message("user", text)
    if not title:
        msg = "Bạn muốn thêm task gì? Nhắn lại tên task nhé."
        await update.message.reply_text(msg)
        save_message("bot", msg)
        return

    rules_map = await asyncio.to_thread(get_rules_point_map)
    matched_type = next((t for t in rules_map.keys() if t.lower() in lower), None)

    page = await asyncio.to_thread(create_task, title, matched_type)
    if page:
        type_note = f" (loại: {matched_type})" if matched_type else " (chưa phân loại — vào Notion gắn Type sau nhé)"
        info = f"Đã tạo task '{title}'{type_note} trên Notion."
    else:
        info = f"Lỗi: không tạo được task '{title}' trên Notion."

    prompt = await build_prompt("chat", text, extra_instruction=info)
    resp = await generate_ai_response(prompt) or info
    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_suggest_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    save_message("user", text)

    tasks = await asyncio.to_thread(get_today_tasks)
    rules_map = await asyncio.to_thread(get_rules_point_map)

    def sort_key(task):
        t = get_task_type(task)
        prio = rules_map.get(t, {}).get("priority")
        return prio if prio is not None else 999

    sorted_tasks = sorted(tasks, key=sort_key)
    lines = []
    for t in sorted_tasks:
        try:
            title = t["properties"]["Task"]["title"][0]["plain_text"]
        except Exception:
            continue
        ttype = get_task_type(t) or "?"
        prio = rules_map.get(ttype, {}).get("priority", "?")
        lines.append(f"- {title} (Type: {ttype}, Priority: {prio})")

    task_list_str = "\n".join(lines) if lines else "Không có task nào."
    prompt = await build_prompt(
        "chat", text,
        extra_instruction=(f"Danh sách task hôm nay đã sắp theo Priority (số nhỏ = ưu tiên cao):\n"
                            f"{task_list_str}\nĐề xuất 1 task nên làm trước, đúng persona TM."),
    )
    resp = await generate_ai_response(prompt) or (f"Ưu tiên: {lines[0]}" if lines else "Chưa có task nào hôm nay.")
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
            except Exception:
                summary = content
            break

    save_message("user", "Duyệt bản tóm tắt.")
    if summary and approve_memory(summary):
        prompt = await build_prompt("chat", "Tôi đã duyệt bản tóm tắt ngày hôm nay.",
                                     extra_instruction="Hệ thống đã lưu xong memory.")
        resp = await generate_ai_response(prompt) or "Đã nhớ! Mình đã lưu lại những điều quan trọng của hôm nay."
    else:
        prompt = await build_prompt("chat", "Tôi muốn duyệt nhưng không thấy tóm tắt.",
                                     extra_instruction="Lỗi: Không tìm thấy tóm tắt.")
        resp = await generate_ai_response(prompt) or "Mình chưa thấy bản tóm tắt nào để duyệt cả."

    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       interaction_type: str = "chat", extra: str = "", track_push: bool = False):
    user_text = update.message.text
    save_message("user", user_text)

    state = load_state()
    hour = datetime.now(VN).hour
    energy_status = "Giờ vàng, ưu tiên Deep Work." if is_golden_hour(hour) else \
        "Năng lượng có thể thấp, ưu tiên task nhẹ hoặc nghỉ ngơi."
    instruction = f"[ENERGY ADVICE]: {energy_status}"
    if extra:
        instruction += f"\n{extra}"

    cooldown_active = False
    last_reminder = state.get("last_task_reminder_at")
    if last_reminder:
        try:
            mins_since = (datetime.now(VN) - datetime.fromisoformat(last_reminder)).total_seconds() / 60
            cooldown_active = mins_since < Config.REMINDER_GAP_MINUTES
        except Exception:
            pass

    push_count = state.get("push_count", 0)
    forced_probe = track_push and not cooldown_active and not state.get("working") and push_count >= Config.PUSH_LIMIT

    prompt = await build_prompt(interaction_type, user_text, extra_instruction=instruction)
    resp = await generate_ai_response(prompt) or "Mình đang nghe đây, bạn cứ nói tiếp đi."
    await update.message.reply_text(resp)
    save_message("bot", resp)

    if track_push and not cooldown_active and not state.get("working"):
        if forced_probe:
            reset_push()
        else:
            record_push()


async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_text = update.message.text
    intent = await detect_intent(user_text)
    hour = datetime.now(VN).hour

    try:
        if intent == "approve":
            await handle_approve(update, context)

        elif intent == "done":
            task = user_text.lower().replace("done", "").replace("xong rồi", "").replace("hoàn thành", "").strip()
            await handle_done(update, context, task)

        elif intent == "add_task":
            await handle_add_task(update, context)

        elif intent == "suggest_task":
            await handle_suggest_task(update, context)

        elif intent == "energy":
            await handle_chat(update, context, "chat",
                               extra="Người dùng đang cảm thấy mệt mỏi/nản. Xử lý theo khung Push-Probe-Pivot: "
                                     "KHÔNG mở đầu bằng câu an ủi, đi thẳng vào hỏi nguyên nhân hoặc đề xuất "
                                     "hướng xử lý phù hợp.",
                               track_push=True)

        elif intent == "working":
            update_state(
                working=True, working_since=datetime.now(VN).isoformat(),
                push_count=0, last_task_reminder_at=None, smalltalk_count=0,
            )
            await handle_chat(update, context, "chat", extra="Người dùng vừa xác nhận đang làm việc/tập trung.")

        elif intent == "smalltalk":
            state = load_state()
            update_state(smalltalk_count=state.get("smalltalk_count", 0) + 1)
            await handle_chat(update, context, "chat",
                               extra="Người dùng đang chỉ trò chuyện phiếm/đùa vui. Trả lời kiểu bạn bè, "
                                     "KHÔNG biến thành coaching.")

        elif intent == "morning":
            update_state(smalltalk_count=0)
            await handle_chat(update, context, "morning")

        elif intent == "sleep":
            if hour >= Config.SLEEP_RITUAL_HOUR:
                update_state(smalltalk_count=0)
                await handle_chat(update, context, "sleep")
            else:
                await handle_chat(update, context, "chat",
                                   extra="Người dùng nói muốn đi ngủ nhưng còn quá sớm trong ngày. "
                                         "Coi đây như tín hiệu mệt mỏi/muốn né việc — xử lý theo "
                                         "Push-Probe-Pivot, ĐỪNG chạy nghi thức đi ngủ đầy đủ.",
                                   track_push=True)

        else:
            await handle_chat(update, context, "chat", track_push=True)

    except Exception as e:
        logger.exception(e)
        prompt = await build_prompt("chat", user_text, extra_instruction="Hệ thống gặp lỗi kỹ thuật nhẹ.")
        resp = await generate_ai_response(prompt) or "Hình như mình gặp chút trục trặc, bạn nói lại được không?"
        await update.message.reply_text(resp)


# ==========================================================
# PHẦN 10: SCHEDULER
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

    prompt = await build_prompt(task_type, f"Đến giờ {task_type} rồi.")
    resp = await generate_ai_response(prompt) or f"Đến giờ {task_type} rồi, chúng ta bắt đầu chứ?"
    await context.bot.send_message(chat_id=job.chat_id, text=resp)
    save_message("bot", resp)
    mark_sent(task_type, today)

    if task_type == "sleep":
        history = get_daily_history()
        sum_prompt = (f"Hãy tóm tắt ngày hôm nay tự nhiên, chân thực (dưới 200 từ) dựa trên "
                      f"lịch sử này:\n{json.dumps(history, ensure_ascii=False)}")
        summary = await generate_ai_response(sum_prompt)
        if summary:
            msg = (f"[TÓM TẮT HÔM NAY]\n{summary}\n\n"
                   "Bạn thấy bản tóm tắt này thế nào? Nhắn \"Duyệt\" để mình lưu vào bộ nhớ nhé!")
            await context.bot.send_message(chat_id=job.chat_id, text=msg)
            save_message("bot", msg)
            update_state(last_summary_at=datetime.now(VN).isoformat(), messages_since_summary=0)


async def resummary_check(context: ContextTypes.DEFAULT_TYPE):
    """Mục 13: sau 21h, nếu đã gửi tóm tắt và có >=3 tin nhắn mới -> tóm tắt lại."""
    now = datetime.now(VN)
    if now.hour < 21:
        return
    state = load_state()
    last_summary_at = state.get("last_summary_at")
    if not last_summary_at:
        return
    try:
        ts = datetime.fromisoformat(last_summary_at)
    except Exception:
        return
    if ts.strftime("%Y-%m-%d") != now.strftime("%Y-%m-%d"):
        return
    if state.get("messages_since_summary", 0) < 3:
        return

    history = get_daily_history()
    sum_prompt = (f"Hãy tóm tắt lại ngày hôm nay (đã có thêm hội thoại mới), tự nhiên, chân thực "
                  f"(dưới 200 từ), dựa trên lịch sử này:\n{json.dumps(history, ensure_ascii=False)}")
    summary = await generate_ai_response(sum_prompt)
    if summary:
        msg = (f"[TÓM TẮT HÔM NAY - CẬP NHẬT]\n{summary}\n\n"
               "Bạn thấy bản tóm tắt này thế nào? Nhắn \"Duyệt\" để mình lưu vào bộ nhớ nhé!")
        await context.bot.send_message(chat_id=int(Config.CHAT_ID), text=msg)
        save_message("bot", msg)
        update_state(last_summary_at=datetime.now(VN).isoformat(), messages_since_summary=0)


async def send_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    """Mục 15+18: báo cáo tuần + tính điểm theo Rules Point."""
    now = datetime.now(VN)
    start_date = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    tasks = await asyncio.to_thread(get_tasks_by_date_range, start_date, end_date)
    rules_map = await asyncio.to_thread(get_rules_point_map)

    total_point = 0
    done_count = 0
    total_count = len(tasks)
    by_type = {}

    for t in tasks:
        ttype = get_task_type(t) or "Chưa phân loại"
        done = t.get("properties", {}).get("Done", {}).get("checkbox", False)
        by_type.setdefault(ttype, {"done": 0, "total": 0})
        by_type[ttype]["total"] += 1
        if done:
            by_type[ttype]["done"] += 1
            done_count += 1
            total_point += rules_map.get(ttype, {}).get("point", 0)

    lines = [f"📊 BÁO CÁO TUẦN ({start_date} → {end_date})",
             f"Hoàn thành: {done_count}/{total_count} task",
             f"Tổng điểm: {total_point}", ""]
    for ttype, stat in sorted(by_type.items(), key=lambda x: -x[1]["done"]):
        lines.append(f"- {ttype}: {stat['done']}/{stat['total']}")

    report_data = "\n".join(lines)
    prompt = await build_prompt("chat", "Đây là báo cáo tuần.",
                                 extra_instruction=f"Dữ liệu báo cáo tuần thật:\n{report_data}\n"
                                                    "Viết lại thành tin nhắn đúng persona TM, ngắn gọn, "
                                                    "có nhận xét/phản biện nếu thấy pattern đáng chú ý.")
    resp = await generate_ai_response(prompt) or report_data
    await context.bot.send_message(chat_id=int(Config.CHAT_ID), text=resp)
    save_message("bot", resp)


async def working_timeout_check(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state.get("working") or state.get("focus_session") or not state.get("working_since"):
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

    jq.run_daily(run_scheduled_job, time=time(Config.MORNING_HOUR, Config.MORNING_MINUTE, tzinfo=VN),
                 chat_id=chat_id, name="job_morning", data="morning")
    jq.run_daily(run_scheduled_job, time=time(Config.AFTERNOON_HOUR, Config.AFTERNOON_MINUTE, tzinfo=VN),
                 chat_id=chat_id, name="job_focus", data="focus")
    jq.run_daily(run_scheduled_job, time=time(Config.EVENING_HOUR, Config.EVENING_MINUTE, tzinfo=VN),
                 chat_id=chat_id, name="job_sleep", data="sleep")
    jq.run_repeating(working_timeout_check, interval=Config.WORKING_TIMEOUT_CHECK_SECONDS,
                      first=60, name="job_working_timeout")
    jq.run_repeating(resummary_check, interval=900, first=120, name="job_resummary_check")
    # Chủ nhật 21:50 GMT+7. Lưu ý: days=(6,) theo quy ước Thứ 2=0 ... Chủ nhật=6.
    # Nếu chạy sai ngày thực tế, chỉnh lại số trong "days=" cho khớp.
    jq.run_daily(send_weekly_report, time=time(21, 50, tzinfo=VN),
                 days=(6,), chat_id=chat_id, name="job_weekly_report")

    logger.info("[Scheduler] Đã đăng ký đủ: morning/focus/sleep + working_timeout + resummary + weekly_report")


# ==========================================================
# PHẦN 11: MAIN
# ==========================================================
def main():
    Config.validate()
    app = Application.builder().token(Config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("done", handle_done))
    app.add_handler(CommandHandler("focus", handle_focus))
    app.add_handler(CommandHandler("stopfocus", handle_stop_focus))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    schedule_jobs(app)

    logger.info("TM-Bot Started")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
