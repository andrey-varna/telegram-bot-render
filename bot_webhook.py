import os
import re
import asyncio
import logging
import requests
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# ------------------ Конфигурация ------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", 0))
PORT = int(os.environ.get("PORT", 5000))  # Render даёт порт
RENDER_URL = os.environ.get("RENDER_URL")  # Пример: https://srv-d5juasp4tr6s73b3r8pg.onrender.com
WEBHOOK_URL = f"{RENDER_URL}/webhook"

if not BOT_TOKEN or not ADMIN_TELEGRAM_ID or not RENDER_URL:
    raise RuntimeError("Установите BOT_TOKEN, ADMIN_TELEGRAM_ID и RENDER_URL в env")

# ------------------ Логирование ------------------
logging.basicConfig(level=logging.INFO)
logging.getLogger('aiohttp.access').setLevel(logging.INFO)

# ------------------ Инициализация бота ------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ------------------ FSM ------------------
class BookingForm(StatesGroup):
    name = State()
    role = State()
    business_status = State()
    partner_status = State()
    income_range = State()
    time_of_day = State()

# ------------------ Keyboards ------------------
role_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton("Собственник бизнеса")],
        [types.KeyboardButton("CEO / управляющий")],
        [types.KeyboardButton("Предприниматель (стартап / малый бизнес)")]
    ], resize_keyboard=True, one_time_keyboard=True
)
business_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton("Только запускаю")],
        [types.KeyboardButton("Действующий")],
        [types.KeyboardButton("Масштабирую")]
    ], resize_keyboard=True, one_time_keyboard=True
)
partner_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton("Да")],
        [types.KeyboardButton("Нет, но хочу")],
        [types.KeyboardButton("Нет, мы в разных сферах")]
    ], resize_keyboard=True, one_time_keyboard=True
)
income_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton("До 50 000 ₽")],
        [types.KeyboardButton("50 000 – 200 000 ₽")],
        [types.KeyboardButton("200 000 – 500 000 ₽")],
        [types.KeyboardButton("Более 500 000 ₽")]
    ], resize_keyboard=True, one_time_keyboard=True
)
time_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton("Утро")],
        [types.KeyboardButton("День")],
        [types.KeyboardButton("Вечер")]
    ], resize_keyboard=True, one_time_keyboard=True
)

# ------------------ Handlers ------------------
@dp.message()
async def fallback(message: types.Message):
    await message.answer("Для записи на сессию используйте /start.")

@dp.message(commands=["start"])
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Здравствуйте! Как к вам можно обращаться?")
    await state.set_state(BookingForm.name)

@dp.message(BookingForm.name)
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", name):
        await message.answer("Введите имя без цифр и спецсимволов.")
        return
    await state.update_data(client_name=name)
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_status)

@dp.message(BookingForm.business_status)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_status=message.text)
    await message.answer("Ваш партнер в бизнесе?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner_status)

@dp.message(BookingForm.partner_status)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner_status=message.text)
    await message.answer("Ваш текущий доход:", reply_markup=income_keyboard)
    await state.set_state(BookingForm.income_range)

@dp.message(BookingForm.income_range)
async def process_income(message: types.Message, state: FSMContext):
    await state.update_data(income_range=message.text)
    await message.answer("Выберите удобную половину дня для сессии:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user

    admin_message = (
        "❤️ НОВЫЙ ЛИД\n"
        f"Имя: {data['client_name']}\n"
        f"Роль: {data['role']}\n"
        f"Бизнес: {data['business_status']}\n"
        f"Партнер: {data['partner_status']}\n"
        f"Доход: {data['income_range']}\n"
        f"Половина дня: {message.text}\n"
        f"Telegram: @{user.username if user.username else 'не указан'}\n"
        f"ID: {user.id}"
    )
    asyncio.create_task(bot.send_message(ADMIN_TELEGRAM_ID, admin_message))

    await message.answer("Спасибо! Мы свяжемся с вами в Telegram.", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()

# ------------------ Webhook ------------------
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.exception(f"Ошибка обработки update: {e}")
    return web.Response(text="ok")

# ------------------ Healthcheck ------------------
async def healthcheck(request: web.Request):
    return web.Response(text="Bot is alive")

# ------------------ Проверка webhook ------------------
def check_and_set_webhook():
    info = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo").json()
    current = info.get("result", {}).get("url")
    print("Текущий webhook:", current)
    if current != WEBHOOK_URL:
        print("Webhook не совпадает. Устанавливаем новый...")
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", data={"url": WEBHOOK_URL}).json()
        if r.get("ok"):
            print("Webhook успешно установлен:", WEBHOOK_URL)
        else:
            print("Ошибка установки webhook:", r.get("description"))
    else:
        print("Webhook уже установлен.")

# ------------------ Run server ------------------
if __name__ == "__main__":
    check_and_set_webhook()
    app = web.Application()
    app.router.add_get("/", healthcheck)
    app.router.add_post("/webhook", handle_webhook)
    logging.info(f"Запуск сервера на порту {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
