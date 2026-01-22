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
from aiogram.types import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove
)
import logging
logging.basicConfig(level=logging.ERROR)

# ================== КОНФИГУРАЦИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

SPREADSHEET_KEY = "15Z2TztesrsbYVzzg4eWqfe1m_Jr_EDVKGhkdiXb7uAI"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

# ================== ЛОГИ ==================
logging.basicConfig(level=logging.INFO)

# ================== BOT ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================== GOOGLE SHEETS ==================
SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(
    service_account_info, scopes=SCOPES
)

gc = gspread.Client(auth=credentials)
gc.login()

spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
main_sheet = spreadsheet.worksheet("leads_main")
unconfirmed_sheet = spreadsheet.worksheet("leads_unconfirmed")

# ================== HELPERS ==================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_or_update_main(data: dict):
    records = main_sheet.get_all_records()
    ids = [str(r["telegram_id"]) for r in records]

    row = [
        data.get("telegram_id"),
        data.get("username"),
        data.get("name", ""),
        data.get("role", ""),
        data.get("business_stage", ""),
        data.get("partner", ""),
        data.get("time_of_day", ""),
        data.get("status"),
        data.get("source", ""),
        data.get("campaign", ""),
        data.get("started_at"),
        data.get("confirmed_at")
    ]

    if str(data["telegram_id"]) in ids:
        idx = ids.index(str(data["telegram_id"])) + 2
        main_sheet.update(f"A{idx}:L{idx}", [row])
    else:
        main_sheet.append_row(row)


def save_or_update_unconfirmed(data: dict):
    records = unconfirmed_sheet.get_all_records()
    ids = [str(r["telegram_id"]) for r in records]

    row = [
        data.get("telegram_id"),
        data.get("username"),
        data.get("source", ""),
        data.get("campaign", ""),
        data.get("name", ""),
        data.get("role", ""),
        data.get("business_stage", ""),
        data.get("partner", ""),
        data.get("main_task", ""),
        data.get("time_of_day", ""),
        now()
    ]

    if str(data["telegram_id"]) in ids:
        idx = ids.index(str(data["telegram_id"])) + 2
        unconfirmed_sheet.update(f"A{idx}:K{idx}", [row])
    else:
        unconfirmed_sheet.append_row(row)


def delete_unconfirmed(telegram_id: int):
    records = unconfirmed_sheet.get_all_records()
    ids = [str(r["telegram_id"]) for r in records]

    if str(telegram_id) in ids:
        idx = ids.index(str(telegram_id)) + 2
        unconfirmed_sheet.delete_rows(idx)


# ================== FSM ==================
class BookingForm(StatesGroup):
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    main_task = State()
    time_of_day = State()

# ================== КЛАВИАТУРЫ ==================
role_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Собственник бизнеса")],
        [types.KeyboardButton(text="CEO / управляющий")],
        [types.KeyboardButton(text="Предприниматель")],
        [types.KeyboardButton(text="Эксперт / фрилансер")]
    ],
    resize_keyboard=True
)

business_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Только запускаю")],
        [types.KeyboardButton(text="Действующий бизнес")],
        [types.KeyboardButton(text="Масштабирую")]
    ],
    resize_keyboard=True
)

partner_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Да")],
        [types.KeyboardButton(text="Нет, но хочу")],
        [types.KeyboardButton(text="Нет")]
    ],
    resize_keyboard=True
)

time_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Утро")],
        [types.KeyboardButton(text="День")],
        [types.KeyboardButton(text="Вечер")]
    ],
    resize_keyboard=True
)

confirm_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="confirm")]
    ]
)

# ================== START ==================
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()

    args = message.text.split(" ", 1)
    source, campaign = "organic", ""

    if len(args) > 1:
        parts = args[1].split("_", 1)
        source = parts[0]
        campaign = parts[1] if len(parts) > 1 else ""

    await state.update_data(source=source, campaign=campaign)

    save_or_update_main({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "status": "started",
        "source": source,
        "campaign": campaign,
        "started_at": now(),
        "confirmed_at": ""
    })

    save_or_update_unconfirmed({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "source": source,
        "campaign": campaign
    })

    await message.answer(
        "Здравствуйте.\n\nКак к вам можно обращаться?"
    )
    await state.set_state(BookingForm.name)

# ================== ВОПРОСЫ ==================
@dp.message(BookingForm.name)
async def process_name(message: types.Message, state: FSMContext):
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", message.text):
        await message.answer("Введите корректное имя.")
        return

    data = await state.get_data()
    data["name"] = message.text
    await state.update_data(name=message.text)

    save_or_update_unconfirmed({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data
    })

    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()

    save_or_update_unconfirmed({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data
    })

    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()

    save_or_update_unconfirmed({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data
    })

    await message.answer("Есть ли у вас партнёр?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()

    save_or_update_unconfirmed({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data
    })

    await message.answer(
        "Какую главную задачу вы хотите решить в ближайшие 3 месяца?"
    )
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def process_main_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    data = await state.get_data()

    save_or_update_unconfirmed({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data
    })

    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()

    save_or_update_unconfirmed({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data
    })

    await message.answer(
        "Проверьте данные и подтвердите запись.",
        reply_markup=confirm_keyboard
    )
    await state.clear()

# ================== CONFIRM ==================
@dp.callback_query(lambda c: c.data == "confirm")
async def confirm(callback: types.CallbackQuery):
    records = unconfirmed_sheet.get_all_records()
    user_id = callback.from_user.id

    row = next((r for r in records if str(r["telegram_id"]) == str(user_id)), None)
    if not row:
        await callback.answer("Данные не найдены")
        return

    if ADMIN_TELEGRAM_ID:
        await bot.send_message(
            ADMIN_TELEGRAM_ID,
            f"❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
            f"«Бизнес как продолжение любви»\n\n"
            f"👤 Имя: {row['name']}\n"
            f"🎯 Роль: {row['role']}\n"
            f"💼 Бизнес: {row['business_stage']}\n"
            f"👥 Партнёр: {row['partner']}\n"
            f"💡 Главная задача: {row['main_task']}\n"
            f"⏰ Время: {row['time_of_day']}\n"
            f"🔗 Telegram: @{row['username']}"
        )

    save_or_update_main({
        "telegram_id": user_id,
        "username": callback.from_user.username,
        "name": row["name"],
        "role": row["role"],
        "business_stage": row["business_stage"],
        "partner": row["partner"],
        "time_of_day": row["time_of_day"],
        "status": "confirmed",
        "source": row["source"],
        "campaign": row["campaign"],
        "started_at": "",
        "confirmed_at": now()
    })

    delete_unconfirmed(user_id)

    await callback.message.answer(
        "Спасибо. Мы свяжемся с вами для подтверждения времени.",
        reply_markup=ReplyKeyboardRemove()
    )
    await callback.answer()

# ================== WEBHOOK ==================
async def webhook_handler(request):
    try:
        data = await request.json()
        update = Update.model_validate(data)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    except Exception:
        logging.exception("❌ Webhook handler error")
        return web.Response(text="error", status=500)

async def healthcheck(request):
    return web.Response(text="Bot is alive")

app = web.Application()
app.router.add_get("/", healthcheck)
app.router.add_post(WEBHOOK_PATH, webhook_handler)

if __name__ == "__main__":
    logging.info(f"Bot started on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
