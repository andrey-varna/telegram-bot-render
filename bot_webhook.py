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
from aiogram.types import Update, InlineKeyboardButton, InlineKeyboardMarkup

# ================== КОНФИГУРАЦИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

# ================== ЛОГИ ==================
logging.basicConfig(level=logging.INFO)

# ================== BOT ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================== GOOGLE SHEETS ==================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.Client(auth=credentials)
sheet = gc.open_by_key("15Z2TztesrsbYVzzg4eWqfe1m_Jr_EDVKGhkdiXb7uAI").sheet1

def save_user_field(user_id: int, field: str, value: str, extra_data=None):
    """Сохраняет отдельное поле пользователя в таблицу сразу."""
    extra_data = extra_data or {}
    records = sheet.get_all_records()
    ids = [str(r.get("telegram_id")) for r in records]
    row = [None] * 11  # A-K

    # Заполняем данные если есть в extra_data
    keys = ["telegram_id", "username", "name", "role", "business_stage",
            "partner", "income", "time_of_day", "status", "source", "campaign"]

    for idx, key in enumerate(keys):
        if key in extra_data:
            row[idx] = extra_data[key]

    # Обновляем только поле field
    field_map = {
        "telegram_id": 0,
        "username": 1,
        "name": 2,
        "role": 3,
        "business_stage": 4,
        "partner": 5,
        "income": 6,
        "time_of_day": 7,
        "status": 8,
        "source": 9,
        "campaign": 10
    }
    if field in field_map:
        row[field_map[field]] = value

    if str(user_id) in ids:
        idx = ids.index(str(user_id)) + 2
        sheet.update(f"A{idx}:K{idx}", [row])
    else:
        sheet.append_row(row)

# ================== FSM ==================
class BookingForm(StatesGroup):
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    income = State()
    time_of_day = State()

# ================== КЛАВИАТУРЫ ==================
role_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Собственник бизнеса")],
        [types.KeyboardButton(text="CEO / управляющий")],
        [types.KeyboardButton(text="Предприниматель")]
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

income_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="До 50 000")],
        [types.KeyboardButton(text="50 000 – 200 000")],
        [types.KeyboardButton(text="200 000 – 500 000")],
        [types.KeyboardButton(text="Более 500 000")]
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

record_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📅 Записаться на консультацию", callback_data="record")]
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

    save_user_field(
        message.from_user.id,
        "status",
        "visited",
        extra_data={
            "telegram_id": message.from_user.id,
            "username": message.from_user.username,
            "source": source,
            "campaign": campaign
        }
    )

    await message.answer(
        "Здравствуйте.\n\n"
        "Чтобы диагностика была максимально полезной, "
        "ответьте на несколько вопросов.\n\n"
        "Как к вам можно обращаться?"
    )
    await state.set_state(BookingForm.name)

# ================== ВОПРОСЫ ==================
@dp.message(BookingForm.name)
async def process_name(message: types.Message, state: FSMContext):
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", message.text):
        await message.answer("Введите корректное имя.")
        return
    await state.update_data(name=message.text)
    save_user_field(message.from_user.id, "name", message.text)
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    save_user_field(message.from_user.id, "role", message.text)
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)

@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    save_user_field(message.from_user.id, "business_stage", message.text)
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)

@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    save_user_field(message.from_user.id, "partner", message.text)
    await message.answer("Ваш текущий доход:", reply_markup=income_keyboard)
    await state.set_state(BookingForm.income)

@dp.message(BookingForm.income)
async def process_income(message: types.Message, state: FSMContext):
    await state.update_data(income=message.text)
    save_user_field(message.from_user.id, "income", message.text)
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    save_user_field(message.from_user.id, "time_of_day", message.text, extra_data={"status": "needs_followup"})

    if ADMIN_TELEGRAM_ID:
        asyncio.create_task(
            bot.send_message(
                ADMIN_TELEGRAM_ID,
                f"⚠️ ЛИД НА ДОРАБОТКУ\n\n"
                f"{message.from_user.full_name} — {message.text}\n"
                f"ID: {message.from_user.id}"
            )
        )

    await message.answer(
        "Спасибо. Ниже вы можете записаться на консультацию.",
        reply_markup=record_keyboard
    )
    await state.clear()

# ================== ЗАПИСАТЬСЯ ==================
@dp.callback_query(lambda c: c.data == "record")
async def record_callback(callback: types.CallbackQuery):
    save_user_field(callback.from_user.id, "status", "recorded")
    await callback.message.answer("Спасибо. Мы свяжемся с вами для подтверждения и согласования времени.")
    await callback.answer()

# ================== FALLBACK ==================
@dp.message()
async def fallback(message: types.Message):
    await message.answer("Используйте /start")

# ================== WEBHOOK ==================
async def webhook_handler(request: web.Request):
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return web.Response(text="ok")

async def healthcheck(request: web.Request):
    return web.Response(text="Bot is alive")

# ================== APP ==================
app = web.Application()
app.router.add_get("/", healthcheck)
app.router.add_post(WEBHOOK_PATH, webhook_handler)

if __name__ == "__main__":
    logging.info(f"Bot started on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
