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
ADMIN_ZHENA_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
ADMIN_MUZH_ID = int(os.getenv("ADMIN_MUZH_ID", "0"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "10000"))

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
    target = State()
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    main_task = State()
    time_of_day = State()
    email = State()


# ================== КЛАВИАТУРЫ ==================
def get_reply_kb(options):
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=opt)] for opt in options],
        resize_keyboard=True, one_time_keyboard=True
    )


cd_barrier_kb = get_reply_kb(
    ["Всё тащу на себе (операционка)", "Конфликты или холод в отношениях", "Команда не тянет / нет системы",
     "Потерял(а) смысл и драйв"])
cd_link_kb = get_reply_kb(
    ["Да, это сильно связано", "Скорее нет, я их разделяю", "В семье всё хорошо, но бизнес буксует"])
cd_risk_kb = get_reply_kb(
    ["Выгорание и потеря здоровья", "Разрушение отношений / развод", "Стагнация и проигрыш конкурентам",
     "Потеря интереса к жизни и делу"])

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
            status, f"{data.get('target')}_{data.get('source')}",
            data.get("campaign", ""), data.get("email", ""),
            data.get("started_at", "")
        ]

        cells = unconfirmed_sheet.findall(tid, in_column=1)
        target_row = None
        if cells:
            last_cell = cells[-1]
            try:
                last_time_str = unconfirmed_sheet.cell(last_cell.row, 12).value
                last_time = datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S")
                if datetime.now() - last_time < timedelta(hours=3):
                    target_row = last_cell.row
            except:
                pass

        if target_row:
            unconfirmed_sheet.update(f"A{target_row}:L{target_row}", [row])
        else:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"Error sync_unconfirmed: {e}")


