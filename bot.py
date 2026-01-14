import asyncio
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ------------------ Настройки ------------------
BOT_TOKEN = "8110652792:AAESu--Mv8-gRjl_GGAi1OPF1NUc3yq3lGc"        # вставьте токен вашего бота
ADMIN_TELEGRAM_ID = 476041868      # ваш числовой Telegram ID для уведомлений
# ------------------------------------------------

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ------------------ FSM ------------------
class BookingForm(StatesGroup):
    name = State()
    role = State()
    time_of_day = State()

# ------------------ Keyboards ------------------
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

# ------------------ Handlers ------------------
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

    # ------------------ сообщение админу ------------------
    admin_message = (
        "❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
        "«Бизнес как продолжение любви»\n\n"
        f"👤 Имя: {data['client_name']}\n"
        f"🎯 Роль: {data['role']}\n"
        f"⏰ Половина дня: {message.text}\n\n"
        f"🔗 Telegram: @{user.username if user.username else 'не указан'}\n"
        f"🆔 ID: {user.id}"
    )
    # Отправка админу в фоне, чтобы не блокировать пользователя
    asyncio.create_task(bot.send_message(ADMIN_TELEGRAM_ID, admin_message))

    # ------------------ ответ пользователю ------------------
    await message.answer(
        "Благодарю.\n\n"
        "Мы с вами свяжемся в Telegram, чтобы согласовать день и свободное время.\n\n"
        "До встречи."
    )

    await state.clear()

@dp.message()
async def fallback(message: Message):
    await message.answer("Для записи на диагностическую сессию используйте команду /start.")

# ------------------ Запуск локального polling ------------------
if __name__ == "__main__":
    print("Бот запущен. Отправьте /start в Telegram.")
    import asyncio
    try:
        asyncio.run(dp.start_polling(bot, skip_updates=True))
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен вручную")
