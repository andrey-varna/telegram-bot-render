import os
import re
import json
import asyncio
import logging
from datetime import datetime

from aiohttp import web
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove

# ================== КОНФИГУРАЦИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_SHEET_KEY = os.getenv("MAIN_SHEET_KEY")
UNCONFIRMED_SHEET_KEY = os.getenv("UNCONFIRMED_SHEET_KEY")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Должен быть https://название.onrender.com

logging.basicConfig(level=logging.INFO)

# ================== GOOGLE SHEETS ==================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.authorize(credentials)

unconfirmed_sheet = gc.open_by_key(UNCONFIRMED_SHEET_KEY).sheet1
main_sheet = gc.open_by_key(MAIN_SHEET_KEY).sheet1

# ================== BOT & FSM ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


class BookingForm(StatesGroup):
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    main_task = State()
    time_of_day = State()


# ================== КЛАВИАТУРЫ ==================
def get_reply_kb(options):
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=opt)] for opt in options],
        resize_keyboard=True
    )


role_keyboard = get_reply_kb(["Собственник бизнеса", "CEO / управляющий", "Предприниматель", "Я эксперт/фрилансер"])
business_keyboard = get_reply_kb(["Только запускаю", "Действующий бизнес", "Масштабирую"])
partner_keyboard = get_reply_kb(["Да", "Нет, но хочу", "Нет"])
task_keyboard = get_reply_kb([
    "Перестать всё контролировать и тащить на себе",
    "Вернуть страсть и близость, не теряя доход",
    "Распределить роли, чтобы не конфликтовать",
    "Выйти на новый уровень дохода без выгорания"
])
time_keyboard = get_reply_kb(["Утро", "День", "Вечер"])
confirm_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="confirm_final")]])


# ================== ЛОГИКА ТАБЛИЦ ==================

def sync_unconfirmed(data: dict, status: str = "started"):
    try:
        records = unconfirmed_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]
        tid = str(data.get("telegram_id"))
        row = [tid, data.get("username", ""), data.get("name", ""), data.get("role", ""),
               data.get("business_stage", ""), data.get("partner", ""), data.get("time_of_day", ""),
               status, data.get("source", ""), data.get("campaign", ""), data.get("started_at", ""), ""]

        if tid in ids:
            idx = ids.index(tid) + 2
            unconfirmed_sheet.update(range_name=f"A{idx}:L{idx}", values=[row])
        else:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"❌ Error sync_unconfirmed: {e}")


def finalize_to_main(data: dict):
    try:
        row_main = [str(data.get("telegram_id")), data.get("username", ""), data.get("source", ""),
                    data.get("campaign", ""), data.get("name", ""), data.get("role", ""),
                    data.get("business_stage", ""), data.get("partner", ""), data.get("main_task", ""),
                    data.get("time_of_day", ""), datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        main_sheet.append_row(row_main)

        records = unconfirmed_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]
        tid = str(data.get("telegram_id"))
        if tid in ids:
            idx = ids.index(tid) + 2
            unconfirmed_sheet.delete_rows(idx)
        return True
    except Exception as e:
        logging.error(f"❌ Error finalize_to_main: {e}")
        return False


# ================== HANDLERS ==================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split(" ", 1)
    source, campaign = "organic", ""
    if len(args) > 1:
        parts = args[1].split("_", 1)
        source = parts[0]
        campaign = parts[1] if len(parts) > 1 else ""

    await state.update_data(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        source=source,
        campaign=campaign,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    welcome_text = (
        "Здравствуйте.\n\n"
        "Рада, что вы здесь. Программа 'Бизнес как продолжение любви' "
        "- это про то, как быть сильной, не ослабляя партнёра. "
        "И как создать дело, которое укрепляет отношения, а не разрушает их.\n\n"
        "Как к вам можно обращаться?"
    )
    await message.answer(welcome_text, reply_markup=ReplyKeyboardRemove())
    await state.set_state(BookingForm.name)


@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Какую задачу хотите решить за 3 месяца?", reply_markup=task_keyboard)
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, status="completed")
    summary = f"📋 Проверьте данные:\n\nИмя: {data['name']}\nРоль: {data['role']}\nЗадача: {data['main_task']}\nВремя: {data['time_of_day']}"
    await message.answer(summary, reply_markup=confirm_keyboard)


@dp.callback_query(lambda c: c.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if finalize_to_main(data):
        await callback.message.edit_text("✅ Спасибо! Данные подтверждены.")
        if ADMIN_TELEGRAM_ID:
            text_admin = (f"👤 Имя: {data.get('name')}\n🎯 Роль: {data.get('role')}\n"
                          f"💡 Задача: {data.get('main_task')}\nTG: @{data.get('username')}")
            try:
                await bot.send_message(ADMIN_TELEGRAM_ID, text_admin)
            except:
                pass
        await state.clear()
    else:
        await callback.answer("Ошибка сохранения.", show_alert=True)


# ================== СЕРВЕР И ВЕБХУК ==================

async def handle_webhook(request):
    logging.info("📥 Входящий запрос на вебхук")
    try:
        body = await request.json()
        update = Update.model_validate(body)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    except Exception as e:
        logging.error(f"❌ Ошибка обработки вебхука: {e}")
        return web.Response(text="error", status=500)


async def handle_health(request):
    return web.Response(text="Bot is running", status=200)


async def on_startup(app):
    # Удаляем старый вебхук перед установкой нового
    await bot.delete_webhook(drop_pending_updates=True)
    webhook_path = f"{WEBHOOK_URL}/webhook"
    await bot.set_webhook(webhook_path)
    logging.info(f"🚀 Вебхук установлен на: {webhook_path}")


async def on_shutdown(app):
    await bot.session.close()


app = web.Application()
app.router.add_get("/", handle_health)
app.router.add_post("/webhook", handle_webhook)  # Упрощенный путь для теста
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)