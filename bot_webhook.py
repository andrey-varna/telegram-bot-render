import os
import json
import logging
from aiohttp import web
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from notion_client import Client
import traceback
from datetime import datetime, timedelta

# ================== КОНФИГУРАЦИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_KEY = os.getenv("MAIN_SHEET_KEY")
ADMIN_ZHENA_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
ADMIN_MUZH_ID = int(os.getenv("ADMIN_MUZH_ID", "0"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "10000"))

# ДАННЫЕ NOTION
NOTION_TOKEN = os.getenv("NOTION_TOKEN")  # secret_...
NOTION_DATABASE_ID = "308a163edd1580caa995ecbefbfe7ee4"  # Твоя база People

# ТВОИ ДАННЫЕ ГУГЛ ФОРМЫ
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSfWRmYZFeaCvF7uHGTmdehQFV5x2ZLK1GW0Twgi-XbWG0m0aw/viewform"
ENTRY_SOURCE_ID = "2108732275"

logging.basicConfig(level=logging.INFO)

# ================== ИНИЦИАЛИЗАЦИЯ API ==================
# Google
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
credentials = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
gc = gspread.authorize(credentials)
spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
main_sheet = spreadsheet.worksheet("leads_main")
unconfirmed_sheet = spreadsheet.worksheet("leads_unconfirmed")

# Notion
notion = Client(auth=NOTION_TOKEN)

# Бот
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="bulgaria/sofia") # Укажи свой часовой пояс

# 2. Добавляем задачу (проверка брошенных корзин каждые 10 минут)

class BookingForm(StatesGroup):
    target = State()
    name = State()
    role = State()
    business_stage = State()
    partner = State()
    main_task = State()
    time_of_day = State()
    email = State()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_reply_kb(options):
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=opt)] for opt in options],
        resize_keyboard=True, one_time_keyboard=True
    )


confirm_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📅 Подтвердить данные", callback_data="confirm_final")]])


# ================== ИНТЕГРАЦИЯ NOTION ==================
def send_to_notion(data: dict):
    try:
        username = data.get("username", "скрыт")
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {"title": [{"text": {"content": data.get("name", "N/A")}}]},
                "Telegram": {"rich_text": [{"text": {"content": f"@{username}"}}]},
                "Role": {"rich_text": [{"text": {"content": data.get("role", "N/A")}}]},
                "Task": {"rich_text": [{"text": {"content": data.get("main_task", "N/A")}}]},
                "Status": {"status": {"name": "New Lead"}}
            }
        )
        return True
    except Exception as e:
        logging.error(f"Notion Error: {e}")
        return False


# ================== ТЕХНИЧЕСКИЕ ФУНКЦИИ (ТАБЛИЦЫ) ==================

def sync_unconfirmed(data: dict, status: str):
    try:
        tid = str(data.get("telegram_id"))
        target = data.get("target", "w")
        row = [
            tid, data.get("username", ""), target, data.get("source", ""), data.get("campaign", ""),
            data.get("name", ""), data.get("role", ""), data.get("business_stage", ""), data.get("partner", ""),
            ("" if target == "cd" else data.get("main_task", "")),
            (data.get("main_task", "") if target == "cd" else ""),
            data.get("time_of_day", ""), data.get("email", ""), status
        ]
        cells = unconfirmed_sheet.findall(tid, in_column=1)
        if cells:
            unconfirmed_sheet.update(f"A{cells[-1].row}:N{cells[-1].row}", [row])
        else:
            unconfirmed_sheet.append_row(row)
    except Exception as e:
        logging.error(f"Sheet error: {e}")


