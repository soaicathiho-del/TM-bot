
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

def load_json(file_name: str) -> dict:
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.warning(f"Error decoding JSON from {file_name}. Returning empty dict.")
        return {}
    except Exception as e:
        logger.exception(e)
        return {}

def save_json(file_name: str, data: dict):
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

def save_memory_to_file(memories: list):
    path = Config.MEMORY_FILE
    save_json(path, memories)

def append_memory(summary: str):
    memories = load_memory()
    new_memory = {
        "date": datetime.now(VN).strftime("%Y-%m-%d"),
        "summary": summary,
    }
    memories.append(new_memory)
    save_memory_to_file(memories)

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
    # Format memories for the prompt
    formatted_memories = "\n".join(
        [f"- {m["date"]}: {m["summary"]}" for m in memories]
    )
    return f"""LONG TERM MEMORY:\n{formatted_memories}"""

def load_today_history_prompt() -> str:
    history = get_daily_history()
    if not history:
        return "Không có lịch sử trò chuyện hôm nay."
    # Format history for the prompt
    formatted_history = "\n".join(
        [f"- {h["role"]}: {h["content"]}" for h in history]
    )
    return f"""TODAY'S HISTORY:\n{formatted_history}"""

def load_today_tasks_prompt() -> str:
    tasks = get_today_tasks()
    task_lines = []
    for task in tasks:
        try:
            title = (
                task["properties"]["Task"]["title"][0]["plain_text"]
            )
            task_lines.append(f"- {title}")
        except Exception:
            continue
    if task_lines:
        return f"""TODAY'S TASKS:\n{os.linesep.join(task_lines)}"""
    return "Không có task nào chưa hoàn thành."

def load_task_specific_prompt(interaction_type: str) -> str:
    if interaction_type == "morning":
        return _load_prompt_file("prompts/tasks/morning.md")
    elif interaction_type == "focus":
        return _load_prompt_file("prompts/tasks/focus.md")
    elif interaction_type == "sleep":
        return _load_prompt_file("prompts/tasks/sleep.md")
    return ""

def build_prompt(
    interaction_type: str,
    user_message: str,
    extra_instruction: str = "",
) -> str:
    system_prompt = load_system_prompt()
    adaptive_rules = load_adaptive_rules_prompt()
    task_prompt = load_task_specific_prompt(interaction_type)
    user_profile = load_user_profile_prompt()
    long_term_memory = load_long_term_memory_prompt()
    today_history = load_today_history_prompt()
    today_tasks = load_today_tasks_prompt()

    prompt_parts = [
        system_prompt,
        adaptive_rules,
    ]

    if task_prompt:
        prompt_parts.append(f"\n\nTASK PROMPT:\n{task_prompt}")

    prompt_parts.extend([
        f"\n\nUSER PROFILE:\n{user_profile}",
        f"\n\nLONG TERM MEMORY:\n{long_term_memory}",
        f"\n\nTODAY'S HISTORY:\n{today_history}",
        f"\n\nTODAY'S TASKS:\n{today_tasks}",
        f"\n\nCURRENT USER MESSAGE:\nInteraction: {interaction_type}\nUser: {user_message}",
    ])

    if extra_instruction:
        prompt_parts.append(f"\n\nSYSTEM INSTRUCTION:\n{extra_instruction}")

    # Add role instruction as per original main.py
    prompt_parts.append(
        """\n\nROLE:\nBạn đồng thời là\n- Planner\n- Coach\n- Accountability Partner\nHãy phản hồi tự nhiên.\nKhông nói mình là AI.\nƯu tiên hành động.\n"""
    )

    return "\n".join(prompt_parts)

# ==========================================================
# PHẦN 6: AI ENGINE (Nguyên tử - Atomic)
# ==========================================================
async def generate_ai_response(prompt: str) -> str:
    try:
        response = await ask_gemini(prompt)
        return response if response else ""
    except Exception as e:
        logger.exception(e)
        return ""

