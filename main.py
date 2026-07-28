import logging
import json
import os

from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config
from gemini_service import ask_gemini
from notion_service import (
    get_today_tasks,
    update_task_status,
    find_task_by_title,
    update_status_note,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

VN = timezone(timedelta(hours=7))


# ==========================================================
# File Utilities
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


# ==========================================================
# History
# ==========================================================

def get_daily_history():

    path = Config.HISTORY_FILE

    if not os.path.exists(path):
        return []

    try:

        with open(path, "r", encoding="utf-8") as f:

            history = json.load(f)

    except Exception:

        return []

    today = datetime.now(VN).strftime("%Y-%m-%d")

    return [

        item

        for item in history

        if item.get("date") == today

    ]


def save_message(user_text, bot_response, metadata=None):

    path = Config.HISTORY_FILE

    history = []

    if os.path.exists(path):

        try:

            with open(path, "r", encoding="utf-8") as f:

                history = json.load(f)

        except Exception:

            history = []

    message = {

        "date": datetime.now(VN).strftime("%Y-%m-%d"),

        "timestamp": datetime.now(VN).isoformat(),

        "user": user_text,

        "bot": bot_response,

    }

    if metadata:

        message.update(metadata)

    history.append(message)

    os.makedirs("data", exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:

        json.dump(

            history[-Config.HISTORY_LIMIT:],

            f,

            ensure_ascii=False,

            indent=2,

        )


# ==========================================================
# Prompt Builder
# ==========================================================

async def generate_tm_response(
    user_input: str,
    interaction_type: str = "chat",
):

    history = get_daily_history()

    yesterday_memory = load_file(Config.MEMORY_FILE)

    user_profile = load_file(Config.USER_PROFILE_FILE)

    system_prompt = load_file(
        "prompts/system/tm-core.md"
    )

    adaptive_rules = load_file(
        "prompts/system/tm-adaptive-rules.md"
    )

    tasks = get_today_tasks()

    task_lines = []

    for task in tasks:

        try:

            title = (
                task["properties"]
                ["Task"]
                ["title"][0]
                ["plain_text"]
            )

            task_lines.append(f"- {title}")

        except Exception:

            continue

    if task_lines:

        tasks_str = "\n".join(task_lines)

    else:

        tasks_str = "Không có task nào chưa hoàn thành."

    now = datetime.now(VN)

    hour = now.hour

    energy_level = (
        "High"
        if (8 <= hour <= 11) or (14 <= hour <= 17)
        else "Low"
    )

    if energy_level == "High":

        energy_advice = (
            "Đây là giờ vàng. "
            "Ưu tiên Deep Work."
        )

    else:

        energy_advice = (
            "Năng lượng đang thấp. "
            "Ưu tiên task nhẹ."
        )

    priority_instruction = (
        "Phân tích ROI của các task "
        "và đề xuất việc quan trọng nhất."
    )
        # ==========================================================
    # Report Logic
    # ==========================================================

    report_sent = any(
        item.get("is_report", False)
        for item in history
    )

    new_messages_since_report = 999

    if report_sent:

        try:

            last_report_index = max(

                index

                for index, item in enumerate(history)

                if item.get("is_report")

            )

            new_messages_since_report = (
                len(history)
                - last_report_index
                - 1
            )

        except ValueError:

            new_messages_since_report = 999

    # ==========================================================
    # Reminder Logic
    # ==========================================================

    last_reminder_time = None

    for item in reversed(history):

        if item.get("is_reminder"):

            try:

                last_reminder_time = datetime.fromisoformat(
                    item["timestamp"]
                )

            except Exception:

                last_reminder_time = None

            break

    can_remind = True

    if last_reminder_time:

        diff = (
            now - last_reminder_time
        ).total_seconds() / 3600

        if diff < 1:

            can_remind = False

    is_evening = hour >= 21

    is_report = False

    is_reminder = False

    extra_instruction = ""

    extra_instruction += (
        "\n\n[ENERGY ADVICE]\n"
        + energy_advice
    )

    extra_instruction += (
        "\n\n[PRIORITY ENGINE]\n"
        + priority_instruction
    )

    # ==========================================================
    # Daily Summary
    # ==========================================================

    if (
        is_evening
        and interaction_type == "nhắn tin"
        and (
            (not report_sent)
            or new_messages_since_report >= 3
        )
    ):

        summary_prompt = f"""
Dựa trên lịch sử dưới đây.

{json.dumps(history, ensure_ascii=False)}

Hãy tạo bản tóm tắt ngắn.

Không quá 200 từ.

Người dùng sẽ bấm "Duyệt"
để lưu vào bộ nhớ dài hạn.
"""

        summary = await ask_gemini(
            summary_prompt
        )

        extra_instruction += f"""

[TÓM TẮT HÔM NAY]

{summary}

Sau khi hiển thị,
hãy hỏi người dùng:

"Duyệt"

để lưu.
"""

        is_report = True

    # ==========================================================
    # Reminder
    # ==========================================================

    if interaction_type == "nhắn tin":

        if can_remind:

            is_reminder = True

        else:

            extra_instruction += """

[LƯU Ý]

Bạn vừa nhắc task gần đây.

Không nhắc lại.

Chỉ tập trung trả lời
đúng câu hỏi của người dùng.

"""

    # ==========================================================
    # Prompt
    # ==========================================================

    prompt = f"""

{system_prompt}

{adaptive_rules}


===========================
USER PROFILE
===========================

{user_profile}


===========================
LONG TERM MEMORY
===========================

{yesterday_memory}


===========================
TODAY TASKS
===========================

{tasks_str}


===========================
TODAY HISTORY
===========================

{json.dumps(history, ensure_ascii=False)}


===========================
CURRENT MESSAGE
===========================

Interaction:

{interaction_type}

User:

{user_input}


===========================
SYSTEM INSTRUCTION
===========================

{extra_instruction}


===========================
ROLE
===========================

Bạn đồng thời là

- Planner

- Coach

- Accountability Partner

Hãy phản hồi tự nhiên.

Không nói mình là AI.

Ưu tiên hành động.

"""

    response = await ask_gemini(
        prompt
    )

    if not response:

        response = (
            "TM: Xin lỗi, mình chưa thể phản hồi lúc này."
        )

    save_message(

        user_input,

        response,

        metadata={

            "is_report": is_report,

            "is_reminder": is_reminder,

        },

    )

    return response
    # ==========================================================
# Telegram Handlers
# ==========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.message is None:
        return

    user_text = (update.message.text or "").strip()

    # ======================================================
    # Approve Daily Summary
    # ======================================================

    if user_text.lower() == "duyệt":

        history = get_daily_history()

        report_message = None

        for item in reversed(history):

            if item.get("is_report"):

                report_message = item.get("bot")

                break

        if report_message:

            os.makedirs("data", exist_ok=True)

            with open(
                Config.MEMORY_FILE,
                "w",
                encoding="utf-8",
            ) as f:

                f.write(report_message)

            await update.message.reply_text(
                "TM: ✅ Đã lưu bản tóm tắt vào bộ nhớ."
            )

            return

    # ======================================================
    # Normal Chat
    # ======================================================

    try:

        response = await generate_tm_response(
            user_text,
            "nhắn tin",
        )

    except Exception as e:

        logger.exception(e)

        response = (
            "TM: Xin lỗi, đã xảy ra lỗi khi xử lý."
        )

    if not response:

        response = (
            "TM: Hiện tại mình chưa có phản hồi."
        )

    await update.message.reply_text(response)


# ==========================================================
# /start
# ==========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    response = await generate_tm_response(

        "Chào TM, tôi bắt đầu phiên làm việc.",

        "bắt đầu",

    )

    await update.message.reply_text(response)


# ==========================================================
# /done
# ==========================================================

async def done_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.args:

        await update.message.reply_text(
            "TM: Gõ /done <tên task>"
        )

        return

    task_name = " ".join(context.args)

    task = find_task_by_title(task_name)

    if task is None:

        await update.message.reply_text(
            f"TM: Không tìm thấy task '{task_name}'."
        )

        return

    try:

        update_task_status(
            task["id"],
            done=True,
        )

        try:

            update_status_note(
                task["id"],
                f"Hoàn thành lúc {datetime.now(VN).strftime('%H:%M')}"
            )

        except Exception:

            pass

        response = await generate_tm_response(

            f"Tôi đã hoàn thành task: {task_name}",

            "hoàn thành task",

        )

        await update.message.reply_text(response)

    except Exception as e:

        logger.exception(e)

        await update.message.reply_text(
            "TM: Không thể cập nhật Notion."
        )


# ==========================================================
# Application
# ==========================================================

def main():

    Config.validate()

    application = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "done",
            done_command,
        )
    )

    application.add_handler(

        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            handle_message,

        )

    )

    logger.info("TM-Bot started.")

    application.run_polling(
        drop_pending_updates=True
    )


# ==========================================================
# Entry
# ==========================================================

if __name__ == "__main__":

    main()
