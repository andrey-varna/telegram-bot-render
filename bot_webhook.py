import os
import re
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, Update

# ---------------- Настройки ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # На Render добавьте переменную BOT_TOKEN
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан! Установите переменную окружения BOT_TOKEN.")

ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID", 0))  # ID администратора
PORT = int(os.environ.get("PORT", 5000))  # Render сам даёт PORT, локально можно 5000

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------------- FSM ----------------
class BookingForm(StatesGroup):
    name = State()
    role = State()
    time_of_day = State()

# ---------------- Keyboards ----------------
role_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Собственник бизнеса")],
        [KeyboardButton(text="CEO / управляющий")],
        [KeyboardButton(text="Предприниматель (стартап / малый бизнес)")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

time_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Утро")],
        [KeyboardButton(text="День")],
        [KeyboardButton(text="Вечер")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

# ---------------- Handlers ----------------
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Здравствуйте.\n\n"
        "Вы записываетесь на диагностическую сессию\n"
        "«Бизнес как продолжение любви».\n\n"
        "Для начала, подскажите, как к вам можно обращаться?"
    )
    await state.set_state(BookingForm.name)

@dp.message(BookingForm.name)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not re.match(r"^[A-Za-zА-Яа-яЁё\s\-]{2,30}$", name):
        await message.answer("Пожалуйста, укажите имя без цифр и специальных символов.")
        return
    await state.update_data(client_name=name)
    await message.answer(
        "Спасибо.\n\nУточните вашу текущую роль в бизнесе:",
        reply_markup=role_keyboard
    )
    await state.set_state(BookingForm.role)

@dp.message(
    BookingForm.role,
    F.text.in_([
        "Собственник бизнеса",
        "CEO / управляющий",
        "Предприниматель (стартап / малый бизнес)"
    ])
)
async def process_role(message: Message, state: FSMContext):
    await state.update_data(role=message.text)
    await message.answer(
        "Выберите удобную половину дня для сессии:",
        reply_markup=time_keyboard
    )
    await state.set_state(BookingForm.time_of_day)

@dp.message(
    BookingForm.time_of_day,
    F.text.in_(["Утро", "День", "Вечер"])
)
async def process_time(message: Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user

    # Сообщение админу
    admin_message = (
        "❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
        "«Бизнес как продолжение любви»\n\n"
        f"👤 Имя: {data['client_name']}\n"
        f"🎯 Роль: {data['role']}\n"
        f"⏰ Половина дня: {message.text}\n\n"
        f"🔗 Telegram: @{user.username if user.username else 'не указан'}\n"
        f"🆔 ID: {user.id}"
    )
    asyncio.create_task(bot.send_message(ADMIN_TELEGRAM_ID, admin_message))

    # Ответ пользователю
    await message.answer(
        "Благодарю.\n\n"
        "Мы с вами свяжемся в Telegram, чтобы согласовать день и свободное время.\n\n"
        "До встречи."
    )
    await state.clear()

@dp.message()
async def fallback(message: Message):
    await message.answer("Для записи на диагностическую сессию используйте команду /start.")

# ---------------- Webhook endpoint ----------------
async def handle_webhook(request: web.Request):
    try:
        update = await request.json()
        logging.info(f"Incoming update: {update}")
        await dp.process_update(Update(**update))
    except Exception as e:
        logging.exception(f"Ошибка обработки update: {e}")
    return web.Response(text="ok")

# ---------------- Приложение ----------------
app = web.Application()
app.add_routes([
    web.post(f"/webhook/{BOT_TOKEN}", handle_webhook),
    web.get("/", lambda request: web.Response(text="Сервер жив!"))
])

# ---------------- Запуск сервера ----------------
if __name__ == "__main__":
    logging.info(f"Бот запущен. PORT={PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
