import os
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# Отримуємо змінні середовища з Render
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

bot = Bot(token=TOKEN)
dp = Dispatcher()

async def send_reminder():
    """Функція, яка надсилає нагадування"""
    if ADMIN_ID:
        current_time = datetime.now().strftime("%H:%M:%S")
        await bot.send_message(
            chat_id=ADMIN_ID, 
            text=f"⏰ Час перерватись! Поточний час: {current_time}"
        )

@dp.message(commands=["start"])
async def start_command(message: types.Message):
    await message.answer("Привіт! Я твій бот-нагадування. Я працюю на Render і буду надсилати повідомлення за розкладом.")

async def main():
    # Налаштування планувальника (APScheduler)
    scheduler = AsyncIOScheduler()
    
    # ТУТ НАЛАШТОВУЄТЬСЯ РОЗКЛАД:
    # Наприклад, нагадування кожну годину. 
    # Можна змінити на minutes=30 або hours=2
    scheduler.add_job(send_reminder, "interval", hours=1)
    
    scheduler.start()
    
    # Запускаємо бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
