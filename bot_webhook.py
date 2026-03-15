import os
import json
import logging
import traceback
import asyncio
from datetime import datetime, timedelta
import pytz

# FastAPI и серверные компоненты
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Aiogram
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, Update
from aiogram.fsm.storage.memory import MemoryStorage

# Внешние сервисы
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import gspread
from google.oauth2.service_account import Credentials
from notion_client import Client

# Логика ИИ
from src.brain import AssistantBrain

# ================== КОНФИГУРАЦИЯ ==================
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_KEY = os.getenv("MAIN_SHEET_KEY")
ADMIN_ZHENA_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
ADMIN_MUZH_ID = int(os.getenv("ADMIN_MUZH_ID", "0"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = "308a163edd1580caa995ecbefbfe7ee4"

FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSfWRmYZFeaCvF7uHGTmdehQFV5x2ZLK1GW0Twgi-XbWG0m0aw/viewform"
ENTRY_SOURCE_ID = "2108732275"

# ================== ИНИЦИАЛИЗАЦИЯ ==================
app = FastAPI()

# Настройка CORS, чтобы ваш сайт мог делать запросы к API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

brain = AssistantBrain()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Sofia")

# API Клиенты
try:
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
    main_sheet = spreadsheet.worksheet("leads_main")
    unconfirmed_sheet = spreadsheet.worksheet("leads_unconfirmed")
    notion = Client(auth=NOTION_TOKEN)
except Exception as e:
    logging.error(f"Критическая ошибка инициализации API: {e}")


class BookingForm(StatesGroup):
    target = State()
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    main_task = State()
    time_of_day = State()
    email = State()


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

def get_reply_kb(options):
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=opt)] for opt in options],
        resize_keyboard=True, one_time_keyboard=True
    )


confirm_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="confirm_final")]])


async def send_and_update_status(tid, msg, row_idx, col_idx, status_code):
    try:
        await bot.send_message(tid, msg)
        unconfirmed_sheet.update_cell(row_idx, col_idx, status_code)
    except Exception as e:
        logging.error(f"Ошибка отправки дожатия {status_code} для {tid}: {e}")


def sync_unconfirmed(data: dict, status: str):
    try:
        tid = str(data.get("telegram_id"))
        target = data.get("target", "w")
        created_at = data.get("created_at") or datetime.now(pytz.timezone('Europe/Sofia')).strftime("%d.%m.%Y %H:%M:%S")

        row = [
            tid, data.get("username", ""), target, data.get("source", ""), data.get("campaign", ""),
            data.get("name", ""), data.get("role", ""), data.get("business_stage", ""), data.get("partner", ""),
            ("" if target == "cd" else data.get("main_task", "")),
            (data.get("main_task", "") if target == "cd" else ""),
            data.get("time_of_day", ""), data.get("email", ""),
            created_at, status
        ]

        cells = unconfirmed_sheet.findall(tid, in_column=1)
        if cells:
            unconfirmed_sheet.update(f"A{cells[-1].row}:O{cells[-1].row}", [row])
        else:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"Sheet error: {e}")


def finalize_to_main(data: dict):
    try:
        target = data.get("target", "w")
        tid = str(data.get("telegram_id", ""))
        current_time = datetime.now(pytz.timezone('Europe/Sofia')).strftime("%d.%m.%Y %H:%M:%S")

        row_main = [
            tid, data.get("username", ""), target, data.get("source", ""), data.get("campaign", ""),
            data.get("name", ""), data.get("role", ""), data.get("business_stage", ""), data.get("partner", ""),
            ("" if target == "cd" else data.get("main_task", "")),
            (data.get("main_task", "") if target == "cd" else ""),
            data.get("time_of_day", ""), data.get("email", ""), current_time
        ]

        main_sheet.append_row(row_main, value_input_option="USER_ENTERED")
        cell = unconfirmed_sheet.find(tid, in_column=1)
        if cell:
            unconfirmed_sheet.delete_rows(cell.row)
        return True
    except Exception:
        traceback.print_exc()
        return False


def send_to_notion(data: dict):
    try:
        username = data.get("username", "скрыт")
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {"title": [{"text": {"content": data.get("name", "N/A")}}]},
                "Telegram": {"rich_text": [{"text": {"content": f"@{username}"}}]},
                "Role": {"rich_text": [{"text": {"content": data.get("role", "N/A")}}]},
                "Status": {"status": {"name": "New Lead"}}
            }
        )
        return True
    except Exception as e:
        logging.error(f"Notion Error: {e}")
        return False


# ================== ДОЖАТИЕ (SCHEDULER) ==================

