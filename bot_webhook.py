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
SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.Client(auth=credentials)
gc.login()

# Ключи таблиц
ANALYTICS_SHEET_KEY = "15Z2TztesrsbYVzzg4eWqfe1m_Jr_EDVKGhkdiXb7uAI"
LIVE_SHEET_KEY = "15Z2TztesrsbYVzzg4eWqfe1m_Jr_EDVKGhkdiXb7uAI"

analytics_sheet = gc.open_by_key(ANALYTICS_SHEET_KEY).sheet1
live_sheet = gc.open_by_key(LIVE_SHEET_KEY).sheet1

# ================== ФУНКЦИИ СОХРАНЕНИЯ ==================
def save_analytics(data: dict):
    records = analytics_sheet.get_all_records()
    ids = [str(r.get("telegram_id","")) for r in records]

    row = [
        data.get("telegram_id"),
        data.get("username"),
        data.get("name",""),
        data.get("role",""),
        data.get("business_stage",""),
        data.get("partner",""),
        data.get("time_of_day",""),
        data.get("status")
    ]

    if str(data["telegram_id"]) in ids:
        idx = ids.index(str(data["telegram_id"])) + 2
        analytics_sheet.update(f"A{idx}:H{idx}", [row])
    else:
        analytics_sheet.append_row(row)

def save_live(data: dict):
    records = live_sheet.get_all_records()
    ids = [str(r.get("telegram_id","")) for r in records]

    row = [
        data.get("telegram_id"),
        data.get("username"),
        data.get("name",""),
        data.get("role",""),
        data.get("business_stage",""),
        data.get("partner",""),
        data.get("income",""),
        data.get("time_of_day",""),
        data.get("status")
    ]

    if str(data["telegram_id"]) in ids:
        idx = ids.index(str(data["telegram_id"])) + 2
        live_sheet.update(f"A{idx}:I{idx}", [row])
    else:
        live_sheet.append_row(row)

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

    save_analytics({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "status": "visited"
    })
    save_live({
        "telegram_id": message.from_user.id,
        "username": message.from_user.username,
        "status": "visited"
    })

    await message.answer(
        "Здравствуйте.\n\n"
        "Рада, что вы здесь. Программа 'Бизнес как продолжение любви' "
        "- это про то, как быть сильной, не ослабляя партнёра. "
        "И как создать дело, которое укрепляет отношения, а не разрушает их.\n\n"
        "Диагностика - это первый шаг к тому, чтобы увидеть свою жизнь "
        "как систему. За 40-60 минут мы найдём ключевые точки, "
        "где сейчас утекает ваша энергия и сила.\n"
        "Увидим, что даёт вам опору, а что тормозит движение.\n\n"
        "Чтобы подготовиться и провести сессию максимально эффективно, "
        "мне важно узнать о вас немного больше. Ответьте на несколько вопросов.\n\n"
        "Как к вам можно обращаться?"
    )
    await state.set_state(BookingForm.name)

# ================== ВОПРОСЫ ==================
async def save_all_data(user_id, username, data, status="pending_confirmation"):
    save_analytics({
        "telegram_id": user_id,
        "username": username,
        "name": data.get("name",""),
        "role": data.get("role",""),
        "business_stage": data.get("business_stage",""),
        "partner": data.get("partner",""),
        "time_of_day": data.get("time_of_day",""),
        "status": status
    })
    save_live({
        "telegram_id": user_id,
        "username": username,
        **data,
        "status": status
    })

@dp.message(BookingForm.name)
async def process_name(message: types.Message, state: FSMContext):
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", message.text):
        await message.answer("Введите корректное имя.")
        return
    await state.update_data(name=message.text)
    data = await state.get_data()
    await save_all_data(message.from_user.id, message.from_user.username, data)
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()
    await save_all_data(message.from_user.id, message.from_user.username, data)
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)

@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()
    await save_all_data(message.from_user.id, message.from_user.username, data)
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)

@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()
    await save_all_data(message.from_user.id, message.from_user.username, data)
    await message.answer(
        "Какую главную задачу вы хотите решить в ближайшие 3 месяца?:",
        reply_markup=income_keyboard
    )
    await state.set_state(BookingForm.income)

@dp.message(BookingForm.income)
async def process_income(message: types.Message, state: FSMContext):
    await state.update_data(income=message.text)
    data = await state.get_data()
    await save_all_data(message.from_user.id, message.from_user.username, data)
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username

    await save_all_data(user_id, username, data)

    # Отправка админу всех данных
    if ADMIN_TELEGRAM_ID:
        await bot.send_message(
            ADMIN_TELEGRAM_ID,
            f"❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
            f"«Бизнес как продолжение любви»\n\n"
            f"👤 Имя: {data.get('name','не указано')}\n"
            f"🎯 Роль: {data.get('role','не указано')}\n"
            f"💼 Бизнес: {data.get('business_stage','не указано')}\n"
            f"👥 Партнёр: {data.get('partner','не указано')}\n"
            f"💡 Главная задача: {data.get('income','не указано')}\n"
            f"⏰ Время: {data.get('time_of_day','не указано')}\n"
            f"🔗 Telegram: @{username}"
        )

    await message.answer(
        "Проверьте введенные данные. Если всё верно, нажмите кнопку для подтверждения.",
        reply_markup=record_keyboard
    )
    await state.clear()

# ================== CALLBACK ==================
@dp.callback_query(lambda c: c.data == "record")
async def record_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.username

    # Обновляем статус в аналитике
    all_rows = analytics_sheet.get_all_records()
    ids = [str(r.get("telegram_id","")) for r in all_rows]
    if str(user_id) in ids:
        idx = ids.index(str(user_id)) + 2
        analytics_sheet.update(f"H{idx}", "confirmed")

    # Живая таблица: очищаем лишние поля после отправки админу
    live_rows = live_sheet.get_all_records()
    live_ids = [str(r.get("telegram_id","")) for r in live_rows]
    if str(user_id) in live_ids:
        idx = live_ids.index(str(user_id)) + 2
        row = live_sheet.row_values(idx)
        # очищаем лишние поля (оставляем только аналитические)
        live_sheet.update(f"A{idx}:I{idx}", [[row[0], row[1], row[2], row[3], row[4], row[5], "", row[7], "confirmed"]])

    await callback.message.answer(
        "Спасибо. Мы свяжемся с вами для подтверждения и согласования времени.",
        reply_markup=ReplyKeyboardRemove()
    )
    await callback.answer()

# ================== FOLLOW-UP ==================
async def follow_up_unconfirmed():
    while True:
        await asyncio.sleep(600)  # 10 минут
        all_rows = analytics_sheet.get_all_records()
        for idx, r in enumerate(all_rows, start=2):
            if r.get("status") == "pending_confirmation":
                if ADMIN_TELEGRAM_ID:
                    await bot.send_message(
                        ADMIN_TELEGRAM_ID,
                        f"⚠️ ЛИД НА ДОРАБОТКУ\n\n"
                        f"Имя: {r.get('name','не указано')}\n"
                        f"Telegram: @{r.get('username','не указано')}\n"
                        f"Связаться для подтверждения информации."
                    )
                analytics_sheet.update(f"H{idx}", "needs_followup")

asyncio.create_task(follow_up_unconfirmed())

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