def finalize_to_main(data: dict):
    # Эта строка говорит Python использовать те самые таблицы, что ты открыл в начале файла
    global main_sheet, unconfirmed_sheet, notion, NOTION_DATABASE_ID

    try:
        # 1. ЛОГИ: Поймем, что пришло с лендинга (почему нет "выбора проблем")
        print(f"DEBUG: Входящие данные: {data}")

        target = data.get("target", "w")
        tid = str(data.get("telegram_id", ""))
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Собираем строку для записи
        row_main = [
            tid, data.get("username", ""), target, data.get("source", ""), data.get("campaign", ""),
            data.get("name", ""), data.get("role", ""), data.get("business_stage", ""), data.get("partner", ""),
            ("" if target == "cd" else data.get("main_task", "")),
            (data.get("main_task", "") if target == "cd" else ""),
            data.get("time_of_day", ""), data.get("email", ""), current_time
        ]

        # 2. ЗАПИСЬ В GOOGLE SHEETS
        # append_row добавляет В КОНЕЦ. Если затирает — проверим логи!
        # main_sheet.append_row(row_main, value_input_option="USER_ENTERED")
        try:
            # Получаем все значения первого столбца, чтобы найти реальный конец
            col_values = main_sheet.col_values(1)
            next_row = len(col_values) + 1

            # Записываем данные в конкретную строку
            main_sheet.insert_row(row_main, next_row, value_input_option="USER_ENTERED")
            print(f"DEBUG: Google Sheets - OK (Записано в строку {next_row})")
        except Exception as sheet_err:
            print(f"!!! ОШИБКА GOOGLE SHEETS: {sheet_err}")
            # Если insert_row не сработал, пробуем обычный append как запасной вариант
            main_sheet.append_row(row_main, value_input_option="USER_ENTERED")
        print("DEBUG: Google Sheets (main_sheet) - OK")

        # 3. ЗАПИСЬ В NOTION
        try:
            notion_properties = {
                "Name": {"title": [{"text": {"content": data.get("name", "Без имени")}}]},
                "Telegram ID": {"rich_text": [{"text": {"content": tid}}]},
                "Email": {"email": data.get("email")} if data.get("email") else None,
                # Поля Select требуют точного совпадения или наличия опции в Notion
                "Relationship Status": {"select": {"name": "Confirmed"}},
                "Role in Business": {"select": {"name": data.get("role") if data.get("role") else "Other"}},
                "Business Stage": {
                    "select": {"name": data.get("business_stage") if data.get("business_stage") else "None"}},
                "Source": {"select": {"name": data.get("source") if data.get("source") else "direct"}},
                "Partner": {"select": {"name": data.get("partner") if data.get("partner") else "None"}}
            }

            notion_properties = {k: v for k, v in notion_properties.items() if v is not None}

            notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties=notion_properties
            )
            print("DEBUG: Notion - OK")
        except Exception as n_err:
            print(f"!!! ОШИБКА NOTION: {n_err}")

        # 4. УДАЛЕНИЕ ИЗ ВРЕМЕННОЙ ТАБЛИЦЫ
        cell = unconfirmed_sheet.find(tid, in_column=1)
        if cell:
            unconfirmed_sheet.delete_rows(cell.row)
            print(f"DEBUG: Удалена строка {cell.row} из unconfirmed_sheet")

        return True

    except Exception as e:
        print("!!! КРИТИЧЕСКАЯ ОШИБКА В FINALIZE_TO_MAIN:")
        traceback.print_exc()
        return False
# ================== ХЕНДЛЕРЫ ==================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    param = args[1] if len(args) > 1 else "w_organic_none"

    if param == "w_from_formula":
        await state.update_data(telegram_id=message.from_user.id, username=message.from_user.username or "none",
                                target="w", source="from_formula")
        await message.answer(
            "🚀 С возвращением! Вы рассчитали Формулу. Теперь пора внедрить её в жизнь.\nГотовы обсудить программу на диагностике?",
            reply_markup=get_reply_kb(["Да, готов(а)", "Узнать подробнее"]))
        await state.set_state(BookingForm.main_task)
        return

    parts = param.split("_")
    target = parts[0] if parts[0] in ["w", "m", "cd", "cw", "cm"] else "w"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    data = {"telegram_id": message.from_user.id, "username": message.from_user.username or "none",
            "target": target, "source": parts[1] if len(parts) > 1 else "organic",
            "campaign": parts[2] if len(parts) > 2 else "none", "name": "", "email": "", "created_at": now_str}
    await state.update_data(**data)
    sync_unconfirmed(data, now_str)

    if target == "cd":
        msg = ("Приветствую!\n\nБлагодарю за помощь в моем исследовании темы \n\n"
               "'Бизнес как продолжение любви'.\n\n Ваше мнение - важная часть этого проекта. \n\n"
               "В конце я пришлю обещанный расчет вашей персональной 'Формулы Результата'. \n\n"
               "Как к вам обращаться?")
    else:
        msg = ("Здравствуйте.\n\n"
        "Рада, что вы здесь. Программа 'Бизнес как продолжение любви' "
        "- это про то, как быть сильной, не ослабляя партнёра. "
        "И как создать дело, которое укрепляет отношения, а не разрушает их.\n\n"
        "Диагностика - это первый шаг к тому, чтобы увидеть свою жизнь как систему.\n\n"
        "Как к вам можно обращаться?")

    await message.answer(msg, reply_markup=ReplyKeyboardRemove())
    await state.set_state(BookingForm.name)

