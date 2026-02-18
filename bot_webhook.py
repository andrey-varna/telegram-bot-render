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


role_kb = get_reply_kb(["Собственник бизнеса", "CEO / управляющий", "Предприниматель", "Я эксперт/фрилансер"])
stage_kb = get_reply_kb(["Только запускаю", "Действующий бизнес", "Масштабирую"])
partner_kb = get_reply_kb(["Да", "Нет, но хочу", "Нет"])
pain_kb = get_reply_kb([
    "Перестать всё контролировать и тащить на себе",
    "Вернуть страсть и близость, не теряя доход",
    "Распределить роли, чтобы не конфликтовать",
    "Выйти на новый уровень дохода без выгорания"
])
time_kb = get_reply_kb(["Утро", "День", "Вечер"])

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

def sync_unconfirmed(data: dict, status_or_time: str):
    try:
        tid = str(data.get("telegram_id"))
        target = data.get("target", "w")
        is_cd = (target == "cd")

        row = [
            tid, data.get("username", ""), target, data.get("source", ""), data.get("campaign", ""),
            data.get("name", ""), data.get("role", ""), data.get("business_stage", ""), data.get("partner", ""),
            ("" if is_cd else data.get("main_task", "")),  # J: program_pain
            (data.get("main_task", "") if is_cd else ""),  # K: cd_task
            data.get("time_of_day", ""), data.get("email", ""),
            status_or_time  # N: created_at (используется для планировщика)
        ]

        cells = unconfirmed_sheet.findall(tid, in_column=1)
        if cells:
            unconfirmed_sheet.update(f"A{cells[-1].row}:N{cells[-1].row}", [row])
        else:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"Error sync: {e}")


def finalize_to_main(data: dict):
    try:
        target = data.get("target", "w")
        is_cd = (target == "cd")
        tid = str(data.get("telegram_id"))

        row_main = [
            tid, data.get("username", ""), target, data.get("source", ""), data.get("campaign", ""),
            data.get("name", ""), data.get("role", ""), data.get("business_stage", ""), data.get("partner", ""),
            ("" if is_cd else data.get("main_task", "")),  # J
            (data.get("main_task", "") if is_cd else ""),  # K
            data.get("time_of_day", ""), data.get("email", ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # N
        ]

        main_sheet.append_row(row_main)

        try:
            cell = unconfirmed_sheet.find(tid, in_column=1)
            if cell: unconfirmed_sheet.delete_rows(cell.row)
        except:
            pass
        return True
    except Exception as e:
        logging.error(f"Error finalize: {e}")
        return False


# ================== ПЛАНИРОВЩИК (ДОЖАТИЕ) ==================

async def check_abandoned_carts():
    try:
        records = unconfirmed_sheet.get_all_records()
        now = datetime.now()
        for i, row in enumerate(records):
            # Бот ищет время в колонке created_at (N)
            try:
                start_dt = datetime.strptime(str(row.get('created_at')), "%Y-%m-%d %H:%M:%S")
            except:
                continue

            diff = now - start_dt
            tid = row.get('telegram_id')
            is_cd = "cd" in str(row.get('target', ''))
            row_idx = i + 2

            # 15 минут
            if timedelta(minutes=15) <= diff < timedelta(hours=1) and "n1" not in str(row.get('role_barrier')):
                msg = "🎁 Почти готово! Ответьте на пару вопросов и заберите подарок." if is_cd else "Вы начали регистрацию, но не завершили её. Есть вопросы?"
                try:
                    await bot.send_message(tid, msg)
                    unconfirmed_sheet.update_cell(row_idx, 7, "notified_n1")  # Пишем метку в G для простоты
                except:
                    pass
            # 3 дня
            elif timedelta(days=3) <= diff < timedelta(days=4) and "n2" not in str(row.get('role_barrier')):
                msg = "Ваша 'Формула Результата' всё еще ждет вас." if is_cd else "Мы сохранили ваше место на программу. Актуально?"
                try:
                    await bot.send_message(tid, msg)
                    unconfirmed_sheet.update_cell(row_idx, 7, "notified_n2")
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

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_data = {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username or "none",
        "target": target, "source": parts[1] if len(parts) > 1 else "organic",
        "campaign": parts[2] if len(parts) > 2 else "none",
        "started_at": now_str,
        "name": "", "role": "", "business_stage": "", "partner": "", "main_task": "", "time_of_day": "", "email": ""
    }
    await state.update_data(**start_data)
    sync_unconfirmed(start_data, now_str)  # Записываем время в колонку N

    if target == "cd":
        text = "Приветствую!\n\n" \
        "Благодарю за помощь в моем исследовании темы \n\n"
        "'Бизнес как продолжение любви' \n\n"
        "Ваше мнение - важная часть этого проекта."
        "В конце я пришлю обещанный расчет вашей персональной \n\n"
        "Формулы Результата\n\n"
        "Как к вам обращаться?\n\n"
    else:
        text = ("Здравствуйте.\n\n"
        "Рада, что вы здесь. Программа 'Бизнес как продолжение любви'"
        "- это про то, как быть сильной, не ослабляя партнёра. "
        "И как создать дело, которое укрепляет отношения, а не разрушает их.\n\n"
        "Диагностика - это первый шаг к тому, чтобы увидеть свою жизнь "
        "как систему. За 40-60 минут мы найдём ключевые точки, "
        "где сейчас утекает ваша энергия и сила."
        "Увидим, что даёт вам опору, а что тормозит движение.\n\n" 
        "Чтобы подготовиться и провести сессию максимально эффективно, " 
        "мне важно узнать о вас немного больше. "
        "Ответьте, пожалуйста, на несколько вопросов - это займёт 2-3 минуты.\n\n" 
        "Как к вам можно обращаться?")

    await message.answer(text, reply_markup=ReplyKeyboardRemove())
    await state.set_state(BookingForm.name)


@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "name_done")
    if data['target'] == 'cd':
        await message.answer("Что сейчас больше всего забирает энергию?", reply_markup=cd_barrier_kb)
    else:
        await message.answer("Ваша роль в бизнесе:", reply_markup=role_kb)
    await state.set_state(BookingForm.role)


