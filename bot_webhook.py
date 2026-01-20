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
    "https://www.googleapis.com/auth/drive",
]

service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.Client(auth=credentials)

sheet = gc.open_by_key("15Z2TztesrsbYVzzg4eWqfe1m_Jr_EDVKGhkdiXb7uAI").sheet1

def save_or_update_user(data: dict):
    records = sheet.get_all_records()
    ids = [str(r.get("telegram_id")) for r in records]

    row = [
        data.get("telegram_id"),
        data.get("username"),
        data.get("name", ""),
        data.get("role", ""),
        data.get("business_stage", ""),
        data.get("partner", ""),
        data.get("income", ""),
        data.get("time_of_day", ""),
        data.get("status", ""),
        data.get("source", ""),
        data.get("campaign", ""),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ]

    if str(data["telegram_id"]) in ids:
        idx = ids.index(str(data["telegram_id"])) + 2
        sheet.update(f"A{idx}:L{idx}", [row])
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

    save_or_update_user({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "status": "visited",
        "source": source,
        "campaign": campaign
    })

    await message.answer(
        "Здравствуйте.\n\n"
        "Чтобы диагностика была максимально полезной, "
        "ответьте, пожалуйста, на несколько вопросов.\n\n"
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
    save_or_update_user({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "name": message.text,
        "status": "in_progress"
    })
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def process_role(message, state):
    await state.update_data(role=message.text)
    data = await state.get_data()
    save_or_update_user({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data,
        "role": message.text,
        "status": "in_progress"
    })
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)

@dp.message(BookingForm.business_stage)
async def process_business(message, state):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()
    save_or_update_user({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data,
        "business_stage": message.text,
        "status": "in_progress"
    })
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)

@dp.message(BookingForm.partner)
async def process_partner(message, state):
    await state.update_data(partner=message.text)
    data = await state.get_data()
    save_or_update_user({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data,
        "partner": message.text,
        "status": "in_progress"
    })
    await message.answer("Ваш текущий доход:", reply_markup=income_keyboard)
    await state.set_state(BookingForm.income)

@dp.message(BookingForm.income)
async def process_income(message, state):
    await state.update_data(income=message.text)
    data = await state.get_data()
    save_or_update_user({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data,
        "income": message.text,
        "status": "in_progress"
    })
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def process_time(message, state):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    save_or_update_user({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        **data,
        "time_of_day": message.text,
        "status": "needs_followup"
    })
    if ADMIN_TELEGRAM_ID:
        asyncio.create_task(
            bot.send_message(
                ADMIN_TELEGRAM_ID,
                f"⚠️ ЛИД НА ДОРАБОТКУ\n\n"
                f"{data['name']} — {data['business_stage']}\n"
                f"Источник: {data['source']} / {data['campaign']}"
            )
        )
    await message.answer(
        "Спасибо. Ниже вы можете записаться на консультацию.",
        reply_markup=record_keyboard
    )
    await state.clear()

# ================== ЗАПИСАТЬСЯ ==================
@dp.callback_query(lambda c: c.data == "record")
async def record_callback(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()  # Получаем текущее состояние (если есть)
    # Обновляем в таблице статус
    save_or_update_user({
        "telegram_id": callback.from_user.id,
        "username": callback.from_user.username,
        **data,
        "status": "recorded"
    })
    await callback.message.answer(
        "Спасибо. Мы свяжемся с вами для подтверждения и согласования времени."
    )
    await state.clear()
    await callback.answer()

# ================== FALLBACK ==================
@dp.message()
async def fallback(message):
    await message.answer("Используйте /start")

# ================== WEBHOOK ==================
async def webhook_handler(request):
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return web.Response(text="ok")

async def healthcheck(request):
    return web.Response(text="Bot is alive")

# ================== APP ==================
app = web.Application()
app.router.add_get("/", healthcheck)
app.router.add_post(WEBHOOK_PATH, webhook_handler)

if __name__ == "__main__":
    logging.info(f"Bot started on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
