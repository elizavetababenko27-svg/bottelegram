import os
import time
from flask import Flask
from notion_client import Client
import requests

app = Flask(__name__)

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("DATABASE_ID")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

notion = Client(auth=NOTION_TOKEN)

# Зберігаємо стан бази: {page_id: last_edited_time}
known_pages = {}
is_initialized = False

last_check_time = 0
CHECK_INTERVAL = 0  # Перевіряти одразу при кожному пінгу від Cron-Job.org

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Помилка відправки в Telegram: {e}")

def extract_page_title(page):
    properties = page.get("properties", {})
    title = "Без назви"
    
    # Шукаємо назву (поле типу title)
    for prop_name, prop_value in properties.items():
        if prop_value["type"] == "title":
            title_array = prop_value.get("title", [])
            if title_array:
                title = title_array[0].get("text", {}).get("content", "Без назви")
                
    last_edited = page.get("last_edited_time", "")
    return title, last_edited

def check_notion_calendar():
    global is_initialized, known_pages
    try:
        response = notion.databases.query(
            database_id=DATABASE_ID,
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}]
        )
        
        pages = response.get("results", [])
        
        # Перший запуск: запам'ятовуємо все, що є, щоб не спамити старими сторінками
        if not is_initialized:
            for page in pages:
                page_id = page["id"]
                _, last_edited = extract_page_title(page)
                known_pages[page_id] = last_edited
            is_initialized = True
            print("Ініціалізація завершена. Стежимо за новими сторінками...")
            return

        for page in pages:
            page_id = page["id"]
            title, last_edited = extract_page_title(page)
            
            if page_id not in known_pages:
                # З'явилася нова сторінка — запам'ятовуємо її і шлемо ТІЛЬКИ налвзи (title)
                known_pages[page_id] = last_edited
                send_telegram_message(title)
                
            else:
                # Якщо сторінка вже була, просто оновлюємо час редакції без сповіщень
                known_pages[page_id] = last_edited

    except Exception as e:
        print(f"Помилка при запиті до Notion: {e}")

@app.route("/")
def home():
    global last_check_time
    current_time = time.time()
    
    if current_time - last_check_time > CHECK_INTERVAL:
        check_notion_calendar()
        last_check_time = current_time
        
    return "Notion-Telegram Bot is running!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
