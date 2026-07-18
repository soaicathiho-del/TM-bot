from gemini_service import test_gemini
from telegram_service import send_message


def main():
    reply = test_gemini()

    send_message(
        f"""🤖 Gemini OK!

{reply}"""
    )


if __name__ == "__main__":
    main()
