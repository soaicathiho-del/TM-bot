from gemini_service import test_gemini
from telegram_service import send_message


def main():
    """
    Chạy bot.
    """

    message = "🤖 Gemini OK!\n\n"
    message += test_gemini()

    send_message(message)


if __name__ == "__main__":
    main()
