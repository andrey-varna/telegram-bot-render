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
MAIN_SHEET_KEY = os.getenv("MAIN_SHEET_KEY")  # leads_main
UNCONFIRMED_SHEET_KEY = os.getenv("UNCONFIRMED_SHEET_KEY")  # leads_unconfirmed
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN or not MAIN_SHEET_KEY or not UNCONFIRMED_SHEET_KEY or "GOOGLE_SERVICE_ACCOUNT_JSON" not in os.environ:
    raise RuntimeError("BOT_TOKEN или ключи таблиц не заданы")

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
gc.login()  # авторизация

main_sheet = gc.open_by_key(MAIN_SHEET_KEY).sheet1
unconfirmed_sheet = gc.open_by_key(UNCONFIRMED_SHEET_KEY).sheet1


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

task_keyboard = types.ReplyKeyboardMarkup(
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

confirm_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="record")]
    ]
)


# ================== ФУНКЦИИ ==================
def save_main_user(data: dict):
    """Сохраняет или обновляет пользователя в основной таблице."""
    records = main_sheet.get_all_records()
    ids = [str(r.get("telegram_id", "")) for r in records]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Порядок колонок: A-L (12 колонок)
    # A: telegram_id, B: username, C: name, D: role, E: business_stage,
    # F: partner, G: time_of_day, H: status, I: source, J: campaign,
    # K: started_at, L: confirmed_at
    row = [
        str(data.get("telegram_id", "")),
        data.get("username", ""),
        data.get("name", ""),
        data.get("role", ""),
        data.get("business_stage", ""),
        data.get("partner", ""),
        data.get("time_of_day", ""),
        data.get("status", "needs_followup"),
        data.get("source", ""),
        data.get("campaign", ""),
        data.get("started_at", now_str),
        data.get("confirmed_at", "")
    ]

    if str(data["telegram_id"]) in ids:
        idx = ids.index(str(data["telegram_id"])) + 2
        main_sheet.update(f"A{idx}:L{idx}", [row])
    else:
        main_sheet.append_row(row)


def save_unconfirmed_user(data: dict):
    """Сохраняет или обновляет пользователя во временной таблице для дожима."""
    records = unconfirmed_sheet.get_all_records()
    ids = [str(r.get("telegram_id", "")) for r in records]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Порядок колонок: A-K (11 колонок)
    row = [
        str(data.get("telegram_id", "")),
        data.get("username", ""),
        data.get("source", ""),
        data.get("campaign", ""),
        data.get("name", ""),
        data.get("role", ""),
        data.get("business_stage", ""),
        data.get("partner", ""),
        data.get("main_task", ""),
        data.get("time_of_day", ""),
        now_str
    ]

    if str(data["telegram_id"]) in ids:
        idx = ids.index(str(data["telegram_id"])) + 2
        unconfirmed_sheet.update(f"A{idx}:K{idx}", [row])
    else:
        unconfirmed_sheet.append_row(row)


