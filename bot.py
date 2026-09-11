import os
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()

async def send_reminder():
    """Функція надсилання нагадування"""
    if ADMIN_ID:
        current_time = datetime.now().strftime("%H:%M:%S")
        await bot.send_message(
            chat_id=ADMIN_ID, 
            text=f"⏰ Час перерватись! Поточний час: {current_time}"
        )

@dp.message(commands=["start"])
async def start_command(message: types.Message):
    await message.answer("Привіт! Я твій безкоштовний бот-нагадування на Render.")

# Вебсервер, щоб Render бачив живий сайт і не вимикав його
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    # Запускаємо вебсервер для Render
    await start_web_server()

    # Планувальник нагадувань (можна змінити інтервал, наприклад: minutes=30)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminder, "interval", hours=1)
    scheduler.start()
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