async def check_abandoned_carts():
    try:
        records = unconfirmed_sheet.get_all_records()
        if not records: return
        tz = pytz.timezone('Europe/Sofia')
        now = datetime.now(tz).replace(tzinfo=None)

        for i, row in enumerate(records):
            tid = row.get('telegram_id')
            created_val = row.get('created_at')
            current_status = str(row.get('status', ''))
            target = str(row.get('target', '')).lower()
            if not tid or not created_val: continue

            try:
                start_dt = datetime.strptime(str(created_val), "%d.%m.%Y %H:%M:%S")
            except:
                continue

            diff = now - start_dt
            row_idx = i + 2
            is_cd = "cd" in target

            if timedelta(minutes=15) <= diff < timedelta(hours=1) and "notified_n1" not in current_status:
                msg = "🎁 Почти готово! Завершите опрос и заберите подарок."
                await send_and_update_status(tid, msg, row_idx, 15, "notified_n1")
            elif timedelta(days=3) <= diff < timedelta(days=4) and "notified_n2" not in current_status:
                msg = "Мы всё еще сохраняем за вами возможность попасть на диагностику. Актуально?"
                await send_and_update_status(tid, msg, row_idx, 15, "notified_n2")
    except Exception as e:
        logging.error(f"Scheduler error: {e}")


scheduler.add_job(check_abandoned_carts, "interval", minutes=15)


# ================== API ЭНДПОИНТЫ ==================

@app.post("/ask")
async def ask_website(request: Request):
    data = await request.json()
    question = data.get("question", "")
    answer = await brain.get_answer(question)
    return {"answer": answer}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.get("/")
async def health_check():
    return {"status": "running", "time": datetime.now().isoformat()}


# ================== ХЕНДЛЕРЫ ТЕЛЕГРАМ ==================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    param = args[1] if len(args) > 1 else "w_organic_none"

    parts = param.split("_")
    target = parts[0] if parts[0] in ["w", "m", "cd", "cw", "cm"] else "w"
    tz = pytz.timezone('Europe/Sofia')
    now_str = datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S")

    data = {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username or "none",
        "target": target,
        "source": parts[1] if len(parts) > 1 else "organic",
        "campaign": parts[2] if len(parts) > 2 else "none",
        "name": "", "email": "", "created_at": now_str
    }
    await state.update_data(**data)
    sync_unconfirmed(data, now_str)

    msg = "Здравствуйте! Как к вам можно обращаться?"
    await message.answer(msg, reply_markup=ReplyKeyboardRemove())
    await state.set_state(BookingForm.name)


@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Ваша роль в бизнесе:",
                         reply_markup=get_reply_kb(["Собственник", "CEO", "Фрилансер", "Эксперт"]))
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    await message.answer("Стадия бизнеса:", reply_markup=get_reply_kb(["Запуск", "Действующий", "Масштабирование"]))
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    await message.answer("Есть партнер в бизнесе?", reply_markup=get_reply_kb(["Да", "Нет", "Хочу"]))
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()
    if data['target'] == 'cd':
        await message.answer("Ваша главная задача сейчас?")
    else:
        await message.answer("Выберите задачу:", reply_markup=get_reply_kb(
            ["Выйти на новый уровень", "Вернуть страсть", "Распределить роли"]))
    await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    data = await state.get_data()
    if data['target'] == 'cd':
        await message.answer("Укажите ваш Email:")
        await state.set_state(BookingForm.email)
    else:
        await message.answer("Удобное время для звонка?", reply_markup=get_reply_kb(["Утро", "День", "Вечер"]))
        await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    await message.answer(f"📋 Подтверждаете?\n👤 {data['name']}\n🎯 {data['main_task']}", reply_markup=confirm_keyboard)


@dp.message(BookingForm.email)
async def proc_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text)
    data = await state.get_data()
    await message.answer(f"📋 Подтверждаете Email: {data['email']}?", reply_markup=confirm_keyboard)


@dp.callback_query(F.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    finalize_to_main(data)
    send_to_notion(data)

    if data.get("target") == "cd":
        label = f"{data['target']}_{data.get('source')}"
        link = f"{FORM_URL}?usp=pp_url&entry.{ENTRY_SOURCE_ID}={label}"
        await callback.message.edit_text(f"✅ Спасибо! Ваша ссылка: {link}")
    else:
        await callback.message.edit_text("✅ Данные приняты! Мы свяжемся с вами.")

    # Уведомление админа
    admin_id = ADMIN_MUZH_ID if data.get("target") in ["m", "cm"] else ADMIN_ZHENA_ID
    await bot.send_message(admin_id, f"Новый лид: {data['name']} (@{data.get('username')})")
    await state.clear()


# --- ФИНАЛЬНЫЙ ХЕНДЛЕР ДЛЯ ИИ ---
@dp.message()
async def ai_handler(message: types.Message, state: FSMContext):
    if await state.get_state() is not None: return
    if not message.text: return

    await bot.send_chat_action(message.chat.id, "typing")
    answer = await brain.get_answer(message.text)
    await message.answer(answer)


# ================== ЗАПУСК ==================

@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    scheduler.start()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)