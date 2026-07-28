from flask import Flask, request
import telegram_handlers
from config import CHAT_ID

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data:
        chat_id = str(data["message"]["chat"]["id"])
        text = data["message"].get("text", "")
        
        if chat_id == CHAT_ID:
            if text.startswith("/start"):
                telegram_handlers.handle_start(chat_id)
            elif text.startswith("/focus"):
                parts = text.split()
                duration = parts[1] if len(parts) > 1 else "25"
                telegram_handlers.handle_focus(chat_id, duration)
            else:
                telegram_handlers.handle_message(chat_id, text)
                
    return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
