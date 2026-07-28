import logging
import json
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from gemini_service import ask_gemini
from notion_service import get_today_tasks, update_task_status, find_task_by_title, update_status_note
from config import Config
from datetime import datetime, timedelta, timezone

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
VN = timezone(timedelta(hours=7))

def load_file(file_name):
    try:
        with open(file_name, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

def get_daily_history():
    path = "data/history.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            history = json.load(f)
            today_str = datetime.now(VN).strftime("%Y-%m-%d")
            return [m for m in history if m.get("date") == today_str]
    return []

def save_message(user_text, bot_response, metadata=None):
    path = "data/history.json"
    history = []
    if os.path.exists(path):
        with open(path, "r") as f:
            history = json.load(f)
    
    msg_entry = {
        "date": datetime.now(VN).strftime("%Y-%m-%d"),
        "timestamp": datetime.now(VN).isoformat(),
        "user": user_text,
        "bot": bot_response
    }
    if metadata:
        msg_entry.update(metadata)
        
    history.append(msg_entry)
    os.makedirs("data", exist_ok=True)
    with open(path, "w") as f:
        json.dump(history[-200:], f, ensure_ascii=False)

async def generate_tm_response(user_input: str, interaction_type: str = "chat") -> str:
    daily_context = get_daily_history()
    yesterday_memory = load_file("data/memories.json")
    tasks = get_today_tasks()
    tasks_str = "\n".join([f"- {t['properties']['Task']['title'][0]['plain_text']}" for t in tasks]) if tasks else "Không có task nào chưa xong."
    
    system_prompt = load_file("prompts/system/tm-core.md")
    user_profile = load_file("data/user-profile.md")
    adaptive_rules = load_file("prompts/system/tm-adaptive-rules.md")

    now = datetime.now(VN)
    hour = now.hour
    
    # [NEW] Logic Energy Management cơ bản dựa trên giờ giấc
    energy_level = "High" if 8 <= hour <= 11 or 14 <= hour <= 17 else "Low"
    energy_advice = "\n[ENERGY ADVICE]: "
    if energy_level == "High":
        energy_advice += "Đây là giờ vàng, hãy ưu tiên Deep Work và các task khó nhất."
    else:
        energy_advice += "Năng lượng có thể đang thấp, hãy ưu tiên các task nhẹ nhàng hoặc nghỉ ngơi."

    # [NEW] Logic Priority Engine cơ bản
    priority_instruction = "\n[PRIORITY ENGINE]: Phân tích task dựa trên ROI và Goal của người dùng trong User Profile. Đề xuất task quan trọng nhất ngay bây giờ."

    # Logic tránh lặp Report
    report_sent = any(m.get("is_report") for m in daily_context)
    new_messages_since_report = 0
    if report_sent:
        last_report_idx = next(i for i, m in enumerate(reversed(daily_context)) if m.get("is_report"))
        new_messages_since_report = last_report_idx

    # Logic giãn cách nhắc việc
    last_reminder_time = None
    for m in reversed(daily_context):
        if m.get("is_reminder"):
            last_reminder_time = datetime.fromisoformat(m["timestamp"])
            break
    
    can_remind = True
    if last_reminder_time:
        diff = (now - last_reminder_time).total_seconds() / 3600
        if diff < 1.0:
            can_remind = False

    is_evening = hour >= 21
    extra_instruction = energy_advice + priority_instruction
    is_report = False
    
    if is_evening and interaction_type == "nhắn tin" and (not report_sent or new_messages_since_report >= 3):
        summary_prompt = f"Dựa trên lịch sử ngày hôm nay: {json.dumps(daily_context, ensure_ascii=False)}, hãy tạo một bản tóm tắt ngắn gọn để người dùng duyệt lưu vào bộ nhớ."
        summary = await ask_gemini(summary_prompt)
        extra_instruction += f"\n\n[TỰ ĐỘNG TÓM TẮT NGÀY]:\n{summary}\n\n(Hãy hỏi người dùng có muốn 'Duyệt' bản tóm tắt này không)"
        is_report = True

    is_reminder = False
    if interaction_type == "nhắn tin" and not can_remind:
        extra_instruction += "\n\n[LƯU Ý]: Vừa nhắc việc gần đây, hãy tập trung phản hồi nội dung người dùng, ĐỪNG nhắc task nữa."
    elif interaction_type == "nhắn tin" and can_remind:
        is_reminder = True

    # [NEW] Multi-Agent Roleplay: Yêu cầu Gemini đóng vai Planner + Coach
    prompt = f"""
{system_prompt}
{adaptive_rules}

THÔNG TIN NGƯỜI DÙNG:
{user_profile}

BỘ NHỚ HÔM QUA:
{yesterday_memory}

BỐI CẢNH HỆ THỐNG (Task):
{tasks_str}

LỊCH SỬ TRONG NGÀY:
{json.dumps(daily_context, ensure_ascii=False)}

NGƯỜI DÙNG VỪA {interaction_type.upper()}:
{user_input}
{extra_instruction}

NHIỆM VỤ: Phối hợp vai trò Planner (sắp xếp việc) và Coach (thúc đẩy) để đưa ra phản hồi tối ưu nhất.
"""
    response = await ask_gemini(prompt)
    save_message(user_input, response, metadata={"is_report": is_report, "is_reminder": is_reminder})
    return response

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    if user_text.lower() == "duyệt":
        history = get_daily_history()
        if history:
            report_msg = next((m["bot"] for m in reversed(history) if m.get("is_report")), None)
            if report_msg:
                with open("data/memories.json", "w") as f:
                    f.write(report_msg)
                await update.message.reply_text("TM: Đã ghi nhớ bản tóm tắt. Chúc bạn ngủ ngon!")
                return
    
    response = await generate_tm_response(user_text, "nhắn tin")
    await update.message.reply_text(response)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    response = await generate_tm_response("Chào TM, tôi bắt đầu phiên làm việc.", "bắt đầu")
    await update.message.reply_text(response)

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("TM: Gõ /done <tên task>.")
        return
    task_name = " ".join(context.args)
    task_page = find_task_by_title(task_name)
    if task_page:
        update_task_status(task_page["id"], done=True)
        response = await generate_tm_response(f"Tôi đã xong task: {task_name}.", "hoàn thành task")
        await update.message.reply_text(response)
    else:
        await update.message.reply_text(f"TM: Không tìm thấy task '{task_name}'.")

def main() -> None:
    application = Application.builder().token(Config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()
