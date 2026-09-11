import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from pytz import timezone
import dateparser
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
KYIV_TZ = timezone("Europe/Kiev")

bot = Bot(token=TOKEN)
dp = Dispatcher()

jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///reminders.db')
}
scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=KYIV_TZ)

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

class ReminderStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time = State()
    waiting_for_repeat = State()
    waiting_for_custom_days = State()
    editing_text = State()
    editing_time = State()

async def send_reminder(job_id: str, chat_id: int, text: str):
    try:
        builder = InlineKeyboardBuilder()
        builder.button(text="✨ Виконано", callback_data=f"done_{job_id}")
        builder.button(text="⏱ +15 хв", callback_data=f"snooze_{job_id}_15")
        builder.button(text="⏳ +1 год", callback_data=f"snooze_{job_id}_60")
        builder.button(text="🌅 Завтра", callback_data=f"snooze_{job_id}_day")
        builder.adjust(2, 2)

        await bot.send_message(
            chat_id=chat_id, 
            text=f"🔔 **Нагадування!**\n\n💬 *{text}*", 
            parse_mode="Markdown", 
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        logging.error(f"Помилка надсилання: {e}")

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "👋 **Привіт! Я твій інтелектуальний асистент-нагадування.**\n\n"
        "🚀 **Як зі мною працювати:**\n"
        "Просто напиши мені завдання у вільній формі, наприклад:\n"
        "• _«Завтра о 15:30 забрати посилку»_\n"
        "• _«Через 2 години вимкнути пральну машину»_\n"
        "Або викорискай команду `/add [текст і час]`.\n\n"
        "📌 **Основні команди:**\n"
        "📋 /list — Активні задачі та керування\n"
        "❓ /help — Детальна довідка",
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "💡 **Прогресивні поради:**\n"
        "• Я чудово розумію українську та російську мови.\n"
        "• Можна писати природно: _«у п'ятницю о 10:00»_, _«щодня о 8:00»_.\n"
        "• Усі задачі надійно захищені в базі даних і нікуди не зникнуть при перезавантаженні сервера.",
        parse_mode="Markdown"
    )

def get_cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати", callback_data="cancel_action")
    return builder.as_markup()

