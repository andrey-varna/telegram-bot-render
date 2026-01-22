import os
import re
import json
import asyncio
import logging
from datetime import datetime, timedelta

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
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
SHEET_KEY = os.getenv("ANALYTICS_SHEET_KEY")  # один ключ для обеих вкладок

if not BOT_TOKEN or not SHEET_KEY:
    raise RuntimeError("BOT_TOKEN или ANALYTICS_SHEET_KEY не задан")

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
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.Client(auth=credentials)
gc.login()

# Открываем вкладки
analytics_sheet = gc.open_by_key(SHEET_KEY).worksheet("leads_main")
unconfirmed_sheet = gc.open_by_key(SHEET_KEY).worksheet("leads_unconfirmed")

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
        [types.KeyboardButton(text="Предприниматель")],
        [types.KeyboardButton(text="Я эксперт/фрилансер")]
    ], resize_keyboard=True
)

business_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Только запускаю")],
        [types.KeyboardButton(text="Действующий бизнес")],
        [types.KeyboardButton(text="Масштабирую")]
    ], resize_keyboard=True
)

partner_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Да")],
        [types.KeyboardButton(text="Нет, но хочу")],
        [types.KeyboardButton(text="Нет")]
    ], resize_keyboard=True
)

request_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Перестать всё контролировать и тащить на себе")],
        [types.KeyboardButton(text="Вернуть страсть и близость, не теряя доход")],
        [types.KeyboardButton(text="Распределить роли, чтобы не конфликтовать")],
        [types.KeyboardButton(text="Выйти на новый уровень дохода без выгорания")]
    ], resize_keyboard=True
)

time_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Утро")],
        [types.KeyboardButton(text="День")],
        [types.KeyboardButton(text="Вечер")]
    ], resize_keyboard=True
)

record_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="record")]
    ]
)

# ================== ФУНКЦИИ ==================
def save_to_analytics(data: dict):
    """Сохраняет структурированные данные в leads_main."""
    records = analytics_sheet.get_all_records()
    ids = [str(r.get("telegram_id", "")) for r in records]

    row = [
        data.get("telegram_id"),
        data.get("username", ""),
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
        analytics_sheet.update(f"A{idx}:L{idx}", [row])
    else:
        analytics_sheet.append_row(row)

def save_to_unconfirmed(data: dict):
    """Сохраняет все ответы в leads_unconfirmed, если статус != confirmed."""
    if data.get("status") != "confirmed":
        row = [
            data.get("telegram_id"),
            data.get("username", ""),
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
        unconfirmed_sheet.append_row(row)

async def send_admin_full_message(data: dict):
    """Отправляет админу все ответы в читаемом виде."""
    msg = (
        f"❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
        f"«Бизнес как продолжение любви»\n\n"
        f"👤 Имя: {data.get('name','не указано')}\n"
        f"🎯 Роль: {data.get('role','не указано')}\n"
        f"💼 Бизнес: {data.get('business_stage','не указано')}\n"
        f"👥 Партнёр: {data.get('partner','не указано')}\n"
        f"💡 Главная задача: {data.get('income','не указано')}\n"
        f"⏰ Время: {data.get('time_of_day','не указано')}\n"
        f"🔗 Telegram: @{data.get('username','не указано')}"
    )
    if ADMIN_TELEGRAM_ID:
        await bot.send_message(ADMIN_TELEGRAM_ID, msg)

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

    save_to_analytics({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "status": "visited",
        "source": source,
        "campaign": campaign
    })

    await message.answer(
        "Здравствуйте.\n\n"
        "Рада, что вы здесь. Программа 'Бизнес как продолжение любви'..."
        "\nКак к вам можно обращаться?"
    )
    await state.set_state(BookingForm.name)

# ================== FSM ==================
@dp.message(BookingForm.name)
async def process_name(message: types.Message, state: FSMContext):
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", message.text):
        await message.answer("Введите корректное имя.")
        return
    await state.update_data(name=message.text)
    save_to_analytics({"telegram_id": message.from_user.id, "username": message.from_user.username, "name": message.text, "status": "visited"})
    save_to_unconfirmed({"telegram_id": message.from_user.id, "username": message.from_user.username, "name": message.text, "status": "needs_followup"})
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()
    save_to_analytics({"telegram_id": message.from_user.id, **data, "status": "visited"})
    save_to_unconfirmed({"telegram_id": message.from_user.id, **data, "status": "needs_followup"})
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)

@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()
    save_to_analytics({"telegram_id": message.from_user.id, **data, "status": "visited"})
    save_to_unconfirmed({"telegram_id": message.from_user.id, **data, "status": "needs_followup"})
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)

@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()
    save_to_analytics({"telegram_id": message.from_user.id, **data, "status": "visited"})
    save_to_unconfirmed({"telegram_id": message.from_user.id, **data, "status": "needs_followup"})
    await message.answer("Главная задача на ближайшие 3 месяца:", reply_markup=request_keyboard)
    await state.set_state(BookingForm.income)

@dp.message(BookingForm.income)
async def process_income(message: types.Message, state: FSMContext):
    await state.update_data(income=message.text)
    data = await state.get_data()
    save_to_analytics({"telegram_id": message.from_user.id, **data, "status": "visited"})
    save_to_unconfirmed({"telegram_id": message.from_user.id, **data, "status": "needs_followup"})
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    data.update({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "status": "needs_followup"
    })

    save_to_analytics(data)
    save_to_unconfirmed(data)
    await send_admin_full_message(data)

    await message.answer(
        "Спасибо. Подтвердите, пожалуйста, введенные данные.",
        reply_markup=record_keyboard
    )
    await state.clear()

# ================== Подтверждение ==================
@dp.callback_query(lambda c: c.data == "record")
async def record_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    records = analytics_sheet.get_all_records()
    ids = [str(r.get("telegram_id","")) for r in records]

    if str(user_id) in ids:
        idx = ids.index(str(user_id)) + 2
        row = analytics_sheet.row_values(idx)
        row[8] = "confirmed"
        analytics_sheet.update(f"A{idx}:L{idx}", [row])

    # Удаляем или помечаем запись в unconfirmed
    unconfirmed_records = unconfirmed_sheet.get_all_records()
    for i, r in enumerate(unconfirmed_records):
        if str(r.get("telegram_id","")) == str(user_id):
            unconfirmed_sheet.update(f"I{i+2}", [["confirmed"]])  # только статус
            break

    await callback.message.answer(
        "Спасибо. Мы свяжемся с вами для подтверждения и согласования времени.",
        reply_markup=ReplyKeyboardRemove()
    )
    await callback.answer()

# ================== FALLBACK ==================
@dp.message()
async def fallback(message: types.Message):
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
