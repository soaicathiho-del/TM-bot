import asyncio
import logging
import json
import os
import re
import unicodedata
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
    "state_date": None,
    "working": False,
    "working_since": None,
    "focus_session": None,
    "last_task_reminder_at": None,
    "push_count": 0,
    "smalltalk_count": 0,
    "last_sent": {},
    "last_summary_at": None,
    "messages_since_summary": 0,
    "pending_task": None,   # {"title": str, "date": str, "guess_type": str|None} khi chờ xác nhận Type
    "custom_reminders": [], # [{"due_at": iso, "text": str, "sent": bool}, ...]
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


def strip_diacritics(text: str) -> str:
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return text.replace('đ', 'd').replace('Đ', 'D')


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

    if role == "user":
        state = load_state()
        if state.get("last_summary_at"):
            update_state(messages_since_summary=state.get("messages_since_summary", 0) + 1)


# ==========================================================
# PHẦN 3.5: SESSION STATE
# ==========================================================
def load_state() -> dict:
    """
    Đọc state dùng chung. Tự động reset các field theo-NGÀY (working, push_count,
    focus_session, smalltalk_count, last_task_reminder_at) khi phát hiện sang ngày mới.
    last_sent / last_summary_at KHÔNG bị đụng vì bản thân chúng đã tự dedup theo ngày.
    """
    path = Config.STATE_FILE
    today = datetime.now(VN).strftime("%Y-%m-%d")
    try:
        if not os.path.exists(path):
            state = dict(DEFAULT_STATE)
            state["state_date"] = today
            save_state(state)
            return state

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_STATE)
        merged.update(data)

        if merged.get("state_date") != today:
            logger.info(f"[State] Sang ngày mới ({merged.get('state_date')} -> {today}), "
                        f"reset working/push_count/focus_session/smalltalk_count.")
            merged.update({
                "state_date": today,
                "working": False,
                "working_since": None,
                "focus_session": None,
                "last_task_reminder_at": None,
                "push_count": 0,
                "smalltalk_count": 0,
            })
            save_state(merged)

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
            "không coaching. Chỉ đồng hành nhẹ nếu họ chủ động nói chuyện. "
            "Luôn trả lời đúng câu hiện tại trước; không xúc phạm, không quy chụp."
        )
    return (
        "ROLE:\nBạn đồng thời là Planner, Coach, Accountability Partner. "
        "Phản hồi tự nhiên, gần gũi như con người. Tuyệt đối không nói mình là AI. "
        "Ưu tiên trả lời đúng câu hỏi/cảm xúc hiện tại trước; chỉ coaching khi có liên quan. "
        "Được thẳng nhưng không xúc phạm, không quy chụp và không ra mệnh lệnh vô cớ."
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
    """Ask Gemini once and return an empty string when the AI layer is unavailable.

    The Gemini service owns model fallback. Retrying here caused duplicate requests
    and made quota errors look like a broken conversation.
    """
    try:
        response = await ask_gemini(prompt)
        return response.strip() if response else ""
    except Exception as e:
        logger.error("AI Engine Error: %s", e)
        return ""


# ==========================================================
# PHẦN 7: NHẬN DIỆN Ý ĐỊNH
# ==========================================================
WORKING_KEYWORDS = [
    "đang làm", "đang viết", "đang code", "đang tập trung",
    "để t làm đã", "để tao làm đã", "ok đang làm", "ừ đang làm", "đang cày",
    "làm việc", "làm cho xong", "đang xử lý", "đang chạy task", "làm nốt",
]
SMALLTALK_KEYWORDS = [
    "haha", "hihi", ":))", ":v", "=))", "á đù", "ạ đù", "hehe", "khakha", ":)))",
]
TASK_ADD_KEYWORDS = ["thêm task", "tạo task", "note task", "ghi task mới", "thêm việc", "tạo việc mới"]
SUGGEST_KEYWORDS = ["nên làm gì", "làm gì trước", "task nào ưu tiên", "việc gì quan trọng nhất", "gợi ý task"]
REMIND_KEYWORDS = ["nhắc t", "nhắc tôi", "nhắc mình", "nhớ nhắc", "nhắc lại giúp", "nhắc giúp"]
DONE_KEYWORDS = ["xong rồi", "hoàn thành", "done"]
FEEDBACK_KEYWORDS = [
    "sai", "nhận nhầm", "không có yêu cầu", "ko có yêu cầu",
    "đừng tạo task", "không phải lệnh", "check lại flow", "check lại quy trình",
    "đề xuất fix bot", "lỗi bot", "code đang sai",
]

# Từ đệm cần loại bỏ khi so khớp tên task / suy đoán Type (KHÔNG phải nội dung thật của task)
STOPWORDS_VN = {
    "toi", "ban", "cho", "va", "la", "cua", "de", "mot", "cai", "nay", "hom",
    "ngay", "mai", "them", "task", "tao", "viec", "moi", "gio", "luon", "voi",
    "tu", "trong", "duoc", "se", "da", "dang", "co", "khong", "gium", "giup",
    "kia", "do", "nen", "ma", "lai", "ra", "vao", "roi", "day", "nhe", "nha",
    "xong", "hoan", "thanh", "done", "nhi", "oi", "di", "a", "ha", "ah",
}


def is_feedback_message(user_message: str) -> bool:
    text = strip_diacritics(user_message.lower())
    return any(strip_diacritics(keyword) in text for keyword in FEEDBACK_KEYWORDS)


async def detect_intent(user_message: str) -> str:
    text = user_message.lower()
    # Feedback/bug report luôn được ưu tiên trước action keyword. Việc một câu
    # có chữ "task", "deadline" hoặc "nhắc" không đủ để tạo dữ liệu.
    if is_feedback_message(user_message): return "feedback"
    if any(kw in text for kw in ["duyệt", "approve"]): return "approve"
    if any(kw in text for kw in DONE_KEYWORDS): return "done"
    if any(kw in text for kw in TASK_ADD_KEYWORDS): return "add_task"
    if any(kw in text for kw in SUGGEST_KEYWORDS): return "suggest_task"
    if any(kw in text for kw in REMIND_KEYWORDS): return "remind_me"
    if any(kw in text for kw in ["mệt", "đuối", "kiệt sức", "nản"]): return "energy"
    if any(kw in text for kw in ["ngủ", "đi ngủ", "ngủ đây"]): return "sleep"
    if any(kw in text for kw in ["chào buổi sáng", "morning"]): return "morning"
    if any(kw in text for kw in WORKING_KEYWORDS): return "working"
    if any(kw in text for kw in SMALLTALK_KEYWORDS): return "smalltalk"
    return "chat"


# ==========================================================
# PHẦN 8: NOTION HELPERS (tên task, Type, Date)
# ==========================================================
def extract_task_title(task_page: dict) -> str:
    try:
        return task_page["properties"]["Task"]["title"][0]["plain_text"]
    except Exception:
        return "Unknown"


async def process_done_page(task_page: dict) -> str:
    title = extract_task_title(task_page)
    try:
        status_ok = await asyncio.to_thread(update_task_status, task_page["id"], True)
        note_ok = await asyncio.to_thread(
            update_status_note,
            task_page["id"],
            f"Hoàn thành lúc {datetime.now(VN).strftime('%H:%M')}",
        )
        if status_ok and note_ok:
            return f"Đã cập nhật Done và Status Note cho task '{title}' trên Notion."
        if status_ok:
            return (f"Đã cập nhật Done cho task '{title}' trên Notion, "
                    "nhưng Status Note chưa cập nhật được.")
        return f"Chưa cập nhật được trạng thái task '{title}' trên Notion."
    except Exception as e:
        logger.exception("Notion error while completing task %s", title)
        return f"Chưa cập nhật được task '{title}' trên Notion."


def _tokenize(text: str) -> set:
    norm = strip_diacritics(text.lower())
    words = re.findall(r'[a-z0-9]+', norm)
    return {w for w in words if w not in STOPWORDS_VN and len(w) > 1}


def fuzzy_match_task(text: str, tasks: list):
    """
    So khớp câu nói của user với tên các task hôm nay bằng overlap từ khóa
    (bỏ từ đệm), KHÔNG đòi khớp nguyên cụm như Notion 'contains' filter.
    Trả về (best_task, tie_candidates):
      - (task, []) nếu match rõ 1 task duy nhất.
      - (None, [task1, task2,...]) nếu bị tie (nhiều task cùng điểm cao nhất).
      - (None, []) nếu không khớp task nào.
    """
    target_tokens = _tokenize(text)
    if not target_tokens:
        return None, []

    scored = []
    for task in tasks:
        title = extract_task_title(task)
        title_tokens = _tokenize(title)
        overlap = target_tokens & title_tokens
        if overlap:
            scored.append((task, len(overlap)))

    if not scored:
        return None, []

    scored.sort(key=lambda x: -x[1])
    top_score = scored[0][1]
    top_matches = [t for t, s in scored if s == top_score]

    if len(top_matches) == 1:
        return top_matches[0], []
    return None, top_matches


def match_task_type(text: str, rules_map: dict):
    """Khớp Type RÕ RÀNG (user tự nói số/tên) -> (type_name, matched_by) hoặc (None, None)."""
    lower = text.lower()
    norm = strip_diacritics(lower)

    m = re.search(r'\b(?:type|loai|loại)\s*[:#]?\s*(\d)\b', norm)
    if m:
        num = int(m.group(1))
        for type_name, info in rules_map.items():
            if info.get("priority") == num:
                return type_name, "priority_number"

    for type_name in rules_map.keys():
        if type_name.lower() in lower:
            return type_name, "exact"

    for type_name in rules_map.keys():
        type_norm = strip_diacritics(type_name.lower())
        if type_norm in norm:
            return type_name, "fuzzy_no_diacritics"

    return None, None


def guess_type_by_description(text: str, rules_map: dict):
    """
    Suy đoán Type dựa trên overlap từ khóa giữa tiêu đề task và (Tên Type + Description).
    Trả về (type_name, description, score) hoặc (None, None, 0) nếu không đoán được / bị tie.
    """
    target_tokens = _tokenize(text)
    if not target_tokens:
        return None, None, 0

    scored = []
    for type_name, info in rules_map.items():
        desc = info.get("description", "") or ""
        type_tokens = _tokenize(type_name) | _tokenize(desc)
        overlap = target_tokens & type_tokens
        if overlap:
            scored.append((type_name, desc, len(overlap)))

    if not scored:
        return None, None, 0

    scored.sort(key=lambda x: -x[2])
    best = scored[0]
    if len(scored) > 1 and scored[1][2] == best[2]:
        return None, None, 0  # 2 loại khớp ngang nhau -> không đoán bừa, hỏi thẳng

    return best


def parse_task_date(text: str) -> str:
    lower = strip_diacritics(text.lower())
    today = datetime.now(VN)
    if re.search(r'\bngay\s+mot\b', lower):
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    if "ngay mai" in lower or re.search(r'\bmai\b', lower):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if "hom qua" in lower:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    return today.strftime("%Y-%m-%d")


def parse_reminder_datetime(text: str):
    """
    Hiểu giờ hẹn nhắc lại. Hỗ trợ:
    - Tương đối: "30 phút nữa", "2 tiếng nữa"
    - Giờ cụ thể: "15h", "15h30", "3h chiều" (tự +12 nếu <=7h và có chiều/tối)
    - Mơ hồ: sáng/trưa/chiều/tối/khuya -> quy ước 8h/12h/15h/19h/22h
    - "mai" -> cộng thêm 1 ngày
    Nếu không nhận diện được gì -> mặc định 2 tiếng nữa (báo rõ cho user biết để họ tự sửa nếu sai).
    """
    norm = strip_diacritics(text.lower())
    now = datetime.now(VN)

    m = re.search(r'(\d+)\s*phut\s*nua', norm)
    if m:
        return now + timedelta(minutes=int(m.group(1)))
    m = re.search(r'(\d+)\s*(tieng|gio)\s*nua', norm)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    hour = minute = None
    m = re.search(r'\b(\d{1,2})\s*h\s*(\d{0,2})\b', norm)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        if hour <= 7 and ("chieu" in norm or "toi" in norm):
            hour += 12

    if hour is None:
        for kw, h in [("sang", 8), ("trua", 12), ("chieu", 15), ("toi", 19), ("khuya", 22)]:
            if kw in norm:
                hour, minute = h, 0
                break

    if hour is None:
        return now + timedelta(hours=2)

    due = now.replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
    if "mai" in norm:
        due += timedelta(days=1)
    elif due <= now:
        due += timedelta(days=1)
    return due


async def handle_remind_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    save_message("user", text)

    due_dt = parse_reminder_datetime(text)
    state = load_state()
    reminders = state.get("custom_reminders", [])
    reminders.append({"due_at": due_dt.isoformat(), "text": text, "sent": False})
    update_state(custom_reminders=reminders)

    due_str = due_dt.strftime("%H:%M %d/%m")
    msg = f"Đã ghi nhớ, mình sẽ nhắc bạn khoảng {due_str}: \"{text}\"\n(nếu giờ này sai ý bạn, nhắn lại rõ giờ hơn nhé)."
    await update.message.reply_text(msg)
    save_message("bot", msg)


def format_type_options(rules_map: dict) -> str:
    items = sorted(rules_map.items(),
                    key=lambda x: (x[1].get("priority") if x[1].get("priority") is not None else 999))
    return "\n".join(f"{info.get('priority', '?')}. {name}" for name, info in items)


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


async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str = ""):
    """
    raw_text: phần câu còn lại sau khi đã bóc từ khóa 'done/xong rồi/hoàn thành' (có thể còn
    lẫn từ đệm như 'task', 'nhé', 'r'...). Dùng fuzzy_match_task để tìm đúng task, KHÔNG đòi
    khớp nguyên cụm.
    """
    if not raw_text and context.args:
        raw_text = " ".join(context.args)

    today_tasks = await asyncio.to_thread(get_today_tasks)

    matched_task = None
    tie_candidates = []

    if not raw_text.strip():
        if len(today_tasks) == 1:
            matched_task = today_tasks[0]
        elif len(today_tasks) > 1:
            tie_candidates = today_tasks
    else:
        matched_task, tie_candidates = fuzzy_match_task(raw_text, today_tasks)

    if tie_candidates:
        titles = [extract_task_title(t) for t in tie_candidates]
        msg = "Bạn vừa xong task nào trong số này?\n" + "\n".join(f"- {t}" for t in titles)
        save_message("user", update.message.text)
        await update.message.reply_text(msg)
        save_message("bot", msg)
        return

    save_message("user", update.message.text)
    cancel_focus_job(context, update.effective_chat.id)
    reset_work_and_push()

    if matched_task:
        title = extract_task_title(matched_task)
        info = await process_done_page(matched_task)
        prompt = await build_prompt("done", f"Tôi đã xong task {title}", extra_instruction=info)
        resp = await generate_ai_response(prompt) or f"Ghi nhận nhé! Bạn làm tốt khi xong {title}."
    else:
        # Không tìm thấy task khớp trong Notion -> vẫn ghi nhận lời user, không bịa lý do
        info = (f"Không tìm thấy task nào trong danh sách Notion hôm nay khớp với "
                f"'{raw_text.strip()}'. Có thể task đã Done từ trước, hoặc chưa có trên Notion.")
        prompt = await build_prompt("chat", update.message.text, extra_instruction=info)
        resp = await generate_ai_response(prompt) or (
            f"Ghi nhận là bạn đã xong việc, nhưng mình không thấy task khớp trên Notion — "
            f"kiểm tra lại tên task giúp mình nhé."
        )

    await update.message.reply_text(resp)
    save_message("bot", resp)