# ==========================================================
# PHẦN 7: NHẬN DIỆN Ý ĐỊNH (Intent Detection)
# ==========================================================
async def detect_intent(user_message: str) -> str:
    # Placeholder for actual LLM-based intent detection
    # In a real scenario, this would involve a specific prompt to Gemini
    # to classify the user_message into predefined intents.
    user_message_lower = user_message.lower()

    if "duyệt" in user_message_lower or "approve" in user_message_lower:
        return "approve"
    if "xong rồi" in user_message_lower or "hoàn thành rồi" in user_message_lower or "done" in user_message_lower:
        return "done"
    if "mệt" in user_message_lower or "đuối" in user_message_lower or "kiệt sức" in user_message_lower:
        return "energy_mode" # This intent needs further handling
    if "ngủ" in user_message_lower or "đi ngủ" in user_message_lower:
        return "sleep"
    if "chào buổi sáng" in user_message_lower:
        return "morning"
    
    # Default to chat if no specific intent is detected
    return "chat"

# ==========================================================
# PHẦN 8: NOTION (Xử lý nghiệp vụ)
# ==========================================================
async def process_done(task_name: str) -> str:
    task = find_task_by_title(task_name)
    if task is None:
        return f"Không tìm thấy task '{task_name}'."

    try:
        update_task_status(task["id"], done=True)
        update_status_note(task["id"], f"Hoàn thành lúc {datetime.now(VN).strftime('%H:%M')}")
        # Reload tasks to reflect changes for AI
        PROMPT_CACHE.pop("prompts/tasks/morning.md", None) # Clear cache for tasks
        PROMPT_CACHE.pop("prompts/tasks/focus.md", None) # Clear cache for tasks
        PROMPT_CACHE.pop("prompts/tasks/sleep.md", None) # Clear cache for tasks
        return f"Đã đánh dấu task '{task_name}' là hoàn thành trên Notion."
    except Exception as e:
        logger.exception(e)
        return "Không thể cập nhật Notion cho task này."

async def process_note(note_content: str):
    # This function is a placeholder. Actual implementation would depend on
    # how notes are structured and what Notion API calls are needed.
    logger.info(f"Processing note for Notion: {note_content}")
    pass

# ==========================================================
# PHẦN 9: TELEGRAM HANDLERS (Điều hướng logic)
# ==========================================================
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = "Chào TM, tôi bắt đầu phiên làm việc."
    prompt = build_prompt("morning", user_message)
    response_text = await generate_ai_response(prompt)
    await update.message.reply_text(response_text)
    save_message("user", user_message)
    save_message("bot", response_text)

async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("TM: Gõ /done <tên task>")
        return
    task_name = " ".join(context.args)
    notion_response = await process_done(task_name)
    
    user_message = f"Tôi đã hoàn thành task: {task_name}"
    prompt = build_prompt("done", user_message, extra_instruction=notion_response)
    response_text = await generate_ai_response(prompt)
    await update.message.reply_text(response_text)
    save_message("user", user_message)
    save_message("bot", response_text)

