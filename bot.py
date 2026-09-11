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

jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///reminders.db')
}
scheduler = AsyncIOScheduler(jobstores=jobstores)

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

# Стани для FSM (створення та редагування)
class ReminderStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time = State()
    waiting_for_repeat = State()
    editing_text = State()
    editing_time = State()

async def send_reminder(job_id: str, chat_id: int, text: str):
    try:
        await bot.send_message(chat_id=chat_id, text=f"⏰ **Нагадування:**\n\n{text}", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Помилка надсилання: {e}")

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "Привіт! Я твій бот-нагадування.\n\n"
        "📌 **Команди:**\n"
        "➕ /add — Створити нове нагадування\n"
        "📋 /list — Переглянути, редагувати або видалити нагадування"
    )

# --- СТВОРЕННЯ НАГАДУВАННЯ ---
@dp.message(Command("add"))
async def add_start(message: types.Message, state: FSMContext):
    await state.set_state(ReminderStates.waiting_for_text)
    await message.answer("Введи текст нагадування:")

@dp.message(ReminderStates.waiting_for_text)
async def add_text(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(ReminderStates.waiting_for_time)
    await message.answer("Введи дату та час у форматі:\n`ДД.ММ.РРРР ГГ:ХХ`\n(Наприклад: `20.09.2026 18:00`)")

@dp.message(ReminderStates.waiting_for_time)
async def add_time(message: types.Message, state: FSMContext):
    try:
        target_time = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        if target_time <= datetime.now():
            await message.answer("Цей час вже минув! Введи майбутню дату та час у форматі `ДД.ММ.РРРР ГГ:ХХ`:")
            return
    except ValueError:
        await message.answer("Неправильний формат! Використовуй строго `ДД.ММ.РРРР ГГ:ХХ`:")
        return

    await state.update_data(target_time=target_time)
    await state.set_state(ReminderStates.waiting_for_repeat)

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

    if repeat_type == "once":
        scheduler.add_job(send_reminder, "date", run_date=target_time, args=[job_id, user_id, text], id=job_id)
        sched_text = "Одноразово"
    elif repeat_type == "daily":
        scheduler.add_job(send_reminder, "cron", hour=target_time.hour, minute=target_time.minute, args=[job_id, user_id, text], id=job_id)
        sched_text = "Щодня"
    elif repeat_type == "weekly":
        scheduler.add_job(send_reminder, "cron", day_of_week=target_time.strftime("%a").lower(), hour=target_time.hour, minute=target_time.minute, args=[job_id, user_id, text], id=job_id)
        sched_text = "Щотижня"

    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_reminders VALUES (?, ?, ?, ?)", (job_id, user_id, text, sched_text))
    conn.commit()
    conn.close()

    await state.clear()
    await callback.message.edit_text(f"✅ Нагадування створено!\n\n📌 Текст: {text}\n🕒 Час: {target_time.strftime('%d.%m.%Y %H:%M')}\n🔄 Повторення: {sched_text}")
    await callback.answer()

# --- СПИСОК ТА КЕРУВАННЯ ---
@dp.message(Command("list"))
async def list_reminders(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, schedule_type FROM user_reminders WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("У тебе немає активних нагадувань.")
        return

    builder = InlineKeyboardBuilder()
    text_msg = "📋 **Активні нагадування:**\n\n"
    for idx, (job_id, text, sched_type) in enumerate(rows, 1):
        job = scheduler.get_job(job_id)
        next_run = job.next_run_time.strftime('%d.%m.%Y %H:%M') if job and job.next_run_time else "За розкладом"
        text_msg += f"{idx}. 📌 _{text}_\n   🕒 {next_run} ({sched_type})\n\n"
        builder.button(text=f"⚙️ Керувати #{idx}", callback_data=f"manage_{job_id}")

    builder.adjust(1)
    await message.answer(text_msg, parse_mode="Markdown", reply_markup=builder.as_markup())

# Меню керування конкретним нагадуванням
@dp.callback_query(F.data.startswith("manage_"))
async def manage_reminder(callback: types.CallbackQuery):
    job_id = callback.data.split("_", 1)[1]
    
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT text, schedule_type FROM user_reminders WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        await callback.message.edit_text("Це нагадування вже не існує.")
        await callback.answer()
        return

    text, sched_type = row
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Змінити текст", callback_data=f"edittext_{job_id}")
    builder.button(text="🕒 Змінити час", callback_data=f"edittime_{job_id}")
    builder.button(text="🗑 Видалити", callback_data=f"del_{job_id}")
    builder.button(text="⬅️ Назад до списку", callback_data="back_to_list")
    builder.adjust(1)

    await callback.message.edit_text(f"📌 **Нагадування:** _{text}_\n🔄 **Тип:** {sched_type}\n\nОбери дію:", parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "back_to_list")
async def back_to_list(callback: types.CallbackQuery):
    await callback.message.delete()
    # Просто імітуємо виклик списку
    message = callback.message
    message.from_user = callback.from_user
    await list_reminders(message)

# --- РЕДАГУВАННЯ ТЕКСТУ ---
@dp.callback_query(F.data.startswith("edittext_"))
async def start_edit_text(callback: types.CallbackQuery, state: FSMContext):
    job_id = callback.data.split("_", 1)[1]
    await state.update_data(editing_job_id=job_id)
    await state.set_state(ReminderStates.editing_text)
    await callback.message.answer("Введи новий текст для цього нагадування:")
    await callback.answer()

@dp.message(ReminderStates.editing_text)
async def save_edit_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    job_id = data.get("editing_job_id")
    new_text = message.text

    # Оновлюємо в БД
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE user_reminders SET text = ? WHERE id = ?", (new_text, job_id))
    conn.commit()
    conn.close()

    # Оновлюємо аргументи в APScheduler
    job = scheduler.get_job(job_id)
    if job:
        args = list(job.args)
        args[2] = new_text  # змінюємо текст в args=[job_id, user_id, text]
        job.modify(args=args)

    await state.clear()
    await message.answer("✅ Текст нагадування успішно змінено! Використай /list для перегляду.")

# --- РЕДАГУВАННЯ ЧАСУ ---
@dp.callback_query(F.data.startswith("edittime_"))
async def start_edit_time(callback: types.CallbackQuery, state: FSMContext):
    job_id = callback.data.split("_", 1)[1]
    await state.update_data(editing_job_id=job_id)
    await state.set_state(ReminderStates.editing_time)
    await callback.message.answer("Введи новий час у форматі `ДД.ММ.РРРР ГГ:ХХ`:")
    await callback.answer()

@dp.message(ReminderStates.editing_time)
async def save_edit_time(message: types.Message, state: FSMContext):
    try:
        new_time = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        if new_time <= datetime.now():
            await message.answer("Цей час вже минув! Введи майбутню дату та час у форматі `ДД.ММ.РРРР ГГ:ХХ`:")
            return
    except ValueError:
        await message.answer("Неправильний формат! Використовуй строго `ДД.ММ.РРРР ГГ:ХХ`:")
        return

    data = await state.get_data()
    job_id = data.get("editing_job_id")

    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, text, schedule_type FROM user_reminders WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        user_id, text, sched_type = row
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        # Переплануємо завдання
        if sched_type == "Одноразово":
            scheduler.add_job(send_reminder, "date", run_date=new_time, args=[job_id, user_id, text], id=job_id)
        elif sched_type == "Щодня":
            scheduler.add_job(send_reminder, "cron", hour=new_time.hour, minute=new_time.minute, args=[job_id, user_id, text], id=job_id)
        elif sched_type == "Щотижня":
            scheduler.add_job(send_reminder, "cron", day_of_week=new_time.strftime("%a").lower(), hour=new_time.hour, minute=new_time.minute, args=[job_id, user_id, text], id=job_id)

    await state.clear()
    await message.answer(f"✅ Час нагадування успішно змінено на {new_time.strftime('%d.%m.%Y %H:%M')}!")

# --- ВИДАЛЕННЯ ---
@dp.callback_query(F.data.startswith("del_"))
async def delete_reminder(callback: types.CallbackQuery):
    job_id = callback.data.split("_", 1)[1]
    
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_reminders WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("🗑 Нагадування успішно видалено!")
    await callback.answer()

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
