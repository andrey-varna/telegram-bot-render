import os
import re
import asyncio
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Update

# ================== КОНФИГУРАЦИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

# ================== ЛОГИ ==================
logging.basicConfig(level=logging.INFO)

# ================== BOT ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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
        [types.KeyboardButton(text="Предприниматель (стартап / малый бизнес)")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

business_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Только запускаю")],
        [types.KeyboardButton(text="Действующий бизнес")],
        [types.KeyboardButton(text="Масштабирую")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

partner_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Да")],
        [types.KeyboardButton(text="Нет, но хочу")],
        [types.KeyboardButton(text="Нет, мы в разных сферах")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

income_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="До 50 000")],
        [types.KeyboardButton(text="50 000 – 200 000")],
        [types.KeyboardButton(text="200 000 – 500 000")],
        [types.KeyboardButton(text="Более 500 000")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

time_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="Утро")],
        [types.KeyboardButton(text="День")],
        [types.KeyboardButton(text="Вечер")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

# ================== HANDLERS ==================
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Здравствуйте.\n\n"
        "Я бот Татьяны Прокопчук.\n\n"
        "Рада, что вы решились уделить время себе и своему делу.\n\n"
        "Диагностика — это точка, где заканчивается автоматика и \n\n"
        "начинается осознанное управление жизнью.\n\n"
        "Чтобы подобрать время для диагностики и провести ее максимально полезно,\n\n"
        "ответьте, пожалуйста, на несколько вопросов:\n\n"
        "Как к вам можно обращаться?"
    )
    await state.set_state(BookingForm.name)

@dp.message(BookingForm.name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", name):
        await message.answer("Введите корректное имя.")
        return

    await state.update_data(name=name)
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)

@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)

@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    await message.answer("Ваш текущий доход:", reply_markup=income_keyboard)
    await state.set_state(BookingForm.income)

@dp.message(BookingForm.income)
async def process_income(message: types.Message, state: FSMContext):
    await state.update_data(income=message.text)
    await message.answer("Удобная половина дня для консультации:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user

    admin_text = (
        "🔥 НОВЫЙ ЛИД\n\n"
        f"👤 Имя: {data['name']}\n"
        f"🎯 Роль: {data['role']}\n"
        f"🏢 Бизнес: {data['business_stage']}\n"
        f"🤝 Партнер: {data['partner']}\n"
        f"💰 Доход: {data['income']}\n"
        f"⏰ Время: {message.text}\n\n"
        f"🔗 @{user.username or 'не указан'}\n"
        f"🆔 ID: {user.id}"
    )

    if ADMIN_TELEGRAM_ID:
        asyncio.create_task(bot.send_message(ADMIN_TELEGRAM_ID, admin_text))

    await message.answer(
        "Спасибо. Мы свяжемся с вами для подтверждения консультации.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.clear()

@dp.message()
async def fallback(message: types.Message):
    await message.answer("Для начала используйте команду /start")

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

# ================== RUN ==================
if __name__ == "__main__":
    logging.info(f"Bot started on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
