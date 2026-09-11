import os
import asyncio
import logging
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Налаштування APScheduler з підтримкою збереження в БД (SQLite)
jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///reminders.db')
}
scheduler = AsyncIOScheduler(jobstores=jobstores)

# Ініціалізація бази даних SQLite для додаткової інформації
def init_db():
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_reminders (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            text TEXT,
            schedule_type TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Стани для FSM (створення нагадування)
class ReminderStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time = State()
    waiting_for_repeat = State()

async def send_reminder(job_id: str, chat_id: int, text: str):
    """Функція надсилання нагадування"""
    try:
        await bot.send_message(chat_id=chat_id, text=f"⏰ **Нагадування:**\n\n{text}", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Помилка надсилання: {e}")

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привіт! Я твій розширений бот-нагадування.\n\n"
        "📌 **Доступні команди:**\n"
        "➕ /add — Створити нове нагадування\n"
        "📋 /list — Переглянути та керувати активними нагадуваннями\n"
        "❓ /help — Допомога"
    )

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "Як користуватися:\n"
        "1. Натисни /add і введи текст.\n"
        "2. Введи точний час у форматі `ДД.ММ.РРРР ГГ:ХХ` (наприклад, `15.09.2026 14:30`).\n"
        "3. Обери, чи потрібно повторювати подію.\n"
        "У списку (/list) ти зможеш видалити або переглянути свої задачі."
    )

# --- КРОК 1: Створення нагадування ---
@dp.message(Command("add"))
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(ReminderStates.waiting_for_text)
    await message.answer("Введи текст нагадування:")

@dp.message(ReminderStates.waiting_for_text)
async def add_text(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(ReminderStates.waiting_for_time)
    await message.answer(
        "Введи дату та час у форматі:\n`ДД.ММ.РРРР ГГ:ХХ`\n"
        "(Наприклад: `20.09.2026 18:00`)"
    )

@dp.message(ReminderStates.waiting_for_time)
async def add_time(message: types.Message, state: FSMContext):
    try:
        target_time = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        if target_time <= datetime.now():
            await message.answer("Цей час вже минув! Введи майбутню дату та час у форматі `ДД.ММ.РРРР ГГ:ХХ`:")
            return
    except ValueError:
        await message.answer("Неправильний формат! Використовуй строго `ДД.ММ.РРРР ГГ:ХХ` (наприклад: `15.09.2026 14:30`):")
        return

    await state.update_data(target_time=target_time)
    await state.set_state(ReminderStates.waiting_for_repeat)

    # Кнопки для повторення
    builder = InlineKeyboardBuilder()
    builder.button(text="Одноразово", callback_data="rep_once")
    builder.button(text="Щодня", callback_data="rep_daily")
    builder.button(text="Щотижня", callback_data="rep_weekly")
    builder.adjust(1)

    await message.answer("Обери частоту повторення:", reply_markup=builder.as_markup())

@dp.callback_query(ReminderStates.waiting_for_repeat, F.data.startswith("rep_"))
async def add_repeat_finish(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = data["text"]
    target_time: datetime = data["target_time"]
    repeat_type = callback.data.split("_")[1]
    user_id = callback.from_user.id
    job_id = f"rem_{user_id}_{int(datetime.now().timestamp())}"

    # Налаштування розкладу в APScheduler
    if repeat_type == "once":
        scheduler.add_job(send_reminder, "date", run_date=target_time, args=[job_id, user_id, text], id=job_id)
        sched_text = "Одноразово"
    elif repeat_type == "daily":
        scheduler.add_job(send_reminder, "cron", hour=target_time.hour, minute=target_time.minute, args=[job_id, user_id, text], id=job_id)
        sched_text = "Щодня"
    elif repeat_type == "weekly":
        scheduler.add_job(send_reminder, "cron", day_of_week=target_time.strftime("%a").lower(), hour=target_time.hour, minute=target_time.minute, args=[job_id, user_id, text], id=job_id)
        sched_text = "Щотижня"

    # Зберігаємо додаткову інфо в БД
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_reminders VALUES (?, ?, ?, ?)", (job_id, user_id, text, sched_text))
    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text(f"✅ Нагадування успішно створено!\n\n📌 Текст: {text}\n🕒 Час: {target_time.strftime('%d.%m.%Y %H:%M')}\n🔄 Повторення: {sched_text}")
    await callback.answer()

# --- КРОК 2: Керування / Список нагадувань ---
@dp.message(Command("list"))
async def list_reminders(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, schedule_type FROM user_reminders WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("У тебе немає активних нагадувань. Створи нове через /add")
        return

    builder = InlineKeyboardBuilder()
    text_msg = "📋 ** твої активні нагадування:**\n\n"
    for idx, (job_id, text, sched_type) in enumerate(rows, 1):
        job = scheduler.get_job(job_id)
        next_run = job.next_run_time.strftime('%d.%m.%Y %H:%M') if job and job.next_run_time else "За розкладом"
        text_msg += f"{idx}. 📌 _{text}_\n   🕒 Наступний запуск: {next_run} ({sched_type})\n\n"
        builder.button(text=f"🗑 Видалити #{idx}", callback_data=f"del_{job_id}")

    builder.adjust(1)
    await message.answer(text_msg, parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_"))
async def delete_reminder(callback: types.CallbackQuery):
    job_id = callback.data.split("_", 1)[1]
    
    # Видаляємо з планувальника
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    # Видаляємо з БД
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_reminders WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("🗑 Нагадування успішно видалено!")
    await callback.answer()

# Вебсервер для Render
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
    await start_web_server()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