def delete_unconfirmed_user(telegram_id):
    """Удаляет запись из временной таблицы."""
    records = unconfirmed_sheet.get_all_records()
    ids = [str(r.get("telegram_id", "")) for r in records]
    if str(telegram_id) in ids:
        idx = ids.index(str(telegram_id)) + 2
        unconfirmed_sheet.delete_rows(idx)


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

    await state.update_data(
        source=source,
        campaign=campaign,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        telegram_id=message.from_user.id,
        username=message.from_user.username or ""
    )

    await message.answer(
        "Здравствуйте.\n\n"
        "Рада, что вы здесь. Программа 'Бизнес как продолжение любви' "
        "- это про то, как быть сильной, не ослабляя партнёра. "
        "И как создать дело, которое укрепляет отношения, а не разрушает их.\n\n"
        "Диагностика - это первый шаг к тому, чтобы увидеть свою жизнь "
        "как систему. За 40-60 минут мы найдём ключевые точки, "
        "где сейчас утекает ваша энергия и сила. "
        "Увидим, что даёт вам опору, а что тормозит движение.\n\n"
        "Чтобы подготовиться и провести сессию максимально эффективно, "
        "мне важно узнать о вас немного больше. "
        "Ответьте, пожалуйста, на несколько вопросов - это займёт 2-3 минуты.\n\n"
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
    data = await state.get_data()
    save_main_user({**data, "status": "started"})
    save_unconfirmed_user(data)
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()
    save_main_user({**data, "status": "needs_followup"})
    save_unconfirmed_user(data)
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()
    save_main_user({**data, "status": "needs_followup"})
    save_unconfirmed_user(data)
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()
    save_main_user({**data, "status": "needs_followup"})
    save_unconfirmed_user(data)
    await message.answer(
        "И последний, самый важный вопрос:\nКакую главную задачу вы хотите решить в ближайшие 3 месяца?",
        reply_markup=task_keyboard
    )
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def process_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    data = await state.get_data()
    save_main_user({**data, "status": "needs_followup"})
    save_unconfirmed_user(data)
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    save_main_user({**data, "status": "needs_followup"})
    save_unconfirmed_user(data)
    await message.answer(
        "Спасибо. Подтвердите, пожалуйста, введенные данные.",
        reply_markup=confirm_keyboard
    )
    await state.clear()


# ================== CALLBACK ==================
@dp.callback_query(lambda c: c.data == "record")
async def confirm_record(callback: types.CallbackQuery):
    telegram_id = callback.from_user.id
    records = main_sheet.get_all_records()
    ids = [str(r.get("telegram_id", "")) for r in records]

    if str(telegram_id) in ids:
        idx = ids.index(str(telegram_id)) + 2
        row = main_sheet.row_values(idx)

        # Обновляем только нужные ячейки: H (status) и L (confirmed_at)
        confirmed_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        main_sheet.update_acell(f"H{idx}", "confirmed")  # status
        main_sheet.update_acell(f"L{idx}", confirmed_time)  # confirmed_at

        # Получаем актуальные данные для сообщения админу
        updated_row = main_sheet.row_values(idx)
        text_admin = (
            f"❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
            f"«Бизнес как продолжение любви»\n\n"
            f"👤 Имя: {updated_row[2]}\n"
            f"🎯 Роль: {updated_row[3]}\n"
            f"💼 Бизнес: {updated_row[4]}\n"
            f"👥 Партнёр: {updated_row[5]}\n"
            f"⏰ Время: {updated_row[6]}\n"
            f"Telegram: @{updated_row[1] if updated_row[1] else 'не указан'}"
        )

        # Удаляем из временной таблицы
        delete_unconfirmed_user(telegram_id)

        # Сначала отвечаем на callback
        await callback.answer()

        # Отправляем сообщение пользователю
        await callback.message.answer(
            "Спасибо! Ваши данные подтверждены. Мы свяжемся с вами для согласования времени диагностической сессии.",
            reply_markup=ReplyKeyboardRemove()
        )

        # Отправляем уведомление админу
        if ADMIN_TELEGRAM_ID:
            try:
                await bot.send_message(ADMIN_TELEGRAM_ID, text_admin)
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения админу: {e}")


# ================== FALLBACK ==================
@dp.message()
async def fallback(message: types.Message):
    await message.answer("Используйте /start")


# ================== WEBHOOK ==================
async def webhook_handler(request):
    try:
        update = Update.model_validate(await request.json())
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    except Exception as e:
        logging.error(f"Ошибка обработки webhook: {e}")
        return web.Response(text="error", status=500)


async def healthcheck(request):
    return web.Response(text="Bot is alive")


# ================== APP ==================
app = web.Application()
app.router.add_get("/", healthcheck)
app.router.add_post(WEBHOOK_PATH, webhook_handler)

if __name__ == "__main__":
    logging.info(f"Bot started on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)