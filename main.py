import os
import time
import requests
from notion_client import Client

# Змінні середовища (будемо задавати в панелі Render)
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("DATABASE_ID")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

notion = Client(auth=NOTION_TOKEN)

# Зберігаємо ID сторінок, про які вже сповістили, щоб не надсилати повторно
notified_pages = set()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

def check_notion_calendar():
    try:
        # Отримуємо записи з бази даних Notion
        response = notion.databases.query(
            database_id=DATABASE_ID,
            sorts=[{"timestamp": "created_time", "direction": "descending"}]
        )
        
        pages = response.get("results", [])
        
        # Під час першого запуску просто заповнюємо базу ID, щоб не спамити старими записами
        global notified_pages
        if not notified_pages and len(pages) > 0:
            for page in pages:
                notified_pages.add(page["id"])
            print("Ініціалізація завершена. Слідкуємо за новими записами...")
            return

        for page in pages:
            page_id = page["id"]
            if page_id not in notified_pages:
                # Новий запис знайдено! Дістаємо назву (зазвичай властивість типу 'title')
                properties = page["properties"]
                title = "Без назви"
                
                # Шукаємо поле типу title
                for prop_name, prop_value in properties.items():
                    if prop_value["type"] == "title":
                        title_array = prop_value.get("title", [])
                        if title_array:
                            title = title_array[0].get("text", {}).get("content", "Без назви")
                
                # Формуємо повідомлення
                message = f"📅 *З'явилася нова подія в календарі!*\n\n📌 Назва: {title}"
                send_telegram_message(message)
                
                # Додаємо до списку опрацьованих
                notified_pages.add(page_id)
                
    except Exception as e:
        print(f"Помилка при запиті до Notion: {e}")

if __name__ == "__main__":
    print("Бот запущено і слідкує за Notion...")
    while True:
        check_notion_calendar()
        time.sleep(60)  # Перевірка кожну хвилину (синхронність біля 1 хвилини)