@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "role_done")
    if data['target'] == 'cd':
        await message.answer("Связаны ли бизнес и ситуация дома?", reply_markup=cd_link_kb)
    else:
        await message.answer("Ваш бизнес сейчас:", reply_markup=stage_kb)
    await state.set_state(BookingForm.business_stage)


@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "stage_done")
    if data['target'] == 'cd':
        await message.answer("Какой риск самый критичный?", reply_markup=cd_risk_kb)
    else:
        await message.answer("Есть ли у вас партнер?", reply_markup=partner_kb)
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "partner_done")
    if data['target'] == 'cd':
        await message.answer("Ваша самая большая задача сейчас?")
        await state.set_state(BookingForm.main_task)
    else:
        await message.answer("Какую задачу хотите решить за 3 месяца?", reply_markup=pain_kb)
        await state.set_state(BookingForm.main_task)


@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "task_done")
    if data['target'] == 'cd':
        await message.answer("Укажите ваш Email:")
        await state.set_state(BookingForm.email)
    else:
        await message.answer("Удобное время для связи:", reply_markup=time_kb)
        await state.set_state(BookingForm.time_of_day)


@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "awaiting_confirm")
    summary = (
        f"📋 **Ваши данные:**\n\n👤 Имя: {data['name']}\n🎯 Задача: {data['main_task']}\n🕒 Время: {data['time_of_day']}")
    await message.answer(summary, reply_markup=confirm_keyboard, parse_mode="Markdown")


@dp.message(BookingForm.email)
@dp.message(BookingForm.email)
async def proc_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "awaiting_confirm_cd")

    # Измененный текст подтверждения для кастдева
    summary = (
        f"📋 **Ваши данные для кастдева:**\n\n"
        f"👤 Имя: {data['name']}\n"
        f"📧 Email: {data['email']}\n"
        f"🎯 Задача: {data['main_task']}\n\n"
        f"Пожалуйста, подтвердите ваши данные, чтобы получить ссылку на расчет 'Формулы Результата':"
    )
    await message.answer(summary, reply_markup=confirm_keyboard, parse_mode="Markdown")


@dp.callback_query(F.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target = data.get("target", "w")

    if finalize_to_main(data):
        if target == "cd":
            # Финальное сообщение пользователю после кастдева
            await callback.message.edit_text(
                "✅ Данные подтверждены! Ваша ссылка на 'Формулу Результата': https://forms.gle/nDYzDLtffxyAcsvS7")

            # ОТПРАВКА ЖЕНЕ (ADMIN_ZHENA_ID)
            admin_id = ADMIN_ZHENA_ID
            label = "📊 НОВЫЙ КАСТДЕВ (Заполнено)"
            details = f"📧 Email: {data.get('email')}\n🎯 Задача: {data.get('main_task')}"
        else:
            # Обычная программа
            await callback.message.edit_text("✅ Данные приняты! Мы свяжемся с вами в ближайшее время.")

            # Распределение: муж или жена
            admin_id = ADMIN_MUZH_ID if target in ["m", "cm"] else ADMIN_ZHENA_ID
            label = "🚀 ЗАЯВКА НА ПРОГРАММУ"
            details = f"🔥 БОЛЬ: {data.get('main_task')}\n🕒 ВРЕМЯ: {data.get('time_of_day')}"

        # Сама отправка сообщения админу
        if admin_id:
            admin_text = (
                f"{label}\n\n"
                f"👤 Имя: {data.get('name')}\n"
                f"{details}\n"
                f"📱 Контакт: @{data.get('username')}\n"
                f"🔗 Источник: {data.get('source')}_{data.get('campaign')}"
            )
            try:
                await bot.send_message(admin_id, admin_text)
            except Exception as e:
                logging.error(f"Ошибка отправки админу: {e}")

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