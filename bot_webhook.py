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
MAIN_SHEET_KEY = os.getenv("MAIN_SHEET_KEY")
UNCONFIRMED_SHEET_KEY = os.getenv("UNCONFIRMED_SHEET_KEY")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

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
gc.login()

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


# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С ТАБЛИЦАМИ ==================

def create_main_lead(data: dict):
    """
    Создаёт запись в leads_main ТОЛЬКО при первом ответе (имя).
    Столбцы: telegram_id, username, name, role, business_stage, partner,
             time_of_day, status, source, campaign, started_at, confirmed_at
    """
    try:
        records = main_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]

        if str(data["telegram_id"]) in ids:
            logging.info(f"Лид {data['telegram_id']} уже существует в main, пропускаем создание")
            return

        row = [
            data.get("telegram_id"),
            data.get("username", ""),
            data.get("name", ""),
            "",  # role - пока пусто
            "",  # business_stage - пока пусто
            "",  # partner - пока пусто
            "",  # time_of_day - пока пусто
            "started",  # status
            data.get("source", ""),
            data.get("campaign", ""),
            data.get("started_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ""  # confirmed_at - пока пусто
        ]

        main_sheet.append_row(row)
        logging.info(f"✅ Создана запись в leads_main для {data['telegram_id']}")
    except Exception as e:
        logging.error(f"❌ Ошибка создания в leads_main: {e}", exc_info=True)


def update_main_lead_confirmed(telegram_id: int):
    """
    Обновляет leads_main ТОЛЬКО при подтверждении.
    Меняет status на 'confirmed' и проставляет confirmed_at.
    """
    try:
        records = main_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]

        if str(telegram_id) not in ids:
            logging.error(f"❌ Лид {telegram_id} не найден в leads_main")
            return

        idx = ids.index(str(telegram_id)) + 2
        confirmed_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Обновляем только status (H) и confirmed_at (L)
        main_sheet.update_acell(f"H{idx}", "confirmed")
        main_sheet.update_acell(f"L{idx}", confirmed_time)

        logging.info(f"✅ Обновлён статус в leads_main для {telegram_id} → confirmed")
    except Exception as e:
        logging.error(f"❌ Ошибка обновления leads_main: {e}", exc_info=True)


def create_unconfirmed_lead(data: dict):
    """
    Создаёт запись в leads_unconfirmed при первом ответе (имя).
    Столбцы: telegram_id, username, source, campaign, name, role,
             business_stage, partner, main_task, time_of_day, last_activity_at
    """
    try:
        records = unconfirmed_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]

        if str(data["telegram_id"]) in ids:
            logging.info(f"Лид {data['telegram_id']} уже в unconfirmed, пропускаем создание")
            return

        row = [
            data.get("telegram_id"),
            data.get("username", ""),
            data.get("source", ""),
            data.get("campaign", ""),
            data.get("name", ""),
            "",  # role
            "",  # business_stage
            "",  # partner
            "",  # main_task
            "",  # time_of_day
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # last_activity_at
        ]

        unconfirmed_sheet.append_row(row)
        logging.info(f"✅ Создана запись в leads_unconfirmed для {data['telegram_id']}")
    except Exception as e:
        logging.error(f"❌ Ошибка создания в leads_unconfirmed: {e}", exc_info=True)