@dp.message(BookingForm.name)
async def proc_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "name_done")
    await message.answer("Ваша роль в бизнесе:",
                         reply_markup=get_reply_kb(["Собственник", "CEO", "Фрилансер", "Эксперт"]))
    await state.set_state(BookingForm.role)

@dp.message(BookingForm.role)
async def proc_role(message: types.Message, state: FSMContext):
    await state.update_data(role=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "role_done")
    await message.answer("Стадия бизнеса:", reply_markup=get_reply_kb(["Запуск", "Действующий", "Масштабирование"]))
    await state.set_state(BookingForm.business_stage)

@dp.message(BookingForm.business_stage)
async def proc_stage(message: types.Message, state: FSMContext):
    await state.update_data(business_stage=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "stage_done")
    await message.answer("Есть партнер в бизнесе?", reply_markup=get_reply_kb(["Да", "Нет", "Хочу"]))
    await state.set_state(BookingForm.partner)


@dp.message(BookingForm.partner)
async def proc_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner=message.text)
    data = await state.get_data()

    # Синхронизируем временные данные
    sync_unconfirmed(data, "partner_done")

    # Проверяем цель (target)
    if data.get('target') == 'cd':
        # Для КД оставляем открытый вопрос без кнопок
        await message.answer(
            "Ваша главная задача сейчас? (Напишите кратко)",
            reply_markup=types.ReplyKeyboardRemove()
        )
    else:
        # Для лендинга/программы даем кнопки с вариантами
        task_keyboard = get_reply_kb([
            "Перестать всё контролировать и тащить на себе",
            "Вернуть страсть и близость, не теряя доход",
            "Распределить роли, чтобы не конфликтовать",
            "Выйти на новый уровень дохода без выгорания"
        ])
        await message.answer("Выберите вашу главную задачу сейчас:", reply_markup=task_keyboard)

    await state.set_state(BookingForm.main_task)

@dp.message(BookingForm.main_task)
async def proc_task(message: types.Message, state: FSMContext):
    await state.update_data(main_task=message.text)
    data = await state.get_data()
    if data['target'] == 'cd':
        await message.answer("Укажите ваш Email для связи:")
        await state.set_state(BookingForm.email)
    else:
        await message.answer("Удобное время для звонка?", reply_markup=get_reply_kb(["Утро", "День", "Вечер"]))
        await state.set_state(BookingForm.time_of_day)

@dp.message(BookingForm.time_of_day)
async def proc_time(message: types.Message, state: FSMContext):
    await state.update_data(time_of_day=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "awaiting_confirm")
    await message.answer(f"📋 Данные подтверждаете?\n👤 {data['name']}\n🎯 {data['main_task']}\n🕒 {data['time_of_day']}",
                         reply_markup=confirm_keyboard)

@dp.message(BookingForm.email)
async def proc_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text)
    data = await state.get_data()
    sync_unconfirmed(data, "awaiting_confirm_cd")
    await message.answer(f"📋 Данные кастдева:\n👤 {data['name']}\n📧 {data['email']}\n\nПодтверждаете?",
                         reply_markup=confirm_keyboard)

@dp.callback_query(F.data == "confirm_final")
async def confirm_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target = data.get("target", "w")
    username = data.get("username", "none")

    # 1. Запись в Google Таблицы
    final_google = finalize_to_main(data)

    # 2. Запись в Notion
    final_notion = send_to_notion(data)

    # 3. Формирование отчета для админа (ПО ТВОЕЙ ФОРМЕ)
    if target == "cd":
        label = f"{target}_{data.get('source')}_{data.get('campaign')}"
        smart_link = f"{FORM_URL}?usp=pp_url&entry.{ENTRY_SOURCE_ID}={label}"
        await callback.message.edit_text(f"✅ Спасибо! Ваша ссылка для расчета:\n{smart_link}",
                                         disable_web_page_preview=True)
        admin_id = ADMIN_ZHENA_ID
        admin_header = "❤️ ОТЧЕТ О КАСТДЕВЕ"
    else:
        await callback.message.edit_text("✅ Данные приняты! Мы свяжемся с вами.")
        admin_id = ADMIN_MUZH_ID if target in ["m", "cm"] else ADMIN_ZHENA_ID
        admin_header = "❤️ ДИАГНОСТИЧЕСКАЯ СЕССИЯ"

    # Полная форма уведомления
    text_admin = (
        f"{admin_header}\n\n"
        f"👤 Имя: {data.get('name')}\n"
        f"🎯 Роль: {data.get('role')}\n"
        f"💼 Бизнес: {data.get('business_stage')}\n"
        f"👥 Партнёр: {data.get('partner')}\n"
        f"💡 Задача: {data.get('main_task')}\n"
        f"⏰ Время: {data.get('time_of_day', 'N/A')}\n"
        f"📧 Email: {data.get('email', 'N/A')}\n"
        f"📱 Telegram: @{username}"
    )

    if admin_id:
        try:
            await bot.send_message(admin_id, text_admin)
        except Exception as e:
            logging.error(f"Admin Notify Error: {e}")

    await state.clear()