def finalize_to_main(data: dict):
    try:
        row_main = [
            str(data.get("telegram_id")), data.get("username", ""),
            data.get("target", ""), data.get("source", ""),
            data.get("campaign", ""), data.get("name", ""),
            data.get("role", ""), data.get("business_stage", ""),
            data.get("partner", ""), data.get("main_task", ""),
            data.get("time_of_day", ""), data.get("email", ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
        main_sheet.append_row(row_main)
        # После финализации удаляем все временные записи этого юзера
        cells = unconfirmed_sheet.findall(str(data.get("telegram_id")), in_column=1)
        for cell in reversed(cells):
            unconfirmed_sheet.delete_rows(cell.row)
        return True
    except Exception as e:
        logging.error(f"Error finalize_to_main: {e}")
        return False


# ================== ПЛАНИРОВЩИК (ДОЖАТИЕ) ==================

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
            status = str(row.get('status', ''))
            tid = row.get('telegram_id')
            is_cd = "cd" in status or "cd" in str(row.get('source', ''))
            row_idx = i + 2

            # 1. ПЕРВОЕ ДОЖАТИЕ (15 МИНУТ)
            if timedelta(minutes=15) <= diff < timedelta(hours=1) and "n1" not in status:
                msg = "🎁 Вы начали кастдев, но не получили ссылку на формулу. Осталось пару вопросов!" if is_cd else "Вы остановились в шаге от записи на программу. Нужна помощь?"
                try:
                    await bot.send_message(tid, msg)
                    unconfirmed_sheet.update_cell(row_idx, 8, status + "_n1")
                except:
                    pass

            # 2. ВТОРОЕ ДОЖАТИЕ (3 ДНЯ)
            elif timedelta(days=3) <= diff < timedelta(days=4) and "n2" not in status:
                msg = "Прошло 3 дня, а ваша 'Формула Результата' всё еще не рассчитана. Это ваш рычаг роста, не упускайте его!" if is_cd else "Мы всё еще ждем вас на программе. Обстоятельства мешают росту или остались сомнения?"
                try:
                    await bot.send_message(tid, msg)
                    unconfirmed_sheet.update_cell(row_idx, 8, status + "_n2")
                except:
                    pass

            # 3. ФИНАЛЬНОЕ (10 ДНЕЙ)
            elif timedelta(days=10) <= diff < timedelta(days=11) and "n3" not in status:
                msg = "Финальное напоминание: ссылка на расчет формулы активна еще 24 часа. Ждем вас!" if is_cd else "Это наше последнее напоминание. Мы верим в ваш результат, но выбор за вами. Удачи!"
                try:
                    await bot.send_message(tid, msg)
                    unconfirmed_sheet.update_cell(row_idx, 8, status + "_n3")
                except:
                    pass
    except Exception as e:
        logging.error(f"Scheduler error: {e}")


# ================== HANDLERS ==================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    param = args[1] if len(args) > 1 else "w_organic_none"
    parts = param.split("_")
    target = parts[0] if parts[0] in ["w", "m", "cd", "cw", "cm"] else "w"

    start_data = {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username or "none",
        "target": target, "source": parts[1] if len(parts) > 1 else "organic",
        "campaign": parts[2] if len(parts) > 2 else "none",
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    await state.update_data(**start_data)
    sync_unconfirmed(start_data, f"start_{target}")

    text = "Здравствуйте! Спасибо за участие в кастдеве. В конце — ссылка на подарок!\nКак к вам обращаться?" if target == "cd" else "Приветствуем! Программа 'Бизнес как продолжение любви' на связи.\nКак к вам обращаться?"
    await message.answer(text, reply_markup=ReplyKeyboardRemove())
    await state.set_state(BookingForm.name)


@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "name_done")
    if data['target'] == 'cd':
        await message.answer("Что забирает энергию и мешает росту?", reply_markup=cd_barrier_kb)
    else:
        await message.answer("Ваша роль в бизнесе:",
                             reply_markup=get_reply_kb(["Собственник", "CEO", "Предприниматель", "Эксперт"]))
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "role_done")
    if data['target'] == 'cd':
        await message.answer("Связаны ли бизнес и ситуация дома?", reply_markup=cd_link_kb)
    else:
        await message.answer("Стадия бизнеса:", reply_markup=get_reply_kb(["Старт", "Действующий", "Масштабирую"]))
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "stage_done")
    if data['target'] == 'cd':
        await message.answer("Какой риск самый критичный?", reply_markup=cd_risk_kb)
    else:
        await message.answer("Есть ли партнер?", reply_markup=get_reply_kb(["Да", "Нет"]))
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()
    if data['target'] == 'cd':
        await message.answer("Введите ваш Email для ссылки на формулу:")
        await state.set_state(BookingForm.email)
    else:
        await message.answer("Ваша главная задача на 3 месяца?")
        await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    await message.answer("Удобное время для связи?", reply_markup=get_reply_kb(["Утро", "День", "Вечер"]))
    await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    await message.answer(f"Проверьте данные:\nИмя: {data['name']}\nЗадача: {data['main_task']}",
                         reply_markup=confirm_keyboard)


@dp.message(BookingForm.email)
async def proc_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text)
    data = await state.get_data()
    await message.answer(f"Проверьте email: {data['email']}", reply_markup=confirm_keyboard)


@dp.callback_query(F.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if finalize_to_main(data):
        target = data.get("target", "w")
        if target == "cd":
            await callback.message.edit_text("✅ Ваша ссылка: https://forms.gle/nDYzDLtffxyAcsvS7")
            admin_id = ADMIN_MUZH_ID
        else:
            await callback.message.edit_text("✅ Данные приняты! Свяжемся с вами.")
            admin_id = ADMIN_MUZH_ID if target in ["m", "cm"] else ADMIN_ZHENA_ID

        if admin_id:
            await bot.send_message(admin_id, f"🚀 Новая заявка [{target}]: {data.get('name')} @{data.get('username')}")
        await state.clear()


# ================== SERVER ==================
async def handle_webhook(request):
    body = await request.json()
    await dp.feed_update(bot, Update.model_validate(body))
    return web.Response(text="ok")


async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    scheduler.add_job(check_abandoned_carts, "interval", minutes=15)
    scheduler.start()


app = web.Application()
app.router.add_post("/webhook", handle_webhook)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)