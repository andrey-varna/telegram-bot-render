import os
import json
import logging
from datetime import datetime, timedelta

from aiohttp import web
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================== КОНФИГУРАЦИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_KEY = os.getenv("MAIN_SHEET_KEY")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

logging.basicConfig(level=logging.INFO)

# ================== GOOGLE SHEETS ==================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.authorize(credentials)

spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
main_sheet = spreadsheet.worksheet("leads_main")
unconfirmed_sheet = spreadsheet.worksheet("leads_unconfirmed")

# ================== BOT & FSM ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


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
        resize_keyboard=True, one_time_keyboard=True
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

def sync_unconfirmed(data: dict, status: str):
    """Пошаговое сохранение во временную таблицу для дожима."""
    try:
        tid = str(data.get("telegram_id"))
        row = [
            tid, data.get("username", ""), data.get("name", ""),
            data.get("role", ""), data.get("business_stage", ""),
            data.get("partner", ""), data.get("time_of_day", ""),
            status, data.get("source", ""), data.get("campaign", ""),
            data.get("ad_label", ""), data.get("started_at", "")
        ]
        try:
            cell = unconfirmed_sheet.find(tid, in_column=1)
            if cell:
                unconfirmed_sheet.update(f"A{cell.row}:L{cell.row}", [row])
            else:
                unconfirmed_sheet.append_row(row)
        except:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"Error sync_unconfirmed: {e}")


def finalize_to_main(data: dict):
    """Перенос в основную таблицу с проверкой на дубликаты."""
    try:
        tid = str(data.get("telegram_id"))
        try:
            if main_sheet.find(tid, in_column=1):
                return True  # Уже есть
        except:
            pass

        row_main = [
            tid, data.get("username", ""),
            data.get("source", ""), data.get("campaign", ""),
            data.get("ad_label", ""), data.get("name", ""),
            data.get("role", ""), data.get("business_stage", ""),
            data.get("partner", ""), data.get("main_task", ""),
            data.get("time_of_day", ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
        main_sheet.append_row(row_main)

        try:
            cell = unconfirmed_sheet.find(tid, in_column=1)
            if cell: unconfirmed_sheet.delete_rows(cell.row)
        except:
            pass
        return True
    except Exception as e:
        logging.error(f"Error finalize_to_main: {e}")
        return False


# ================== ДОЖИМ (SCHEDULER) ==================

async def check_abandoned():
    """Проверка застрявших анкет каждые 15 мин."""
    try:
        records = unconfirmed_sheet.get_all_records()
        now = datetime.now()
        for i, row in enumerate(records):
            if not row.get('started_at') or 'msg_sent' in str(row.get('status')): continue
            start_dt = datetime.strptime(row['started_at'], "%Y-%m-%d %H:%M:%S")
            diff = now - start_dt

            # Если прошло больше 30 мин, но меньше суток - мягко напоминаем
            if timedelta(minutes=30) <= diff <= timedelta(hours=24):
                tid = row.get('telegram_id')
                name = row.get('name') or "Дорогая коллега"
                text = f"{name}, вы остановились на заполнении анкеты. Чтобы мы могли подготовить для вас диагностику, завершите опрос: /start"
                try:
                    await bot.send_message(tid, text)
                    unconfirmed_sheet.update_cell(i + 2, 8, str(row.get('status')) + "_msg_sent")
                except:
                    pass
    except Exception as e:
        logging.error(f"Scheduler error: {e}")


# ================== HANDLERS ==================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    source, campaign, ad_label = "organic", "none", "none"
    if len(args) > 1:
        parts = args[1].split("_")
        if len(parts) >= 1: source = parts[0]
        if len(parts) >= 2: campaign = parts[1]
        if len(parts) >= 3: ad_label = "_".join(parts[2:])

    start_data = {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username or "none",
        "source": source, "campaign": campaign, "ad_label": ad_label,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": "", "role": "", "business_stage": "", "partner": "", "time_of_day": "", "main_task": ""
    }
    await state.update_data(**start_data)
    sync_unconfirmed(start_data, "started")

    welcome_text = (
        "Здравствуйте.\n\n"
        "Рада, что вы здесь. Программа 'Бизнес как продолжение любви' — это про то, как быть сильной, не ослабляя партнёра. "
        "И как создать дело, которое укрепляет отношения, а не разрушает их.\n\n"
        "Диагностика — это первый шаг к тому, чтобы увидеть свою жизнь как систему. "
        "За 40-60 минут мы найдём ключевые точки, где сейчас утекает ваша энергия и сила.\n\n"
        "Ответьте, пожалуйста, на несколько вопросов — это займёт 2-3 минуты.\n\n"
        "Как к вам можно обращаться?"
    )
    await message.answer(welcome_text, reply_markup=ReplyKeyboardRemove())
    await state.set_state(BookingForm.name)


@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    sync_unconfirmed(await state.get_data(), "step_name")
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    sync_unconfirmed(await state.get_data(), "step_role")
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    sync_unconfirmed(await state.get_data(), "step_stage")
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    sync_unconfirmed(await state.get_data(), "step_partner")
    await message.answer("Какую задачу хотите решить за 3 месяца?", reply_markup=task_keyboard)
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    sync_unconfirmed(await state.get_data(), "step_task")
    await message.answer("Удобное время для связи:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "awaiting_confirm")

    summary = (f"📋 **Ваши данные:**\n\n👤 Имя: {data.get('name')}\n🎯 Роль: {data.get('role')}\n"
               f"💼 Бизнес: {data.get('business_stage')}\n👥 Партнёр: {data.get('partner')}\n"
               f"💡 Задача: {data.get('main_task')}\n⏰ Время: {data.get('time_of_day')}")

    await message.answer(summary, reply_markup=ReplyKeyboardRemove())
    await message.answer("Подтвердите данные для записи:", reply_markup=confirm_keyboard)


@dp.callback_query(F.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or not data.get("telegram_id"): return

    # 1. Уведомление админу
    if ADMIN_TELEGRAM_ID:
        try:
            admin_msg = (f"❤️ **НОВАЯ ЗАЯВКА**\n\n👤 {data.get('name')}\n🎯 {data.get('role')}\n"
                         f"💡 {data.get('main_task')}\n📈 {data.get('source')}_{data.get('ad_label')}\n"
                         f"📱 @{callback.from_user.username or 'id' + str(callback.from_user.id)}")
            await bot.send_message(ADMIN_TELEGRAM_ID, admin_msg)
        except:
            pass

    # 2. Запись в таблицу
    finalize_to_main(data)

    # 3. Разделение финала
    source = data.get("source", "organic")
    if source == "direct":
        thanks_text = ("✅ **Спасибо. Заявка принята!**\n\nСвяжусь с вами в течение 24 часов. "
                       "А пока ждете, изучите программу:\n"
                       "🔗 [Посмотреть программу](https://prounityconsult.eu/businessaslove?utm_source=bot&utm_medium=confirmed)")
    else:
        thanks_text = ("✅ **Спасибо. Заявка принята!**\n\nСвяжусь с вами в течение 24 часов. До встречи!")

    await callback.message.edit_text(thanks_text, parse_mode="Markdown")
    await state.clear()


# ================== SERVER ==================

async def handle_webhook(request):
    body = await request.json()
    await dp.feed_update(bot, Update.model_validate(body))
    return web.Response(text="ok")


async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    scheduler.add_job(check_abandoned, "interval", minutes=15)
    scheduler.start()


app = web.Application()
app.router.add_post("/webhook", handle_webhook)
app.router.add_get("/", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)