def update_unconfirmed_lead(data: dict):
    """
    Обновляет leads_unconfirmed на КАЖДОМ шаге опроса.
    Обновляет все поля + last_activity_at.
    """
    try:
        records = unconfirmed_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]

        if str(data["telegram_id"]) not in ids:
            logging.error(f"❌ Лид {data['telegram_id']} не найден в unconfirmed")
            return

        idx = ids.index(str(data["telegram_id"])) + 2

        row = [
            data.get("telegram_id"),
            data.get("username", ""),
            data.get("source", ""),
            data.get("campaign", ""),
            data.get("name", ""),
            data.get("role", ""),
            data.get("business_stage", ""),
            data.get("partner", ""),
            data.get("main_task", ""),
            data.get("time_of_day", ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]

        unconfirmed_sheet.update(f"A{idx}:K{idx}", [row])
        logging.info(f"✅ Обновлена запись в leads_unconfirmed для {data['telegram_id']}")
    except Exception as e:
        logging.error(f"❌ Ошибка обновления leads_unconfirmed: {e}", exc_info=True)


def delete_unconfirmed_lead(telegram_id: int):
    """Удаляет запись из leads_unconfirmed при подтверждении."""
    try:
        records = unconfirmed_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]

        if str(telegram_id) not in ids:
            logging.warning(f"⚠️ Лид {telegram_id} не найден в unconfirmed для удаления")
            return

        idx = ids.index(str(telegram_id)) + 2
        unconfirmed_sheet.delete_rows(idx)
        logging.info(f"✅ Удалена запись из leads_unconfirmed для {telegram_id}")
    except Exception as e:
        logging.error(f"❌ Ошибка удаления из leads_unconfirmed: {e}", exc_info=True)


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

    # ✅ ТОЛЬКО ЗДЕСЬ создаём записи в обеих таблицах
    create_main_lead(data)
    create_unconfirmed_lead(data)

    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def process_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()

    # ✅ Обновляем ТОЛЬКО unconfirmed
    update_unconfirmed_lead(data)

    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def process_business(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()

    # ✅ Обновляем ТОЛЬКО unconfirmed
    update_unconfirmed_lead(data)

    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def process_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()

    # ✅ Обновляем ТОЛЬКО unconfirmed
    update_unconfirmed_lead(data)

    await message.answer(
        "И последний, самый важный вопрос:\nКакую главную задачу вы хотите решить в ближайшие 3 месяца?",
        reply_markup=task_keyboard
    )
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def process_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    data = await state.get_data()

    # ✅ Обновляем ТОЛЬКО unconfirmed
    update_unconfirmed_lead(data)

    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()

    # ✅ Обновляем ТОЛЬКО unconfirmed
    update_unconfirmed_lead(data)

    # Формируем сводку для пользователя
    summary = (
        f"📋 Проверьте ваши данные:\n\n"
        f"👤 Имя: {data.get('name')}\n"
        f"🎯 Роль: {data.get('role')}\n"
        f"💼 Бизнес: {data.get('business_stage')}\n"
        f"👥 Партнёр: {data.get('partner')}\n"
        f"💡 Главная задача: {data.get('main_task')}\n"
        f"⏰ Удобное время: {data.get('time_of_day')}\n\n"
        f"Всё верно?"
    )

    await message.answer(summary, reply_markup=confirm_keyboard)


# ================== CALLBACK ==================
@dp.callback_query(lambda c: c.data == "record")
async def confirm_record(callback: types.CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    data = await state.get_data()

    if not data:
        await callback.answer("Ошибка: данные не найдены. Пожалуйста, начните заново с /start", show_alert=True)
        return

    # ✅ Обновляем leads_main (status → confirmed, confirmed_at)
    update_main_lead_confirmed(telegram_id)

    # ✅ Удаляем из leads_unconfirmed
    delete_unconfirmed_lead(telegram_id)

    # Формируем сообщение админу
    text_admin = (
        f"❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
        f"«Бизнес как продолжение любви»\n\n"
        f"👤 Имя: {data.get('name', 'не указано')}\n"
        f"🎯 Роль: {data.get('role', 'не указано')}\n"
        f"💼 Бизнес: {data.get('business_stage', 'не указано')}\n"
        f"👥 Партнёр: {data.get('partner', 'не указано')}\n"
        f"💡 Главная задача: {data.get('main_task', 'не указано')}\n"
        f"⏰ Время: {data.get('time_of_day', 'не указано')}\n"
        f"Telegram: @{data.get('username', 'не указан')}"
    )

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
            logging.info(f"✅ Отправлено сообщение админу для {telegram_id}")
        except Exception as e:
            logging.error(f"❌ Ошибка отправки сообщения админу: {e}")

    # Очищаем state
    await state.clear()


# ================== FALLBACK ==================
@dp.message()
async def fallback(message: types.Message):
    await message.answer("Используйте /start")


# ================== WEBHOOK ==================
async def webhook_handler(request):
    try:
        json_data = await request.json()
        update = Update.model_validate(json_data)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    except Exception as e:
        logging.error(f"❌ Ошибка обработки webhook: {e}", exc_info=True)
        return web.Response(text="error", status=500)


async def healthcheck(request):
    return web.Response(text="Bot is alive")


# ================== APP ==================
app = web.Application()
app.router.add_get("/", healthcheck)
app.router.add_post(WEBHOOK_PATH, webhook_handler)


async def on_startup():
    """Устанавливает webhook при запуске приложения."""
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
        await bot.set_webhook(webhook_url)
        logging.info(f"✅ Webhook установлен: {webhook_url}")
    else:
        logging.warning("⚠️ WEBHOOK_URL не задан - webhook не установлен!")


if __name__ == "__main__":
    # Устанавливаем webhook перед запуском
    loop = asyncio.get_event_loop()
    loop.run_until_complete(on_startup())

    logging.info(f"🚀 Bot started on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)