async def handle_approve_memory(update: Update, context: ContextTypes.DEFAULT_TYPE, summary: str):
    if approve_memory(summary):
        response_text = "TM: ✅ Đã lưu bản tóm tắt vào bộ nhớ dài hạn."
    else:
        response_text = "TM: ❌ Có lỗi xảy ra khi lưu bản tóm tắt."
    await update.message.reply_text(response_text)
    save_message("bot", response_text) # Log bot's confirmation

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    # Check for daily summary approval logic (after 21:00)
    now = datetime.now(VN)
    hour = now.hour
    if hour >= 21:
        history = get_daily_history()
        report_message = None
        for item in reversed(history):
            if item.get("role") == "bot" and "[TÓM TẮT HÔM NAY]" in item.get("content", ""):
                # Extract summary from bot's previous message
                # This is a simplified extraction, might need more robust parsing
                start_idx = item["content"].find("[TÓM TẮT HÔM NAY]") + len("[TÓM TẮT HÔM NAY]")
                end_idx = item["content"].find("Sau khi hiển thị,", start_idx)
                if start_idx != -1 and end_idx != -1:
                    report_message = item["content"][start_idx:end_idx].strip()
                break
        
        if report_message and ("duyệt" in user_text.lower() or "approve" in user_text.lower()):
            await handle_approve_memory(update, context, report_message)
            return

    # Energy Advice and Reminder Logic (moved from generate_tm_response)
    extra_instruction = ""
    energy_level = "High" if (8 <= hour <= 11) or (14 <= hour <= 17) else "Low"
    energy_advice = "Đây là giờ vàng. Ưu tiên Deep Work." if energy_level == "High" else "Năng lượng đang thấp. Ưu tiên task nhẹ."
    extra_instruction += f"\n\n[ENERGY ADVICE]\n{energy_advice}"

    # Reminder logic - simplified for now, full implementation needs more state management
    # For now, just add a note if it's too soon to remind
    # This part needs more sophisticated state management (e.g., in history or a separate state file)
    # For simplicity, I'm omitting the full can_remind logic here as it requires more complex state tracking
    # and would make this file too long. It should be handled by a dedicated function.
    
    prompt = build_prompt("chat", user_text, extra_instruction=extra_instruction)
    response_text = await generate_ai_response(prompt)
    await update.message.reply_text(response_text)
    save_message("user", user_text)
    save_message("bot", response_text)

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return

    user_text = update.message.text
    intent = await detect_intent(user_text)

    if intent == "done":
        # For /done command, context.args is already handled by CommandHandler
        # For intent 'done', we need to extract task name from user_text
        # This is a simplification; a real intent detection would provide entities
        task_name = user_text.replace("xong rồi", "").replace("hoàn thành rồi", "").replace("done", "").strip()
        if task_name:
            context.args = task_name.split()
            await handle_done(update, context)
        else:
            await update.message.reply_text("TM: Bạn muốn đánh dấu task nào là hoàn thành?")
    elif intent == "approve":
        # Summary needs to be passed to handle_approve_memory
        # This implies that the summary was generated previously and stored somewhere
        # For now, we'll assume the summary is in the last bot message if it was a report
        history = get_daily_history()
        summary_to_approve = None
        for item in reversed(history):
            if item.get("role") == "bot" and "[TÓM TẮT HÔM NAY]" in item.get("content", ""):
                start_idx = item["content"].find("[TÓM TẮT HÔM NAY]") + len("[TÓM TẮT HÔM NAY]")
                end_idx = item["content"].find("Sau khi hiển thị,", start_idx)
                if start_idx != -1 and end_idx != -1:
                    summary_to_approve = item["content"][start_idx:end_idx].strip()
                break
        
        if summary_to_approve:
            await handle_approve_memory(update, context, summary_to_approve)
        else:
            await update.message.reply_text("TM: Không tìm thấy bản tóm tắt nào để duyệt.")
    elif intent == "sleep":
        # Similar to morning/focus, but triggered by user intent
        prompt = build_prompt("sleep", user_text)
        response_text = await generate_ai_response(prompt)
        await update.message.reply_text(response_text)
        save_message("user", user_text)
        save_message("bot", response_text)
    elif intent == "morning":
        prompt = build_prompt("morning", user_text)
        response_text = await generate_ai_response(prompt)
        await update.message.reply_text(response_text)
        save_message("user", user_text)
        save_message("bot", response_text)
    elif intent == "energy_mode":
        # This intent needs a specific response or action, currently not fully defined in spec
        prompt = build_prompt("chat", user_text, extra_instruction="Người dùng đang cảm thấy mệt mỏi. Hãy đưa ra lời khuyên về năng lượng.")
        response_text = await generate_ai_response(prompt)
        await update.message.reply_text(response_text)
        save_message("user", user_text)
        save_message("bot", response_text)
    else: # Default to chat
        await handle_chat_message(update, context)