# Дожатие 3 касания
async def check_abandoned_carts():
    try:
        records = unconfirmed_sheet.get_all_records()
        if not records:
            return

        now = datetime.now()
        header = unconfirmed_sheet.row_values(1)

        # Ищем колонку для статуса, если нет — используем 14-ю
        try:
            status_col_idx = header.index('status') + 1
        except ValueError:
            status_col_idx = 14

        for i, row in enumerate(records):
            tid = row.get('telegram_id')
            if not tid: continue

            try:
                created_val = row.get('created_at')
                if not created_val:
                    continue

                start_dt = datetime.strptime(str(created_val), "%d.%m.%Y %H:%M:%S")
            except Exception as e:
                print(f"DEBUG: Ошибка даты на строке {i + 2}: {e} (значение: {row.get('created_at')})")
                continue

            diff = now - start_dt
            target = str(row.get('target', '')).lower()
            is_cd = "cd" in target
            current_status = str(row.get('status', ''))
            row_idx = i + 2

            # --- 1 КАСАНИЕ (15 минут) ---
            if timedelta(minutes=1) <= diff < timedelta(hours=1) and "notified_n1" not in current_status:
                msg = ("🎁 Почти готово! Ответьте на пару вопросов и заберите подарок." if is_cd
                       else "Вы начали регистрацию на программу, но не завершили её. Всё ли в порядке?")
                await send_and_update_status(tid, msg, row_idx, status_col_idx, "notified_n1")

            # --- 2 КАСАНИЕ (3 дня) ---
            #elif timedelta(days=3) <= diff < timedelta(days=4) and "notified_n2" not in current_status:
            elif timedelta(minutes=2) <= diff < timedelta(hours=1) and "notified_n1" not in current_status:
                msg = ("Ваша 'Формула Результата' всё еще ждет вас. Завершите опрос, чтобы получить её." if is_cd
                       else "Мы всё еще сохраняем ваше место на программу. Регистрация актуальна для вас?")
                await send_and_update_status(tid, msg, row_idx, status_col_idx, "notified_n2")

            # --- 3 КАСАНИЕ (7 дней) — НОВОЕ ---
            #elif timedelta(days=7) <= diff < timedelta(days=8) and "notified_n3" not in current_status:
            elif timedelta(minutes=3) <= diff < timedelta(hours=1) and "notified_n1" not in current_status:
                msg = (
                    "Я всё еще на связи! Если актуально получить подарок и разобрать вашу ситуацию — анкету можно заполнить в любое время." if is_cd
                    else "Хотел напомнить, что вы можете завершить регистрацию в любое время. Если возникли вопросы или сомнения — просто напишите мне.")
                await send_and_update_status(tid, msg, row_idx, status_col_idx, "notified_n3")

    except Exception as e:
        print(f"!!! ОШИБКА SCHEDULER: {e}")

# Добавляем задачу (проверка брошенных корзин каждые 10 минут)
scheduler.add_job(check_abandoned_carts,
    "interval", minutes=10, id="check_abandoned_carts_job",
    replace_existing=True)

# Вспомогательная функция, чтобы не дублировать код
async def send_and_update_status(tid, msg, row_idx, col_idx, status_code):
    try:
        await bot.send_message(tid, msg)
        unconfirmed_sheet.update_cell(row_idx, col_idx, status_code)
        print(f"DEBUG: {status_code} отправлено {tid}")
    except Exception as e:
        print(f"DEBUG: Ошибка отправки {status_code} для {tid}: {e}")
# ================== ЗАПУСК ==================
async def handle_webhook(request):
    body = await request.json()
    await dp.feed_update(bot, Update.model_validate(body))
    return web.Response(text="ok")


async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    scheduler.start()

app = web.Application()
app.router.add_post("/webhook", handle_webhook)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)