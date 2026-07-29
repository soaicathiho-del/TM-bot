
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
    formatted_history = "\n".join(
        [f"- {h['role'].upper()}: {h['content']}" for h in history]
    )
    return f"TODAY'S HISTORY:\n{formatted_history}"

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

def build_prompt(interaction_type: str, user_message: str, extra_instruction: str = "") -> str:
    parts = [
        load_system_prompt(),
        load_adaptive_rules_prompt(),
        load_task_specific_prompt(interaction_type),
        load_user_profile_prompt(),
        load_long_term_memory_prompt(),
        load_today_history_prompt(),
        load_today_tasks_prompt(),
        f"CURRENT USER MESSAGE:\nInteraction: {interaction_type}\nUser: {user_message}",
        f"SYSTEM INSTRUCTION:\n{extra_instruction}" if extra_instruction else "",
        "ROLE:\nBạn đồng thời là Planner, Coach, Accountability Partner. Hãy phản hồi tự nhiên, gần gũi như con người. Tuyệt đối không nói mình là AI. Ưu tiên hành động và động viên người dùng."
    ]
    return "\n\n".join([p for p in parts if p])

# ==========================================================
# PHẦN 6: AI ENGINE (Atomic)
# ==========================================================
async def generate_ai_response(prompt: str) -> str:
    try:
        response = await ask_gemini(prompt)
        return response.strip() if response else ""
    except Exception as e:
        logger.error(f"AI Engine Error: {e}")
        return ""

# ==========================================================
# PHẦN 7: NHẬN DIỆN Ý ĐỊNH (Intent Detection)
# ==========================================================
async def detect_intent(user_message: str) -> str:
    text = user_message.lower()
    if any(kw in text for kw in ["duyệt", "approve"]): return "approve"
    if any(kw in text for kw in ["xong rồi", "hoàn thành", "done"]): return "done"
    if any(kw in text for kw in ["mệt", "đuối", "kiệt sức", "nản"]): return "energy"
    if any(kw in text for kw in ["ngủ", "đi ngủ", "ngủ đây"]): return "sleep"
    if any(kw in text for kw in ["chào buổi sáng", "morning"]): return "morning"
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
    
    # Energy Advice logic
    hour = datetime.now(VN).hour
    energy_status = "Giờ vàng, ưu tiên Deep Work." if (8<=hour<=11 or 14<=hour<=17) else "Năng lượng có thể thấp, ưu tiên task nhẹ hoặc nghỉ ngơi."
    
    instruction = f"[ENERGY ADVICE]: {energy_status}"
    if extra: instruction += f"\n{extra}"
    
    prompt = build_prompt(interaction_type, user_text, extra_instruction=instruction)
    resp = await generate_ai_response(prompt) or "Mình đang nghe đây, bạn cứ nói tiếp đi."
    await update.message.reply_text(resp)
    save_message("bot", resp)

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    user_text = update.message.text
    intent = await detect_intent(user_text)
    
    try:
        if intent == "approve": 
            await handle_approve(update, context)
        elif intent == "done":
            task = user_text.lower().replace("done", "").replace("xong rồi", "").replace("hoàn thành", "").strip()
            await handle_done(update, context, task)
        elif intent == "energy":
            await handle_chat(update, context, "chat", extra="Người dùng đang cảm thấy mệt mỏi/nản. Hãy phản hồi như một người bạn đồng hành, thấu hiểu và đưa ra lời khuyên phù hợp.")
        elif intent in ["morning", "sleep"]: 
            await handle_chat(update, context, intent)
        else: 
            await handle_chat(update, context, "chat")
    except Exception as e:
        logger.exception(e)
        prompt = build_prompt("chat", user_text, extra_instruction="Hệ thống gặp lỗi kỹ thuật nhẹ.")
        resp = await generate_ai_response(prompt) or "Hình như mình gặp chút trục trặc, bạn nói lại được không?"
        await update.message.reply_text(resp)

# ==========================================================
# PHẦN 10: SCHEDULER
# ==========================================================
async def run_scheduled_job(context: ContextTypes.DEFAULT_TYPE, task_type: str):
    job = context.job
    if not job or not job.chat_id: return
    
    prompt = build_prompt(task_type, f"Đến giờ {task_type} rồi.")
    resp = await generate_ai_response(prompt) or f"Đến giờ {task_type} rồi, chúng ta bắt đầu chứ?"
    await context.bot.send_message(chat_id=job.chat_id, text=resp)
    save_message("bot", resp)
    
    if task_type == "sleep" and datetime.now(VN).hour >= 21:
        history = get_daily_history()
        sum_prompt = f"Hãy tóm tắt ngày hôm nay một cách tự nhiên, chân thực (dưới 200 từ) dựa trên lịch sử này:\n{json.dumps(history, ensure_ascii=False)}"
        summary = await generate_ai_response(sum_prompt)
        if summary:
            msg = f"[TÓM TẮT HÔM NAY]\n{summary}\n\nBạn thấy bản tóm tắt này thế nào? Nhắn \"Duyệt\" để mình lưu vào bộ nhớ nhé!"
            await context.bot.send_message(chat_id=job.chat_id, text=msg)
            save_message("bot", msg)

# ==========================================================
# PHẦN 11: MAIN
# ==========================================================
def main():
    Config.validate()
    app = Application.builder().token(Config.BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("done", handle_done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))
    

    logger.info("TM-Bot Started with Human-like personality.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