# ==========================================================
# PHẦN 10: LẬP LỊCH (Scheduler)
# ==========================================================
async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_message = "Đã đến giờ buổi sáng. Bắt đầu ngày mới!"
    prompt = build_prompt("morning", user_message)
    response_text = await generate_ai_response(prompt)
    # Assuming job.chat_id is set when scheduling
    if job and job.chat_id:
        await context.bot.send_message(chat_id=job.chat_id, text=response_text)
        save_message("bot", response_text) # Save bot's message

async def focus_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_message = "Đã đến giờ tập trung. Hãy kiểm tra các task."
    prompt = build_prompt("focus", user_message)
    response_text = await generate_ai_response(prompt)
    if job and job.chat_id:
        await context.bot.send_message(chat_id=job.chat_id, text=response_text)
        save_message("bot", response_text) # Save bot's message

async def evening_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_message = "Đã đến giờ buổi tối. Hãy nhìn lại ngày hôm nay."
    prompt = build_prompt("sleep", user_message)
    response_text = await generate_ai_response(prompt)
    if job and job.chat_id:
        await context.bot.send_message(chat_id=job.chat_id, text=response_text)
        save_message("bot", response_text) # Save bot's message

    # Daily Summary and Approval Logic (after 21:00)
    now = datetime.now(VN)
    hour = now.hour
    if hour >= 21:
        history = get_daily_history()
        if history:
            summary_prompt = f"""
Dựa trên lịch sử trò chuyện hôm nay:
{json.dumps(history, ensure_ascii=False, indent=2)}

Hãy tạo bản tóm tắt ngắn gọn về các hoạt động và kết quả trong ngày.
Không quá 200 từ. Người dùng sẽ bấm "Duyệt" để lưu vào bộ nhớ dài hạn.
"""
            summary = await generate_ai_response(summary_prompt)
            if summary and job and job.chat_id:
                summary_message = f"""
[TÓM TẮT HÔM NAY]
{summary}

Sau khi hiển thị,
hãy hỏi người dùng:
"Duyệt" để lưu.
"""
                await context.bot.send_message(chat_id=job.chat_id, text=summary_message)
                save_message("bot", summary_message) # Save bot's summary message

# ==========================================================
# PHẦN 11: HÀM MAIN & ENTRY POINT
# ==========================================================
def main():
    Config.validate()

    application = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .build()
    )

    # Get the job queue
    job_queue = application.job_queue

    # Add handlers
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("done", handle_done))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    # Schedule jobs (using a dummy chat_id for now, in a real app this would be dynamic)
    # You would need a way to get the user's chat_id to send scheduled messages
    # For demonstration, let's assume Config.CHAT_ID exists and is the target chat_id
    if hasattr(Config, 'CHAT_ID') and Config.CHAT_ID:
        # Schedule morning job
        job_queue.run_daily(morning_job, time=Config.MORNING_TIME, chat_id=Config.CHAT_ID, name="morning_job")
        logger.info(f"Scheduled morning job at {Config.MORNING_TIME}")

        # Schedule focus job
        job_queue.run_daily(focus_job, time=Config.FOCUS_TIME, chat_id=Config.CHAT_ID, name="focus_job")
        logger.info(f"Scheduled focus job at {Config.FOCUS_TIME}")

        # Schedule evening job
        job_queue.run_daily(evening_job, time=Config.EVENING_TIME, chat_id=Config.CHAT_ID, name="evening_job")
        logger.info(f"Scheduled evening job at {Config.EVENING_TIME}")
    else:
        logger.warning("Config.CHAT_ID not set. Scheduled jobs will not be sent.")

    logger.info("TM-Bot started.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
