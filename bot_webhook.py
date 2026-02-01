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
    try:
        tid = str(data.get("telegram_id"))
        row = [
            tid, data.get("username", ""), data.get("name", ""),
            data.get("role", ""), data.get("business_stage", ""),
            data.get("partner", ""), data.get("time_of_day", ""),
            status, data.get("source", ""), data.get("campaign", ""),
            data.get("ad_label", ""), data.get("started_at", "")
        ]
        cell = unconfirmed_sheet.find(tid, in_column=1)
        if cell:
            unconfirmed_sheet.update(f"A{cell.row}:L{cell.row}", [row])
        else:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"Error sync_unconfirmed: {e}")


def finalize_to_main(data: dict):
    """Записывает данные в основную таблицу и удаляет из временной."""
    try:
        tid = str(data.get("telegram_id"))

        # Проверка на дубликат внутри функции записи
        existing = None
        try:
            existing = main_sheet.find(tid, in_column=1)
        except:
            pass

        if not existing:
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

        # Удаляем из временной в любом случае
        try:
            cell = unconfirmed_sheet.find(tid, in_column=1)
            if cell: unconfirmed_sheet.delete_rows(cell.row)
        except:
            pass
        return True
    except Exception as e:
        logging.error(f"Error finalize_to_main: {e}")
        return False


# ================== АВТО-ДОЖАТИЕ ==================

async def check_abandoned_carts():
    try:
        records = unconfirmed_sheet.get_all_records()
        now = datetime.now()
        for i, row in enumerate(records):
            if not row.get('started_at'): continue
            try:
                start_dt = datetime.strptime(row['started_at'], "%Y-%m-%d %H:%M:%S")
            except:
                continue

            diff = now - start_dt
            tid = row.get('telegram_id')
            status = str(row.get('status', ''))
            name = row.get('name') or "Дорогая коллега"

            if timedelta(minutes=10) <= diff < timedelta(hours=23) and "admin_notified" not in status:
                admin_text = (f"⚠️ **НА ДОРАБОТКУ**\n\n👤 @{row.get('username')}\n"
                              f"📝 Имя: {row.get('name') or 'не указано'}\n📍 Шаг: {status}")
                await bot.send_message(ADMIN_TELEGRAM_ID, admin_text)
                unconfirmed_sheet.update_cell(i + 2, 8, status + "_admin_notified")

            if timedelta(days=1) <= diff < timedelta(days=2) and "msg1_sent" not in status:
                text = (f"{name}, здравствуйте! Вы начали заполнять анкету на диагностику, но что-то отвлекло... "
                        "Нажмите /start, чтобы продолжить.")
                try:
                    await bot.send_message(tid, text)
                    unconfirmed_sheet.update_cell(i + 2, 8, status + "_msg1_sent")
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
        source = parts[0] if len(parts) >= 1 else "organic"
        campaign = parts[1] if len(parts) >= 2 else "none"
        ad_label = parts[2] if len(parts) >= 3 else "none"

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
        "Как к вам можно обращаться?"
    )
    await message.answer(welcome_text, reply_markup=ReplyKeyboardRemove())
    await state.set_state(BookingForm.name)


@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    sync_unconfirmed(await state.get_data(), "name_done")
    await message.answer("Ваша роль в бизнесе:", reply_markup=role_keyboard)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    sync_unconfirmed(await state.get_data(), "role_done")
    await message.answer("Ваш бизнес сейчас:", reply_markup=business_keyboard)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    sync_unconfirmed(await state.get_data(), "stage_done")
    await message.answer("Есть ли у вас партнер?", reply_markup=partner_keyboard)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    sync_unconfirmed(await state.get_data(), "partner_done")
    await message.answer("Какую задачу хотите решить за 3 месяца?", reply_markup=task_keyboard)
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    sync_unconfirmed(await state.get_data(), "task_done")
    await message.answer("Удобное время для связи:", reply_markup=time_keyboard)
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "awaiting_confirm")

    summary = (f"📋 **Ваши данные:**\n\n"
               f"👤 **Имя:** {data.get('name')}\n"
               f"🎯 **Роль:** {data.get('role')}\n"
               f"💼 **Бизнес:** {data.get('business_stage')}\n"
               f"👥 **Партнёр:** {data.get('partner')}\n"
               f"💡 **Задача:** {data.get('main_task')}\n"
               f"⏰ **Время:** {data.get('time_of_day')}")
    await message.answer(summary, reply_markup=confirm_keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()

    if not data or not data.get("telegram_id"):
        await callback.answer("Заявка уже обработана.")
        return

    # 1. СНАЧАЛА ОПОВЕЩЕНИЕ АДМИНУ
    if ADMIN_TELEGRAM_ID:
        try:
            text_admin = (
                f"❤️ **НОВАЯ ЗАЯВКА**\n\n"
                f"👤 **Имя:** {data.get('name')}\n"
                f"🎯 **Роль:** {data.get('role')}\n"
                f"💼 **Бизнес:** {data.get('business_stage')}\n"
                f"👥 **Партнёр:** {data.get('partner')}\n"
                f"💡 **Задача:** {data.get('main_task')}\n"
                f"⏰ **Время:** {data.get('time_of_day')}\n\n"
                f"📈 **Метки:** `{data.get('source')}_{data.get('campaign')}`\n"
                f"📱 **TG:** @{callback.from_user.username or 'скрыт'}"
            )
            await bot.send_message(ADMIN_TELEGRAM_ID, text_admin, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Admin notify error: {e}")

    # 2. ЗАПИСЬ В ТАБЛИЦУ (с защитой от дублей внутри функции)
    finalize_to_main(data)

    # 3. ОТВЕТ КЛИЕНТУ С ПРОГРЕВОМ
    thanks_text = (
        "✅ **Отлично! Ваша заявка принята.**\n\n"
        "📞 Я свяжусь с вами в течение 24 часов для согласования времени.\n\n"
        "📚 **Пока вы ждете, узнайте больше о программе:**\n"
        "🔗 [Читать программу и отзывы](https://prounityconsult.eu/businessaslove?utm_source=bot&utm_medium=confirmed)"
    )

    try:
        await callback.message.edit_text(thanks_text, parse_mode="Markdown", disable_web_page_preview=False)
    except:
        await callback.message.answer(thanks_text, parse_mode="Markdown")

    await state.clear()


@dp.message()
async def echo_handler(message: types.Message):
    await message.answer("Для запуска бота наберите команду /start")


# ================== SERVER ==================

async def handle_webhook(request):
    body = await request.json()
    await dp.feed_update(bot, Update.model_validate(body))
    return web.Response(text="ok")


async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    scheduler.add_job(check_abandoned_carts, "interval", minutes=15)
    scheduler.start()


async def on_shutdown(app):
    await bot.session.close()
    scheduler.shutdown()


app = web.Application()
app.router.add_post("/webhook", handle_webhook)
app.router.add_get("/", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)