async def _finalize_create_task(update: Update, title: str, task_type: str, task_date: str):
    page = await asyncio.to_thread(create_task, title, task_type, task_date)
    if page:
        info = f"Đã tạo task trên Notion:\n- Task: {title}\n- Type: {task_type}\n- Date: {task_date}"
    else:
        info = f"Lỗi: không tạo được task '{title}' trên Notion."
    prompt = await build_prompt("chat", f"Thêm task {title}", extra_instruction=info)
    resp = await generate_ai_response(prompt) or info
    await update.message.reply_text(resp)
    save_message("bot", resp)


async def handle_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse a task and require deadline confirmation before creating anything."""
    text = update.message.text
    lower = text.lower()
    trigger = next((kw for kw in TASK_ADD_KEYWORDS if kw in lower), None)
    idx = (lower.find(trigger) + len(trigger)) if trigger else 0
    title = text[idx:].strip(" :-").strip()

    save_message("user", text)
    if not title:
        msg = "Bạn muốn thêm task gì? Nhắn tên task nhé."
        await update.message.reply_text(msg)
        save_message("bot", msg)
        return

    rules_map = await asyncio.to_thread(get_rules_point_map)
    matched_type, matched_by = match_task_type(text, rules_map)
    task_date = parse_task_date(text)

    if matched_by == "priority_number":
        title = re.sub(
            r'\b(?:type|loai|loại)\s*[:#]?\s*\d\b',
            '',
            title,
            flags=re.IGNORECASE,
        ).strip(" ,-")

    guess_type, guess_desc, _ = guess_type_by_description(title, rules_map)
    if matched_type:
        guess_type = matched_type
        guess_desc = "bạn đã chỉ rõ loại task"

    pending = {
        "stage": "confirm_deadline",
        "title": title,
        "date": task_date,
        "guess_type": guess_type,
        "created_at": datetime.now(VN).isoformat(),
    }
    update_state(pending_task=pending)

    due_text = datetime.strptime(task_date, "%Y-%m-%d").strftime("%d/%m/%Y")
    msg = (
        f"Mình hiểu task là **{title}**, deadline **{due_text}**.\n"
        "Xác nhận deadline này đúng không? Nhắn `đúng`, hoặc nói lại ngày muốn sửa."
    )
    await update.message.reply_text(msg)
    save_message("bot", msg)


async def handle_task_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle the next message for deadline/type confirmation."""
    text = update.message.text
    norm = strip_diacritics(text.lower()).strip()
    state = load_state()
    pending = state.get("pending_task")
    if not pending:
        return False

    save_message("user", text)

    if any(kw in norm for kw in ["huy", "bo qua", "cancel", "thoi khoi"]):
        update_state(pending_task=None)
        msg = "Đã hủy, không tạo task này."
        await update.message.reply_text(msg)
        save_message("bot", msg)
        return True

    stage = pending.get("stage", "type")
    if stage == "confirm_deadline":
        has_new_date = any(word in norm for word in ("hom nay", "ngay mai", "mai", "ngay mot"))
        if has_new_date:
            pending["date"] = parse_task_date(text)

        confirmed = (
            norm in {"dung", "ok", "oke", "duoc", "xac nhan", "yes", "u", "uh"}
            or "xac nhan" in norm
            or norm.startswith("dung ")
        )
        if not confirmed and not has_new_date:
            msg = "Mình chưa xác nhận được deadline. Nhắn `đúng`, `hôm nay`, `ngày mai`, hoặc `hủy`."
            await update.message.reply_text(msg)
            save_message("bot", msg)
            return True
        if has_new_date and not confirmed:
            due_text = datetime.strptime(pending["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
            update_state(pending_task=pending)
            msg = f"Đã đổi deadline thành **{due_text}**. Xác nhận đúng ngày này không?"
            await update.message.reply_text(msg)
            save_message("bot", msg)
            return True

        rules_map = await asyncio.to_thread(get_rules_point_map)
        if pending.get("guess_type"):
            pending["stage"] = "confirm_type"
            update_state(pending_task=pending)
            options_text = format_type_options(rules_map)
            msg = (
                f"Deadline đã xác nhận. Mình đoán task thuộc loại **{pending['guess_type']}**. "
                f"Đúng không? Nhắn `đúng` hoặc chọn số loại:\n{options_text}"
            )
            await update.message.reply_text(msg)
            save_message("bot", msg)
            return True

        pending["stage"] = "type"
        update_state(pending_task=pending)
        options_text = format_type_options(rules_map)
        msg = f"Deadline đã xác nhận. Task thuộc loại nào? Chọn đúng 1 số nhé:\n{options_text}"
        await update.message.reply_text(msg)
        save_message("bot", msg)
        return True

    rules_map = await asyncio.to_thread(get_rules_point_map)
    final_type = None
    m = re.search(r'\b([1-6])\b', norm)
    if m:
        num = int(m.group(1))
        for type_name, info in rules_map.items():
            if info.get("priority") == num:
                final_type = type_name
                break

    if not final_type and pending.get("guess_type"):
        if (re.search(r'\b(dung|chuan|yes)\b', norm) or "xac nhan" in norm
                or norm in ("ok", "oke", "u", "duoc", "duoc roi")):
            final_type = pending["guess_type"]

    if not final_type:
        matched_type, _ = match_task_type(text, rules_map)
        final_type = matched_type

    if not final_type:
        options_text = format_type_options(rules_map)
        msg = f"Chưa rõ loại task. Chọn đúng 1 số nhé:\n{options_text}"
        await update.message.reply_text(msg)
        save_message("bot", msg)
        return True

    update_state(pending_task=None)
    await _finalize_create_task(update, pending["title"], final_type, pending["date"])
    return True


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
        title = extract_task_title(t)
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
    resp = await generate_ai_response(prompt) or (
        "Mình đang gặp lỗi kết nối với bộ não Gemini nên chưa trả lời trọn vẹn được. "
        "Tin nhắn của bạn chưa làm thay đổi task hay reminder nào."
    )
    await update.message.reply_text(resp)
    save_message("bot", resp)

    if track_push and not cooldown_active and not state.get("working"):
        if forced_probe:
            reset_push()
        else:
            record_push()


def _strip_done_keywords(text: str) -> str:
    lower = text.lower()
    for kw in DONE_KEYWORDS:
        lower = lower.replace(kw, "")
    return lower.strip()


async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text
    state = load_state()
    # Một phản hồi sửa lỗi/hủy yêu cầu không được dùng làm câu trả lời cho
    # pending category/deadline của task trước đó.
    if state.get("pending_task") and is_feedback_message(user_text):
        update_state(pending_task=None)
    elif state.get("pending_task"):
        handled = await handle_task_confirmation(update, context)
        if handled:
            return
    intent = await detect_intent(user_text)
    hour = datetime.now(VN).hour

    try:
        if intent == "feedback":
            await handle_chat(
                update,
                context,
                "chat",
                extra=("Đây là feedback/bug report của người dùng. Xác nhận điều gì sai, "
                       "tóm tắt đề xuất sửa, không tạo task/reminder và không đổ lỗi cho người dùng."),
                track_push=False,
            )

        elif intent == "approve":
            await handle_approve(update, context)

        elif intent == "done":
            raw_text = _strip_done_keywords(user_text)
            await handle_done(update, context, raw_text)

        elif intent == "add_task":
            await handle_add_task(update, context)

        elif intent == "suggest_task":
            await handle_suggest_task(update, context)

        elif intent == "remind_me":
            await handle_remind_me(update, context)

        elif intent == "energy":
            await handle_chat(update, context, "chat",
                               extra="Người dùng đang cảm thấy mệt mỏi/nản. Trả lời đồng cảm, ngắn gọn, "
                                     "hỏi họ muốn nghỉ, nói chuyện hay chọn một bước rất nhỏ. Không mắng, "
                                     "không tự tạo task và không ép Deep Work.",
                               track_push=False)

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
            await handle_chat(update, context, "chat", track_push=False)

    except Exception as e:
        logger.exception("Message handling error: %s", e)
        # Không gọi Gemini lần hai tại đây: nếu lỗi nằm ở AI/quota, router không
        # được tạo thêm một vòng lỗi. Trả lời thẳng để người dùng biết trạng thái.
        await update.message.reply_text(
            "Mình gặp lỗi khi xử lý tin này nên chưa thực hiện hành động nào. "
            "Bạn gửi lại câu ngắn hơn giúp mình nhé."
        )


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


async def check_custom_reminders(context: ContextTypes.DEFAULT_TYPE):
    """
    Quét data/session_state.json mỗi 5 phút để gửi các lời nhắc "nhắc t lúc..." đến hạn.
    Đọc từ file (không dùng JobQueue.run_once) để không bị mất lời nhắc nếu worker restart
    giữa chừng — đây chính là lỗi đã gặp thực tế (bot nói "đã note" nhưng không có gì lưu lại).
    """
    state = load_state()
    reminders = state.get("custom_reminders", [])
    if not reminders:
        return

    now = datetime.now(VN)
    changed = False
    remaining = []

    for r in reminders:
        if r.get("sent"):
            try:
                due = datetime.fromisoformat(r["due_at"])
                if now - due > timedelta(days=1):
                    changed = True
                    continue  # dọn rác lời nhắc cũ đã gửi quá 1 ngày
            except Exception:
                pass
            remaining.append(r)
            continue

        try:
            due = datetime.fromisoformat(r["due_at"])
        except Exception:
            remaining.append(r)
            continue

        if now >= due:
            prompt = await build_prompt(
                "chat", r["text"],
                extra_instruction=(f"Đây là lời nhắc người dùng tự đặt trước đó lúc "
                                    f"{due.strftime('%H:%M %d/%m')}: \"{r['text']}\". "
                                    "Nhắc lại đúng tinh thần TM, ngắn gọn, thúc đẩy hành động."),
            )
            resp = await generate_ai_response(prompt) or f"Nhắc bạn: {r['text']}"
            await context.bot.send_message(chat_id=int(Config.CHAT_ID), text=resp)
            save_message("bot", resp)
            r["sent"] = True
            changed = True

        remaining.append(r)

    if changed:
        update_state(custom_reminders=remaining)


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
    jq.run_repeating(check_custom_reminders, interval=300, first=45, name="job_custom_reminders")
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
