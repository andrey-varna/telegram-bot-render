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

# ================== ЛОГИ ==================
logging.basicConfig(level=logging.INFO)

# ================== GOOGLE SHEETS SETUP ==================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.authorize(credentials)

unconfirmed_sheet = gc.open_by_key(UNCONFIRMED_SHEET_KEY).sheet1
main_sheet = gc.open_by_key(MAIN_SHEET_KEY).sheet1

# ================== BOT & FSM ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


class BookingForm(StatesGroup):
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    main_task = State()
    time_of_day = State()


# ================== КЛАВИАТУРЫ ==================
def get_reply_kb(options):
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=opt)] for opt in options],
        resize_keyboard=True
    )


role_keyboard = get_reply_kb(["Собственник бизнеса", "CEO / управляющий", "Предприниматель", "Я эксперт/фрилансер"])
business_keyboard = get_reply_kb(["Только запускаю", "Действующий бизнес", "Масштабирую"])
partner_keyboard = get_reply_kb(["Да", "Нет, но хочу", "Нет"])
task_keyboard = get_reply_kb([
    "Перестать всё контролировать и тащить на себе",
    "Вернуть страсть и близость, не теряя доход",
    "Распределить роли, чтобы не конфликтовать",
    "Выйти на новый уровень дохода без выгорания"
])
time_keyboard = get_reply_kb(["Утро", "День", "Вечер"])
confirm_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="confirm_final")]])


# ================== ЛОГИКА ТАБЛИЦ ==================

def sync_unconfirmed(data: dict, status: str = "started"):
    try:
        records = unconfirmed_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]
        tid = str(data.get("telegram_id"))

        row = [
            tid, data.get("username", ""), data.get("name", ""),
            data.get("role", ""), data.get("business_stage", ""),
            data.get("partner", ""), data.get("time_of_day", ""),
            status, data.get("source", ""), data.get("campaign", ""),
            data.get("started_at", ""), ""
        ]

        if tid in ids:
            idx = ids.index(tid) + 2
            unconfirmed_sheet.update(range_name=f"A{idx}:L{idx}", values=[row])
        else:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"❌ Error sync_unconfirmed: {e}")


def finalize_to_main(data: dict):
    try:
        row_main = [
            str(data.get("telegram_id")), data.get("username", ""),
            data.get("source", ""), data.get("campaign", ""),
            data.get("name", ""), data.get("role", ""),
            data.get("business_stage", ""), data.get("partner", ""),
            data.get("main_task", ""), data.get("time_of_day", ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
        main_sheet.append_row(row_main)

        records = unconfirmed_sheet.get_all_records()
        ids = [str(r.get("telegram_id", "")) for r in records]
        tid = str(data.get("telegram_id"))
        if tid in ids:
            idx = ids.index(tid) + 2
            unconfirmed_sheet.delete_rows(idx)
        return True
    except Exception as e:
        logging.error(f"❌ Error finalize_to_main: {e}")
        return False


# ================== HANDLERS ==================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split(" ", 1)
    source, campaign = "organic", ""
    if len(args) > 1:
        parts = args[1].split("_", 1)
        source = parts[0]
        campaign = parts[1] if len(parts) > 1 else ""

    await state.update_data(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        source=source,
        campaign=campaign,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    # ВАШ ТЕКСТ ПРИВЕТСТВИЯ
    welcome_text = (
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

    await message.answer(welcome_text)
    await state.set_state(BookingForm.name)


@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer(
        "И последний, самый важный вопрос:\nКакую главную задачу вы хотите решить в ближайшие 3 месяца?",
        reply_markup=task_keyboard)
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    sync_unconfirmed(await state.get_data())
    await message.answer("Удобная половина дня:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, status="completed")
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


@dp.callback_query(lambda c: c.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if finalize_to_main(data):
        await callback.message.edit_text(
            "Спасибо! Ваши данные подтверждены. Мы свяжемся с вами для согласования времени диагностической сессии.")
        if ADMIN_TELEGRAM_ID:
            admin_text = (
                f"❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ\n"
                f"👤 Имя: {data.get('name')}\n"
                f"🎯 Роль: {data.get('role')}\n"
                f"💼 Бизнес: {data.get('business_stage')}\n"
                f"👥 Партнёр: {data.get('partner')}\n"
                f"💡 Задача: {data.get('main_task')}\n"
                f"⏰ Время: {data.get('time_of_day')}\n"
                f"TG: @{data.get('username')}"
            )
            await bot.send_message(ADMIN_TELEGRAM_ID, admin_text)
        await state.clear()
    else:
        await callback.answer("Ошибка сохранения. Попробуйте снова.", show_alert=True)


# ================== SERVER HANDLERS ==================

async def handle_webhook(request):
    url = str(request.url)
    if BOT_TOKEN in url:
        data = await request.json()
        update = Update.model_validate(data)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    return web.Response(text="forbidden", status=403)


async def handle_health(request):
    return web.Response(text="Bot is running", status=200)


# ================== LIFECYCLE ==================

async def on_startup(app):
    webhook_path = f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"
    await bot.set_webhook(webhook_path)
    logging.info(f"🚀 Webhook set to: {webhook_path}")


async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()
    logging.info("💤 Bot shut down")


# ================== APP SETUP ==================

app = web.Application()
app.router.add_get("/", handle_health)
app.router.add_post(f"/webhook/{BOT_TOKEN}", handle_webhook)

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)