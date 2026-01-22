import os
import json
import re
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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

ANALYTICS_SHEET_KEY = os.getenv("ANALYTICS_SHEET_KEY")
UNCONFIRMED_SHEET_KEY = os.getenv("UNCONFIRMED_SHEET_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")
if not ANALYTICS_SHEET_KEY:
    raise RuntimeError("ANALYTICS_SHEET_KEY не задан")
if not UNCONFIRMED_SHEET_KEY:
    raise RuntimeError("UNCONFIRMED_SHEET_KEY не задан")

# ================== LOGGING ==================
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
gc.login()

analytics_sheet = gc.open_by_key(ANALYTICS_SHEET_KEY).sheet1
unconfirmed_sheet = gc.open_by_key(UNCONFIRMED_SHEET_KEY).sheet1

# ================== FSM ==================
class BookingForm(StatesGroup):
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    income = State()
    time_of_day = State()

# ================== KEYBOARDS ==================
role_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Собственник бизнеса")],
        [types.KeyboardButton(text="CEO / управляющий")],
        [types.KeyboardButton(text="Предприниматель")],
        [types.KeyboardButton(text="Я эксперт/фрилансер")]
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
        [types.KeyboardButton(text="Перестать всё контролировать и тащить на себе")],
        [types.KeyboardButton(text="Вернуть страсть и близость, не теряя доход")],
        [types.KeyboardButton(text="Распределить роли, чтобы не конфликтовать")],
        [types.KeyboardButton(text="Выйти на новый уровень дохода без выгорания")]
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
        [InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="record")]
    ]
)

# ================== HELPER FUNCTIONS ==================
def save_structured(sheet_obj, data: dict):
    """Сохраняет структурированные данные в указанную таблицу."""
    records = sheet_obj.get_all_records()
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
        sheet_obj.update(f"A{idx}:L{idx}", [row])
    else:
        sheet_obj.append_row(row)

async def send_admin_full(data: dict):
    """Отправляет админу полный контекст после подтверждения."""
    row_text = (
        f"❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
        f"«Бизнес как продолжение любви»\n\n"
        f"👤 Имя: {data.get('name','не указано')}\n"
        f"🎯 Роль: {data.get('role','не указано')}\n"
        f"💼 Бизнес: {data.get('business_stage','не указано')}\n"
        f"👥 Партнёр: {data.get('partner','не указано')}\n"
        f"💡 Главная задача: {data.get('income','не указано')}\n"
        f"⏰ Время: {data.get('time_of_day','не указано')}\n"
        f"🔗 Телеграм: @{data.get('username','не указано')}"
    )
    if ADMIN_TELEGRAM_ID:
        await bot.send_message(ADMIN_TELEGRAM_ID, row_text)

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
    save_structured(analytics_sheet, {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "status": "visited",
        "source": source,
        "campaign": campaign
    })

    await message.answer(
        "Здравствуйте. Программа 'Бизнес как продолжение любви'.\n\n"
        "Чтобы подготовиться к диагностической сессии, нужно ответить на несколько вопросов.\n"
        "Как к вам можно обращаться?"
    )
    await state.set_state(BookingForm.name)

# ================== QUESTION HANDLERS ==================
@dp.message(BookingForm.name)
async def process_name(message: types.Message, state: FSMContext):
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", message.text):
        await message.answer("Введите корректное имя.")
        return
    await state.update_data(name=message.text)
    save_structured(analytics_sheet, {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "name": message.text,
        "status": "visited"
    })
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    save_structured(analytics_sheet, {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "role": message.text,
        "status": "visited"
    })
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)

@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    save_structured(analytics_sheet, {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "business_stage": message.text,
        "status": "visited"
    })
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)

@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    save_structured(analytics_sheet, {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "partner": message.text,
        "status": "visited"
    })
    await message.answer("Какую главную задачу вы хотите решить в ближайшие 3 месяца?", reply_markup=income_keyboard)
    await state.set_state(BookingForm.income)

@dp.message(BookingForm.income)
async def process_income(message: types.Message, state: FSMContext):
    await state.update_data(income=message.text)
    save_structured(analytics_sheet, {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "income": message.text,
        "status": "visited"
    })
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    data["telegram_id"] = message.from_user.id
    data["username"] = message.from_user.username
    data["status"] = "needs_followup"

    # Сохраняем в обе таблицы
    save_structured(analytics_sheet, data)
    save_structured(unconfirmed_sheet, data)

    await message.answer("Спасибо. Подтвердите, пожалуйста, введенные данные.", reply_markup=record_keyboard)
    await state.clear()

# ================== CALLBACK ==================
@dp.callback_query(lambda c: c.data == "record")
async def record_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    records = analytics_sheet.get_all_records()
    ids = [str(r.get("telegram_id", "")) for r in records]
    if str(user_id) in ids:
        idx = ids.index(str(user_id)) + 2
        row = records[idx-2]
        row_data = {
            "telegram_id": user_id,
            "username": row.get("username", ""),
            "name": row.get("name", ""),
            "role": row.get("role", ""),
            "business_stage": row.get("business_stage", ""),
            "partner": row.get("partner", ""),
            "income": row.get("income", ""),
            "time_of_day": row.get("time_of_day", ""),
            "status": "confirmed",
            "source": row.get("source", ""),
            "campaign": row.get("campaign", "")
        }
        # Обновляем статус
        save_structured(analytics_sheet, row_data)
        # Отправляем админу полный контекст
        await send_admin_full(row_data)
        # Удаляем из unconfirmed
        unconfirmed_records = unconfirmed_sheet.get_all_records()
        unconfirmed_ids = [str(r.get("telegram_id","")) for r in unconfirmed_records]
        if str(user_id) in unconfirmed_ids:
            del_idx = unconfirmed_ids.index(str(user_id)) + 2
            unconfirmed_sheet.delete_row(del_idx)

    await callback.message.answer("Спасибо. Мы свяжемся с вами для согласования времени.", reply_markup=ReplyKeyboardRemove())
    await callback.answer()

# ================== FALLBACK ==================
@dp.message()
async def fallback(message: types.Message):
    await message.answer("Используйте /start")

# ================== WEBHOOK ==================
async def webhook_handler(request):
    try:
        data = await request.json()
        from aiogram.types import Update
        update = Update.model_validate(data)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    except Exception as e:
        logging.exception("❌ Webhook handler error")
        return web.Response(text="error", status=500)

async def healthcheck(request):
    return web.Response(text="Bot is alive")

# ================== APP ==================
app = web.Application()
app.router.add_get("/", healthcheck)
app.router.add_post(f"/webhook/{BOT_TOKEN}", webhook_handler)

# ================== MAIN ==================
async def main():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Bot started on port {PORT}")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
