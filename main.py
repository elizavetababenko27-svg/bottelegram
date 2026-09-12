import os
import time
from datetime import datetime
from flask import Flask
from notion_client import Client
import requests

app = Flask(__name__)

# Змінні середовища
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("DATABASE_ID")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

notion = Client(auth=NOTION_TOKEN)
notified_pages = set()
is_initialized = False

# Захист від занадто частого опитування при частих пінгах
last_check_time = 0
CHECK_INTERVAL = 60  # Секунд між перевірками (1 хвилина)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Помилка відправки в Telegram: {e}")

def check_notion_calendar():
    global is_initialized, notified_pages
    try:
        response = notion.databases.query(
            database_id=DATABASE_ID,
            sorts=[{"timestamp": "created_time", "direction": "descending"}]
        )
        
        pages = response.get("results", [])
        
        # Ініціалізація при першому запуску, щоб не слати старі події
        if not is_initialized:
            for page in pages:
                notified_pages.add(page["id"])
            is_initialized = True
            print("Ініціалізація завершена. Стежимо за новими сторінками...")
            return

        for page in pages:
            page_id = page["id"]
            if page_id not in notified_pages:
                properties = page["properties"]
                title = "Без назви"
                
                # Шукаємо поле типу title
                for prop_name, prop_value in properties.items():
                    if prop_value["type"] == "title":
                        title_array = prop_value.get("title", [])
                        if title_array:
                            title = title_array[0].get("text", {}).get("content", "Без назви")
                
                message = f"📅 *З'явилася нова сторінка в календарі Notion!*\n\n📌 Назва: {title}"
                send_telegram_message(message)
                notified_pages.add(page_id)
                
    except Exception as e:
        print(f"Помилка при запиті до Notion: {e}")

@app.route("/")
def home():
    global last_check_time
    current_time = time.time()
    
    # Перевіряємо Notion тільки якщо минуло більше ніж CHECK_INTERVAL секунд
    if current_time - last_check_time > CHECK_INTERVAL:
        check_notion_calendar()
        last_check_time = current_time
        
    return "Notion-Telegram Bot is running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