@dp.callback_query(F.data == "cancel_action")
async def cancel_action(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Дію скасовано.")
    await callback.answer()

async def parse_reminder_string(full_text: str):
    words = full_text.split()
    parsed_date = None
    text_part = full_text

    for i in range(len(words), 0, -1):
        candidate = " ".join(words[-i:])
        dt = dateparser.parse(
            candidate,
            languages=['uk', 'ru'],
            settings={
                'TIMEZONE': 'Europe/Kiev',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'PREFER_DATES_FROM': 'future'
            }
        )
        if dt and dt > datetime.now(KYIV_TZ):
            parsed_date = dt
            text_part = " ".join(words[:-i])
            break
            
    return text_part, parsed_date

@dp.message(Command("add"))
async def add_command(message: types.Message, state: FSMContext):
    args = message.text.replace("/add", "", 1).strip()
    if not args:
        await state.set_state(ReminderStates.waiting_for_text)
        await message.answer("✍️ Що саме потрібно нагадати?", reply_markup=get_cancel_keyboard())
        return

    text_part, parsed_date = await parse_reminder_string(args)
    if not parsed_date or not text_part:
        await state.set_state(ReminderStates.waiting_for_text)
        await state.update_data(text=args)
        await message.answer("🕒 Коли саме нагадати? (наприклад: *завтра о 14:00*):", parse_mode="Markdown", reply_markup=get_cancel_keyboard())
        return

    await state.update_data(text=text_part, target_time=parsed_date)
    await state.set_state(ReminderStates.waiting_for_repeat)
    await show_repeat_options(message, text_part, parsed_date)

async def show_repeat_options(message_or_callback, text, target_time):
    builder = InlineKeyboardBuilder()
    builder.button(text="📌 Одноразово", callback_data="rep_once")
    builder.button(text="🔄 Щодня", callback_data="rep_daily")
    builder.button(text="📅 Щотижня", callback_data="rep_weekly")
    builder.button(text="🔢 Кожні N днів", callback_data="rep_custom")
    builder.button(text="❌ Скасувати", callback_data="cancel_action")
    builder.adjust(1)
    
    text_content = f"🎯 **Задача:** _{text}_\n⏰ **Час:** {target_time.strftime('%d.%m.%Y о %H:%M')}\n\nОбери тип повторення:"
    
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(text_content, parse_mode="Markdown", reply_markup=builder.as_markup())
    else:
        await message_or_callback.answer(text_content, parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.message(F.text & ~F.text.startswith("/"))
async def smart_natural_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    text_part, parsed_date = await parse_reminder_string(message.text)
    if parsed_date and text_part:
        await state.update_data(text=text_part, target_time=parsed_date)
        await state.set_state(ReminderStates.waiting_for_repeat)
        await show_repeat_options(message, text_part, parsed_date)

@dp.message(ReminderStates.waiting_for_text)
async def add_text(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(ReminderStates.waiting_for_time)
    await message.answer("🕒 Коли нагадати? (наприклад: *через 3 години* або *у суботу о 10:00*):", parse_mode="Markdown", reply_markup=get_cancel_keyboard())

@dp.message(ReminderStates.waiting_for_time)
async def add_time(message: types.Message, state: FSMContext):
    parsed_date = dateparser.parse(
        message.text.strip(),
        languages=['uk', 'ru'],
        settings={'TIMEZONE': 'Europe/Kiev', 'RETURN_AS_TIMEZONE_AWARE': True, 'PREFER_DATES_FROM': 'future'}
    )

    if not parsed_date or parsed_date <= datetime.now(KYIV_TZ):
        await message.answer("⚠️ Не вдалося розпізнати час або він у минулому. Спробуй ще раз:", reply_markup=get_cancel_keyboard())
        return

    data = await state.get_data()
    text = data["text"]
    await state.update_data(target_time=parsed_date)
    await state.set_state(ReminderStates.waiting_for_repeat)
    await show_repeat_options(message, text, parsed_date)

@dp.callback_query(ReminderStates.waiting_for_repeat, F.data == "rep_custom")
async def ask_custom_days(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ReminderStates.waiting_for_custom_days)
    await callback.message.edit_text("🔢 Введи кількість днів інтервалу (ціле число, наприклад `3`):", reply_markup=get_cancel_keyboard())
    await callback.answer()

@dp.message(ReminderStates.waiting_for_custom_days)
async def process_custom_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text.strip())
        if days <= 0: raise ValueError()
    except ValueError:
        await message.answer("⚠️ Введи коректне додатне число:", reply_markup=get_cancel_keyboard())
        return

    data = await state.get_data()
    text, target_time = data["text"], data["target_time"]
    user_id = message.from_user.id
    job_id = f"rem_{user_id}_{int(datetime.now().timestamp())}"

    scheduler.add_job(send_reminder, "interval", days=days, start_date=target_time, args=[job_id, user_id, text], id=job_id)
    sched_text = f"Кожні {days} дн."

    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_reminders VALUES (?, ?, ?, ?)", (job_id, user_id, text, sched_text))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(f"✅ **Успішно створено!**\n\n📌 _{text}_\n🕒 {target_time.strftime('%d.%m.%Y о %H:%M')}\n🔄 {sched_text}", parse_mode="Markdown")

@dp.callback_query(ReminderStates.waiting_for_repeat, F.data.startswith("rep_"))
async def add_repeat_finish(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "rep_custom": return

    data = await state.get_data()
    text, target_time = data["text"], data["target_time"]
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
    await callback.message.edit_text(f"✅ **Успішно створено!**\n\n📌 _{text}_\n🕒 {target_time.strftime('%d.%m.%Y о %H:%M')}\n🔄 {sched_text}", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("done_"))
async def btn_done(callback: types.CallbackQuery):
    job_id = callback.data.split("_", 1)[1]
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT schedule_type FROM user_reminders WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    
    if row and row[0] == "Одноразово":
        if scheduler.get_job(job_id): scheduler.remove_job(job_id)
        cursor.execute("DELETE FROM user_reminders WHERE id = ?", (job_id,))
        conn.commit()
        info = "✨ Виконано та видалено."
    else:
        info = "✨ Виконано! Наступного разу спрацює за розкладом."
    conn.close()
    await callback.message.edit_text(callback.message.text + f"\n\n_{info}_", parse_mode="Markdown")
    await callback.answer("Готово!")

@dp.callback_query(F.data.startswith("snooze_"))
async def btn_snooze(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    job_id, s_type = parts[1], parts[2]
    
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, text FROM user_reminders WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        user_id, text = row
        now = datetime.now(KYIV_TZ)
        if s_type == "15": new_time, label = now + timedelta(minutes=15), "на 15 хв"
        elif s_type == "60": new_time, label = now + timedelta(hours=1), "на 1 годину"
        else: new_time, label = now + timedelta(days=1), "на завтра"
        
        if scheduler.get_job(job_id): scheduler.remove_job(job_id)
        scheduler.add_job(send_reminder, "date", run_date=new_time, args=[job_id, user_id, text], id=job_id)
        await callback.message.edit_text(callback.message.text + f"\n\n_⏳ Відкладено {label} (до {new_time.strftime('%H:%M')})_", parse_mode="Markdown")
    else:
        await callback.message.edit_text("⚠️ Задача вже не знайдена.")
    await callback.answer("Відкладено!")

async def show_user_reminders_list(message: types.Message, user_id: int):
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, schedule_type FROM user_reminders WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("📭 У тебе немає активних нагадувань.")
        return

    builder = InlineKeyboardBuilder()
    text_msg = "📋 **Твої активні задачі:**\n\n"
    for idx, (job_id, text, sched_type) in enumerate(rows, 1):
        job = scheduler.get_job(job_id)
        next_run = job.next_run_time.astimezone(KYIV_TZ).strftime('%d.%m %H:%M') if job and job.next_run_time else "—"
        text_msg += f"{idx}. 📌 *{text}*\n   🕒 `{next_run}` • _{sched_type}_\n\n"
        builder.button(text=f"⚙️ Керувати #{idx}", callback_data=f"manage_{job_id}")

    builder.adjust(1)
    await message.answer(text_msg, parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.message(Command("list"))
async def list_reminders(message: types.Message):
    await show_user_reminders_list(message, message.from_user.id)

@dp.callback_query(F.data.startswith("manage_"))
async def manage_reminder(callback: types.CallbackQuery):
    job_id = callback.data.split("_", 1)[1]
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT text, schedule_type FROM user_reminders WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        await callback.message.edit_text("⚠️ Задача не знайдена.")
        await callback.answer()
        return

    text, sched_type = row
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Змінити текст", callback_data=f"edittext_{job_id}")
    builder.button(text="🕒 Змінити час", callback_data=f"edittime_{job_id}")
    builder.button(text="🗑 Видалити", callback_data=f"del_{job_id}")
    builder.button(text="⬅️ Назад до списку", callback_data="back_to_list")
    builder.adjust(1)

    await callback.message.edit_text(f"📌 **Задача:** _{text}_\n🔄 **Повторення:** {sched_type}\n\nОбери дію:", parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "back_to_list")
async def back_to_list(callback: types.CallbackQuery):
    await callback.message.delete()
    user_id = callback.from_user.id
    await show_user_reminders_list(callback.message, user_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("edittext_"))
async def start_edit_text(callback: types.CallbackQuery, state: FSMContext):
    job_id = callback.data.split("_", 1)[1]
    await state.update_data(editing_job_id=job_id)
    await state.set_state(ReminderStates.editing_text)
    await callback.message.answer("✍️ Введи новий текст задачі:", reply_markup=get_cancel_keyboard())
    await callback.answer()

@dp.message(ReminderStates.editing_text)
async def save_edit_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    job_id, new_text = data.get("editing_job_id"), message.text

    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE user_reminders SET text = ? WHERE id = ?", (new_text, job_id))
    conn.commit()
    conn.close()

    job = scheduler.get_job(job_id)
    if job:
        args = list(job.args)
        args[2] = new_text
        job.modify(args=args)

    await state.clear()
    await message.answer("✅ Текст успішно оновлено!")

@dp.callback_query(F.data.startswith("edittime_"))
async def start_edit_time(callback: types.CallbackQuery, state: FSMContext):
    job_id = callback.data.split("_", 1)[1]
    await state.update_data(editing_job_id=job_id)
    await state.set_state(ReminderStates.editing_time)
    await callback.message.answer("🕒 Введи новий час (наприклад: *завтра о 18:00*):", parse_mode="Markdown", reply_markup=get_cancel_keyboard())
    await callback.answer()

@dp.message(ReminderStates.editing_time)
async def save_edit_time(message: types.Message, state: FSMContext):
    parsed_date = dateparser.parse(
        message.text.strip(),
        languages=['uk', 'ru'],
        settings={'TIMEZONE': 'Europe/Kiev', 'RETURN_AS_TIMEZONE_AWARE': True, 'PREFER_DATES_FROM': 'future'}
    )

    if not parsed_date or parsed_date <= datetime.now(KYIV_TZ):
        await message.answer("⚠️ Неправильний час або він у минулому:", reply_markup=get_cancel_keyboard())
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
        if scheduler.get_job(job_id): scheduler.remove_job(job_id)
        if sched_type == "Одноразово":
            scheduler.add_job(send_reminder, "date", run_date=parsed_date, args=[job_id, user_id, text], id=job_id)
        elif sched_type == "Щодня":
            scheduler.add_job(send_reminder, "cron", hour=parsed_date.hour, minute=parsed_date.minute, args=[job_id, user_id, text], id=job_id)
        elif sched_type == "Щотижня":
            scheduler.add_job(send_reminder, "cron", day_of_week=parsed_date.strftime("%a").lower(), hour=parsed_date.hour, minute=parsed_date.minute, args=[job_id, user_id, text], id=job_id)
        elif "Кожні" in sched_type:
            days_num = int(''.join(filter(str.isdigit, sched_type)))
            scheduler.add_job(send_reminder, "interval", days=days_num, start_date=parsed_date, args=[job_id, user_id, text], id=job_id)

    await state.clear()
    await message.answer(f"✅ Час оновлено на {parsed_date.strftime('%d.%m.%Y о %H:%M')}!")

@dp.callback_query(F.data.startswith("del_"))
async def delete_reminder(callback: types.CallbackQuery):
    job_id = callback.data.split("_", 1)[1]
    if scheduler.get_job(job_id): scheduler.remove_job(job_id)
    
    conn = sqlite3.connect('reminders.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_reminders WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text("🗑 Нагадування успішно видалено.")
    await callback.answer()

async def handle_ping(request):
    return web.Response(text="Bot is running smoothly!")

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
