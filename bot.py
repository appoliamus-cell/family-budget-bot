import os, json, logging, re, calendar, threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           filters, ContextTypes, ConversationHandler)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

BOT_TOKEN  = os.environ["BOT_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = os.environ["GOOGLE_CREDS"]

# ── STATES ────────────────────────────────────────────────────────────────
(WAITING_BUDGET_MENU, WAITING_MENU_TOP,
 WAITING_TYPE,
 WAITING_CAT_ADD, WAITING_AMOUNT_ADD, WAITING_COMMENT_ADD,
 WAITING_CAT_DEL, WAITING_AMOUNT_DEL,
 WAITING_INCOME_WHO, WAITING_INCOME_AMT,
 WAITING_CAT_REST, WAITING_REST_AMT,
 WAITING_MENU_WHO, WAITING_MENU_ITEM, WAITING_FINAL_PICK) = range(15)

# ── KEYBOARDS ─────────────────────────────────────────────────────────────
TOP_KB = ReplyKeyboardMarkup([
    ["💰 БЮДЖЕТ", "🍽 МЕНЮ"],
], resize_keyboard=True)

BUDGET_KB = ReplyKeyboardMarkup([
    ["➕ Добавить трату",  "➖ Удалить трату"],
    ["📊 Остатки",         "💡 На сегодня"],
    ["💰 Внести доход",    "📅 Итого за месяц"],
    ["🔁 Повторить",       "📋 Последние траты"],
    ["🔄 Ввести остаток"],
    ["⬅️ Назад"],
], resize_keyboard=True)

MENU_TOP_KB = ReplyKeyboardMarkup([
    ["🍽 Выбрать меню недели"],
    ["🔄 Новая неделя"],
    ["⬅️ Назад"],
], resize_keyboard=True)

CANCEL_KB = ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)

TYPE_KB = ReplyKeyboardMarkup([
    ["💳 Разовые платежи"],
    ["🛍️ Частые траты"],
    ["❌ Отмена"],
], resize_keyboard=True)

RAZOVYE_KB = ReplyKeyboardMarkup([
    ["🏠 Аренда",        "🏠 Газ"],
    ["🏠 Свет",          "🌐 Интернет"],
    ["📱 Телефон Поля",  "📱 Телефон Женя"],
    ["💳 Раты",          "📺 Подписки"],
    ["🚌 Транспорт",     "💰 Инвестиции"],
    ["🏋️ Бокс",          "🏋️ Теннис"],
    ["🏋️ Зал",           "🏋️ Тренер Валера"],
    ["❌ Отмена"],
], resize_keyboard=True)

CHASYE_KB = ReplyKeyboardMarkup([
    ["🍔 Еда",           "🛍️ Досуг"],
    ["💆 Массаж Поля",   "💆 Массаж Женя"],
    ["🌿 Растения",      "🐕 PsiBufet"],
    ["🐕 Вкусняшки",     "🛋️ Дом"],
    ["💄 Красивая жена", "🧖 Сауна"],
    ["🃏 Покемоны",      "🎲 Прочее"],
    ["❌ Отмена"],
], resize_keyboard=True)

INCOME_KB = ReplyKeyboardMarkup([
    ["👨 Женя зп",        "👩 Поля зп"],
    ["💵 Поля кэш (USD)", "❌ Отмена"],
], resize_keyboard=True)

MENU_WHO_KB = ReplyKeyboardMarkup([
    ["Женя", "Апполинария"],
    ["❌ Отмена"],
], resize_keyboard=True)

# ── СТРОКИ В ТАБЛИЦЕ (проверено по файлу) ────────────────────────────────
ROW_INC_TOT = 10
ROW_EXP_TOT = 40
ROW_BALANCE  = 41

COL_PLAN = 2  # колонка B — план
COL_FACT = 3  # колонка C — факт
COL_REST = 4  # колонка D — остаток (=B-C, формула таблицы)

ROWS = {
    "🏠 Аренда":          14,
    "🏠 Газ":             15,
    "🏠 Свет":            16,
    "🌐 Интернет":        17,
    "📱 Телефон Поля":    18,
    "📱 Телефон Женя":    19,
    "💳 Раты":            20,
    "📺 Подписки":        21,
    "🚌 Транспорт":       22,
    "🏋️ Бокс":            23,
    "🏋️ Теннис":          24,
    "🏋️ Зал":             25,
    "🏋️ Тренер Валера":   26,
    "💰 Инвестиции":      27,
    "🍔 Еда":             28,
    "🛍️ Досуг":           29,
    "💆 Массаж Поля":     30,
    "💆 Массаж Женя":     31,
    "🌿 Растения":        32,
    "🐕 PsiBufet":        33,
    "🐕 Вкусняшки":       34,
    "🛋️ Дом":             35,
    "💄 Красивая жена":   36,
    "🧖 Сауна":           37,
    "🃏 Покемоны":        38,
    "🎲 Прочее":          39,
}

INCOME_ROWS_MAP = {
    "👨 Женя зп":        (7, "Женя зп"),
    "👩 Поля зп":        (8, "Поля зп"),
    "💵 Поля кэш (USD)": (9, "Поля кэш"),
}

ALL_CATS = set(ROWS.keys())

last_action = {}
last_5 = {}

# ── МЕНЮ НЕДЕЛИ: КОНСТРУКТОР ──────────────────────────────────────────────
MENU_CATEGORIES = [
    {
        "key": "ptitsa", "name": "🐓 ПТИЦА", "min": 1, "max": 1,
        "items": [
            "Marry Me Chicken — сливки, шпинат, вяленые томаты",
            "Курица в сливочно-грибном соусе",
            "Курица в сливочно-горчичном соусе",
            "Курица lemon + garlic + butter + herbs",
            "Курица garlic-parmesan",
            "Курица tomato + mozzarella + basil",
            "Курица pesto + mozzarella",
            "Курица в томатно-сливочном соусе",
            "Запечённая курица paprika + garlic",
            "Запечённая курица lemon + herbs",
            "Запечённая курица mustard + honey + herbs",
            "Запечённая курица yogurt + garlic + herbs",
            "Куриные бёдра с чесноком и травами",
            "Куриные бёдра lemon + butter",
            "Куриные котлеты",
            "Рубленые куриные котлеты",
            "Куриные фрикадельки",
            "Куриные фрикадельки в сливочном соусе",
            "Куриные фрикадельки в томатном соусе",
            "Куриные фрикадельки с cottage cheese в томатно-базиликовом соусе",
            "Chicken strips в панировке",
            "Куриные рулетики с сыром и шпинатом",
            "Greek chicken — лимон, чеснок, oregano",
            "Котлеты из индейки",
            "Фрикадельки из индейки",
            "Индейка в сливочно-грибном соусе",
            "Индейка с горчицей и травами",
            "Orzo alla Nerano с курицей, кабачком, parmesan и basil",
        ],
    },
    {
        "key": "govyadina", "name": "🥩 ГОВЯДИНА", "min": 1, "max": 1,
        "items": [
            "Фрикадельки в томатном соусе",
            "Фрикадельки в сливочно-грибном соусе",
            "Swedish meatballs",
            "Домашние котлеты",
            "Рубленый бифштекс",
            "Meatloaf",
            "Beef stew",
            "Говядина, долго тушённая с луком",
            "Говядина с грибами",
            "Beef Stroganoff",
            "Рваная говядина",
            "Рваная говядина в томатах",
            "Говядина с красным вином и овощами",
            "Болоньезе",
            "Ragù",
            "Лазанья",
            "Фаршированные перцы с говядиной и рисом",
            "Голубцы",
            "Тефтели с рисом",
            "Beef burger patties",
        ],
    },
    {
        "key": "subprodukty", "name": "🫀 СУБПРОДУКТЫ", "min": 1, "max": 1,
        "items": [
            "Сердечки в соусе (сливочный / грибной / овощной / томатный)",
            "Желудочки в соусе (сливочный / грибной / овощной / томатный)",
            "Печень (лук / сливки / грибы / яблоко + лук)",
            "Печёночные оладьи",
        ],
    },
    {
        "key": "rastitelnoe", "name": "🌱 РАСТИТЕЛЬНОЕ", "min": 1, "max": 1,
        "items": [
            "Карри на кокосовом молоке (тофу)",
            "Тофу с овощами в соусе по-тайски",
            "Хрустящий тофу + peanut-yogurt dressing",
            "Чечевичные котлеты",
            "Чечевичные фрикадельки",
            "Фаршированные перцы с чечевицей и рисом",
            "Овощная лазанья",
            "Parmigiana di melanzane",
            "Ньокки с грибами в сливочном соусе",
            "Ньокки с томатами и mozzarella",
            "Broccoli pesto pasta — брокколи, basil, pumpkin seeds, pecorino, ricotta",
        ],
    },
    {
        "key": "ryba", "name": "🐟 РЫБА / МОРЕПРОДУКТЫ", "min": 1, "max": 1,
        "items": [
            "Лосось lemon + butter + herbs",
            "Лосось в сливочно-шпинатном соусе",
            "Лосось с горчицей",
            "Лосось с pesto",
            "Форель с лимоном и травами",
            "Треска lemon + butter",
            "Треска в сливочном соусе",
            "Треска с томатами",
            "Белая рыба с травами",
            "Рыбные котлеты",
            "Рыбные фрикадельки",
            "Fish fingers",
            "Креветки garlic + butter",
            "Креветки в сливочно-чесночном соусе",
            "Креветки + томаты + макароны",
            "Мидии в сливочном соусе",
            "Мидии tomato + garlic",
            "Seafood pasta",
            "Risotto с морепродуктами",
        ],
    },
    {
        "key": "sup", "name": "🍲 СУП", "min": 1, "max": 1,
        "items": [
            "Куриный суп",
            "Сливочно-грибной суп",
            "Суп с фрикадельками",
            "Борщ",
            "Рассольник",
            "Щи",
            "Рыбный суп",
            "Гороховый суп",
            "Крем-суп (брокколи / цветная капуста / тыква)",
        ],
    },
    {
        "key": "garniry", "name": "🍚 ГАРНИРЫ", "min": 2, "max": 3,
        "items": [
            "Гречка",
            "Гречка с овощами",
            "Гречка с грибами",
            "Рис",
            "Карри-рис",
            "Киноа",
            "Киноа с лимоном и зеленью",
            "Сливочная киноа",
            "Перловка",
            "Картофельное пюре",
            "Отварная картошка",
            "Запечённая картошка",
            "Приплюснутая картошка с сыром",
            "Макароны",
        ],
    },
    {
        "key": "salaty", "name": "🥗 БОЛЬШИЕ САЛАТЫ (Пн/Ср/Пт)", "min": 3, "max": 3,
        "items": [
            "Тёплый картофельный + feta + огурец + зелень + peanut-soy-sesame dressing",
            "Картофельный + яйца + солёные огурцы + красный лук + mustard-yogurt dressing",
            "Тёплый картофельный + грибы + шпинат + feta + mustard dressing",
            "Манго + avocado + огурец + feta + зелень + орехи/семена",
            "Манго + рис + avocado + огурец + морковь + peanut-soy-sesame dressing",
            "Макароны + mozzarella + томаты + огурец + оливки + зелень + pesto",
            "Макароны + запечённые овощи + feta + шпинат + creamy dressing",
            "Белая фасоль + feta + томаты + огурец + красный лук + peanut dressing",
            "Тёплая белая фасоль + запечённые овощи + feta + зелень",
            "Киноа + avocado + огурец + томаты + feta + орехи/семена",
            "Киноа + запечённая тыква + feta + шпинат + орехи",
            "Рис + яйца + avocado + огурец + морковь + sesame-peanut dressing",
            "Картошка + грибы + яйцо + шпинат + parmesan",
            "Запечённая свёкла + feta + яблоко + зелень + грецкие орехи",
            "Запечённая морковь + чечевица + feta + зелень + орехи",
            "Брокколи + яйца + сыр + семечки + красный лук + creamy mustard dressing",
            "Цветная капуста + нут + feta + зелень + семечки",
            "Капуста + яблоко + морковь + сыр + орехи + creamy dressing",
            "Charred corn + фасоль + feta + перец + красный лук + avocado-basil dressing",
            "Club-style salad: курица + яйца + avocado + томаты + sourdough croutons + creamy mustard dressing (без bacon/Parma ham)",
            "Запечённые овощи ассорти + feta/mozzarella + крупа недели + зелень + насыщенная заправка",
            "Конструктор: картошка/рис/киноа/макароны + овощи + яйцо/сыр/бобовые + avocado + орехи/семена + dressing",
        ],
    },
    {
        "key": "ovoshi", "name": "🥦 ОВОЩИ", "min": 1, "max": 2,
        "items": [
            "Запечённые овощи ассорти (морковь, шампиньоны, кабачок, баклажан, сладкий перец, лук, брокколи, цветная капуста, брюссельская капуста, спаржа, стручковая фасоль, тыква, свёкла, черри)",
            "Свежие овощи ассорти",
            "Брокколи со сливочным маслом",
            "Стручковая фасоль со сливочным маслом",
            "Шпинат со сливочным маслом/сливками",
            "Грибы с луком",
        ],
    },
    {
        "key": "fermentirovannoe", "name": "🥬 ФЕРМЕНТИРОВАННОЕ", "min": 1, "max": 2,
        "items": [
            "Квашеная капуста",
            "Солёные/квашеные огурцы",
            "Квашеная свёкла",
            "Другие неострые квашеные овощи",
        ],
    },
    {
        "key": "gastronomiya", "name": "🍖 ДОМАШНЯЯ ГАСТРОНОМИЯ", "min": 2, "max": 2,
        "items": [
            "Индюшиная буженина",
            "Индейка garlic + herbs",
            "Индейка mustard + herbs",
            "Запечённая куриная грудка",
            "Roast beef",
            "Домашний слабосолёный лосось",
        ],
        "always_extra": "Паштет из индюшачьей печени (обязательно)",
    },
    {
        "key": "spreads", "name": "🥣 SPREADS", "min": 2, "max": 2,
        "items": [
            "Egg spread",
            "Egg + feta",
            "Egg + herbs + mustard",
            "Egg + солёный огурец + зелень",
            "Tuna + egg",
            "Tuna + Greek yogurt + herbs",
            "Tuna + feta",
            "White bean + feta",
            "White bean + roasted garlic",
            "White bean + herbs + lemon",
            "Печёный перец + feta",
            "Баклажан + feta",
        ],
    },
    {
        "key": "zavtraki", "name": "🍳 ЗАВТРАКИ", "min": 3, "max": 4,
        "items": [
            "Овсяноблин + mozzarella + мясная нарезка + томаты + зелень",
            "Овсяноблин + слабосолёный лосось + avocado + яйцо + огурец + зелень",
            "Большая breakfast plate: яйца + мясо + sourdough + avocado + сыр + овощи + зелень + квашеное",
            "Salmon plate: слабосолёный лосось + яйца + avocado + sourdough + сыр + овощи",
            "Tuna + eggs bowl: яйца + tuna + avocado + огурец + томаты + рис + dressing",
            "Sourdough + индюшиная буженина/roast beef + яйца + сыр + овощи",
            "Sourdough + паштет + яйца + солёные огурцы + овощи",
            "Sourdough + spread + яйца/сыр + овощи + ферментированное",
            "Омлет/яичница + мясная нарезка + сыр + грибы/шпинат/томаты + sourdough",
            "Мясо, оставшееся от основного блюда + яйца + avocado + овощи + sourdough",
            "Breakfast chicken patty + яйцо + сыр + томат + sourdough",
            "Йогурт + домашняя гранола + ягоды + орехи + семена",
        ],
    },
    {
        "key": "sousy", "name": "🫙 СОУСЫ / ЗАПРАВКИ", "min": 1, "max": 2,
        "items": [
            "Greek yogurt + peanut butter + soy sauce + sesame oil",
            "Йогурт + горчица + лимон",
            "Йогурт + чеснок + зелень",
            "Йогурт + хрен",
            "Honey-mustard",
            "Olive oil + lemon",
            "Olive oil + balsamic",
            "Pesto",
            "Salsa verde",
            "Garlic butter",
            "Lemon butter",
        ],
    },
]

MENU_ALWAYS = ["🥚 Яйца", "🍞 Sourdough", "🥜 Орехи / семена"]

MENU_PEOPLE = ["Женя", "Апполинария"]

# state в памяти процесса — сбрасывается при перезапуске бота
menu_selections = {p: {} for p in MENU_PEOPLE}
menu_done       = {p: False for p in MENU_PEOPLE}

# ── SHEETS ────────────────────────────────────────────────────────────────
def get_sheet(name="Month"):
    d = json.loads(CREDS_JSON)
    creds = Credentials.from_service_account_info(
        d, scopes=["https://spreadsheets.google.com/feeds",
                   "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(name)

def get_val(ws, row, col):
    try:
        v = ws.cell(row, col).value
        if v is None:
            return 0.0
        s = str(v).strip().replace('\xa0', '').replace(' ', '')
        if s in ('', '-', 'None', 'nan'):
            return 0.0
        if ',' in s and '.' not in s:
            parts = s.split(',')
            if len(parts[-1]) == 3:
                s = s.replace(',', '')
            else:
                s = s.replace(',', '.')
        import math
        result = float(s)
        if math.isnan(result) or math.isinf(result):
            return 0.0
        return result
    except Exception:
        return 0.0

def set_fact(ws, row, val):
    ws.update_cell(row, COL_FACT, val)

def days_left():
    t = datetime.now()
    return max(1, calendar.monthrange(t.year, t.month)[1] - t.day + 1)

def check_warning(ws, row, cat):
    plan = get_val(ws, row, COL_PLAN)
    fact = get_val(ws, row, COL_FACT)
    if plan > 0 and fact / plan >= 0.8:
        return f"⚠️ *{cat}* уже {int(fact/plan*100)}% от бюджета!"
    return None

def month_grade(rest):
    if rest > 0:    return "🏆 Молодцы! Уложились в бюджет!"
    if rest > -500: return "😅 Почти! Небольшой перерасход."
    return "😬 Перерасход в этом месяце."

# ── /start ────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот Семьи Мушат 💪\n\nВыбери раздел:",
        reply_markup=TOP_KB)
    return ConversationHandler.END

# ── ГЛАВНЫЙ ЭКРАН (entry point) ────────────────────────────────────────────
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text

    if t == "💰 БЮДЖЕТ":
        await update.message.reply_text("Что делаем?", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU

    if t == "🍽 МЕНЮ":
        await update.message.reply_text("Что делаем?", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP

    await update.message.reply_text("Выбери раздел 👇", reply_markup=TOP_KB)
    return ConversationHandler.END

# ── РАЗДЕЛ БЮДЖЕТ ─────────────────────────────────────────────────────────
async def budget_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t   = update.message.text
    cid = update.effective_chat.id

    if t == "➕ Добавить трату":
        await update.message.reply_text("Какой тип траты?", reply_markup=TYPE_KB)
        return WAITING_TYPE

    if t == "➖ Удалить трату":
        await update.message.reply_text("Какой тип траты удалить?", reply_markup=TYPE_KB)
        context.user_data["action"] = "del"
        return WAITING_TYPE

    if t == "🔄 Ввести остаток":
        await update.message.reply_text("Какой тип?", reply_markup=TYPE_KB)
        context.user_data["action"] = "rest"
        return WAITING_TYPE

    if t == "💰 Внести доход":
        await update.message.reply_text("Чей доход?", reply_markup=INCOME_KB)
        return WAITING_INCOME_WHO

    if t == "📊 Остатки":
        await cmd_остатки(update)
        return WAITING_BUDGET_MENU
    if t == "💡 На сегодня":
        await cmd_per_day(update)
        return WAITING_BUDGET_MENU
    if t == "📅 Итого за месяц":
        await cmd_итого(update)
        return WAITING_BUDGET_MENU
    if t == "🔁 Повторить":
        await cmd_repeat(update, cid)
        return WAITING_BUDGET_MENU
    if t == "📋 Последние траты":
        await cmd_last5(update, cid)
        return WAITING_BUDGET_MENU

    if t == "⬅️ Назад":
        await update.message.reply_text("Окей 👌", reply_markup=TOP_KB)
        return ConversationHandler.END

    await update.message.reply_text("Нажми кнопку 👇", reply_markup=BUDGET_KB)
    return WAITING_BUDGET_MENU

# ── ВЫБОР ТИПА ────────────────────────────────────────────────────────────
async def pick_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t      = update.message.text
    action = context.user_data.get("action", "add")

    if t == "❌ Отмена":
        context.user_data.clear()
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU

    if t == "💳 Разовые платежи":
        kb = RAZOVYE_KB
    elif t == "🛍️ Частые траты":
        kb = CHASYE_KB
    else:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=TYPE_KB)
        return WAITING_TYPE

    if action == "del":
        await update.message.reply_text("Из какой категории удалить?", reply_markup=kb)
        return WAITING_CAT_DEL
    elif action == "rest":
        await update.message.reply_text(
            "Выбери категорию — введёшь сколько *осталось*:",
            parse_mode="Markdown", reply_markup=kb)
        return WAITING_CAT_REST
    else:
        await update.message.reply_text("Выбери категорию:", reply_markup=kb)
        return WAITING_CAT_ADD

# ── ДОБАВИТЬ ──────────────────────────────────────────────────────────────
async def pick_cat_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    if t not in ALL_CATS:
        await update.message.reply_text("Нажми кнопку 👇")
        return WAITING_CAT_ADD
    context.user_data["cat"] = t
    await update.message.reply_text(
        f"*{t}* — сколько?", parse_mode="Markdown", reply_markup=CANCEL_KB)
    return WAITING_AMOUNT_ADD

async def enter_amount_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t   = update.message.text
    cid = update.effective_chat.id
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    try:
        amount = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Введи только число 👇", reply_markup=CANCEL_KB)
        return WAITING_AMOUNT_ADD

    context.user_data["amount"] = amount
    cat = context.user_data.get("cat")

    if cat == "🎲 Прочее":
        await update.message.reply_text(
            f"Записала {amount:.0f} PLN. Что это? (напиши коротко)",
            reply_markup=CANCEL_KB)
        return WAITING_COMMENT_ADD

    await _save_fact(update, context, cid, cat, amount, comment=None)
    return WAITING_BUDGET_MENU

async def enter_comment_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t   = update.message.text
    cid = update.effective_chat.id
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU

    cat    = context.user_data.get("cat")
    amount = context.user_data.get("amount", 0)
    comment = t

    await _save_fact(update, context, cid, cat, amount, comment=comment)

    try:
        ws_prochee = get_sheet("Прочее")
        today = datetime.now().strftime("%d.%m.%Y")
        all_vals = ws_prochee.col_values(1)
        next_row = max(3, len([v for v in all_vals if v]) + 1)
        ws_prochee.update_cell(next_row, 1, today)
        ws_prochee.update_cell(next_row, 2, amount)
        ws_prochee.update_cell(next_row, 3, comment)
    except Exception as e:
        logging.error(f"Прочее sheet error: {e}")

    return WAITING_BUDGET_MENU

async def _save_fact(update, context, cid, cat, amount, comment):
    row = ROWS.get(cat)
    try:
        ws  = get_sheet()
        cur = get_val(ws, row, COL_FACT)
        new = cur + amount
        set_fact(ws, row, new)
        plan = get_val(ws, row, COL_PLAN)
        rest = plan - new
        flag = "✅" if rest >= 0 else "⚠️"
        msg  = f"✍️ *{cat}* +{amount:.0f} PLN\n{flag} Остаток: {rest:.0f} PLN"
        if comment:
            msg += f"\n📝 {comment}"
        last_action[cid] = (cat, amount)
        hist = last_5.get(cid, [])
        hist.insert(0, (cat, amount))
        last_5[cid] = hist[:5]
        warn = check_warning(ws, row, cat)
        if warn:
            msg += f"\n\n{warn}"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка записи.", reply_markup=BUDGET_KB)
    context.user_data.clear()

# ── УДАЛИТЬ ───────────────────────────────────────────────────────────────
async def pick_cat_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    if t not in ALL_CATS:
        await update.message.reply_text("Нажми кнопку 👇")
        return WAITING_CAT_DEL
    context.user_data["cat"] = t
    await update.message.reply_text(
        f"*{t}* — сколько удалить?", parse_mode="Markdown", reply_markup=CANCEL_KB)
    return WAITING_AMOUNT_DEL

async def enter_amount_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    try:
        amount = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Введи только число", reply_markup=CANCEL_KB)
        return WAITING_AMOUNT_DEL
    cat = context.user_data.get("cat")
    row = ROWS.get(cat)
    try:
        ws  = get_sheet()
        cur = get_val(ws, row, COL_FACT)
        new = max(0.0, cur - amount)
        set_fact(ws, row, new)
        await update.message.reply_text(
            f"🗑 *{cat}* -{amount:.0f} PLN\nТеперь: {new:.0f} PLN",
            parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=BUDGET_KB)
    context.user_data.clear()
    return WAITING_BUDGET_MENU

# ── ВВЕСТИ ОСТАТОК ────────────────────────────────────────────────────────
async def pick_cat_rest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    if t not in ALL_CATS:
        await update.message.reply_text("Нажми кнопку 👇")
        return WAITING_CAT_REST
    context.user_data["cat"] = t
    try:
        ws   = get_sheet()
        plan = get_val(ws, ROWS[t], COL_PLAN)
        await update.message.reply_text(
            f"*{t}*\nПлан: {plan:.0f} PLN\nСколько осталось?",
            parse_mode="Markdown", reply_markup=CANCEL_KB)
    except Exception:
        await update.message.reply_text(
            f"*{t}* — сколько осталось?",
            parse_mode="Markdown", reply_markup=CANCEL_KB)
    return WAITING_REST_AMT

async def enter_rest_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    try:
        rest_input = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Введи только число", reply_markup=CANCEL_KB)
        return WAITING_REST_AMT
    cat = context.user_data.get("cat")
    row = ROWS.get(cat)
    try:
        ws   = get_sheet()
        plan = get_val(ws, row, COL_PLAN)
        fact = plan - rest_input
        if fact < 0:
            await update.message.reply_text(
                f"⚠️ Остаток {rest_input:.0f} больше плана {plan:.0f}\nПроверь цифры!",
                reply_markup=BUDGET_KB)
            return WAITING_BUDGET_MENU
        set_fact(ws, row, fact)
        await update.message.reply_text(
            f"✅ *{cat}*\nПлан: {plan:.0f} PLN\nОстаток: {rest_input:.0f} PLN\nЗаписала факт: {fact:.0f} PLN",
            parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=BUDGET_KB)
    context.user_data.clear()
    return WAITING_BUDGET_MENU

# ── ДОХОД ─────────────────────────────────────────────────────────────────
async def pick_income_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    info = INCOME_ROWS_MAP.get(t)
    if not info:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=INCOME_KB)
        return WAITING_INCOME_WHO
    context.user_data["income"] = info
    await update.message.reply_text(
        f"*{info[1]}* — сколько?", parse_mode="Markdown", reply_markup=CANCEL_KB)
    return WAITING_INCOME_AMT

async def enter_income_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=BUDGET_KB)
        return WAITING_BUDGET_MENU
    try:
        amount = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Введи только число", reply_markup=CANCEL_KB)
        return WAITING_INCOME_AMT
    row, name = context.user_data.get("income", (None, None))
    try:
        ws = get_sheet()
        ws.update_cell(row, COL_FACT, amount)
        await update.message.reply_text(
            f"💰 *{name}* = {amount:.0f} PLN — записала!",
            parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=BUDGET_KB)
    context.user_data.clear()
    return WAITING_BUDGET_MENU

# ── РАЗДЕЛ МЕНЮ НЕДЕЛИ ────────────────────────────────────────────────────
async def menu_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text

    if t == "🍽 Выбрать меню недели":
        await update.message.reply_text("Кто выбирает?", reply_markup=MENU_WHO_KB)
        return WAITING_MENU_WHO

    if t == "🔄 Новая неделя":
        for p in MENU_PEOPLE:
            menu_selections[p] = {}
            menu_done[p] = False
        await update.message.reply_text(
            "Начали новую неделю — прошлые выборы очищены 🔄", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP

    if t == "⬅️ Назад":
        await update.message.reply_text("Окей 👌", reply_markup=TOP_KB)
        return ConversationHandler.END

    await update.message.reply_text("Нажми кнопку 👇", reply_markup=MENU_TOP_KB)
    return WAITING_MENU_TOP

def _menu_render_category(context: ContextTypes.DEFAULT_TYPE):
    m = context.user_data["menu"]
    cat = MENU_CATEGORIES[m["cat"]]
    items = cat["items"]
    need = f"{cat['min']}" + (f"–{cat['max']}" if cat['max'] != cat['min'] else "")
    lines = [f"{cat['name']} — выбери {need}\n"]
    for i, it in enumerate(items, start=1):
        lines.append(f"{i}. {it}")
    lines.append("\n✍️ Напиши номера через запятую (например: 2, 5, 9)")
    return "\n".join(lines)

async def pick_menu_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP
    if t not in MENU_PEOPLE:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=MENU_WHO_KB)
        return WAITING_MENU_WHO
    context.user_data["menu"] = {"who": t, "cat": 0, "selected": {}}
    await update.message.reply_text(
        f"Погнали, {t}! Вот первая категория 👇\n\n{_menu_render_category(context)}",
        reply_markup=CANCEL_KB)
    return WAITING_MENU_ITEM

async def menu_item_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        context.user_data.pop("menu", None)
        await update.message.reply_text("Окей 👌", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP

    m = context.user_data.get("menu")
    if not m:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP

    cat = MENU_CATEGORIES[m["cat"]]
    items = cat["items"]

    nums = sorted(set(int(x) for x in re.findall(r"\d+", t)))
    bad = [n for n in nums if n < 1 or n > len(items)]
    if not nums or bad:
        await update.message.reply_text(
            f"Не поняла номера 🤔 Напиши числа от 1 до {len(items)} через запятую.")
        return WAITING_MENU_ITEM
    if not (cat["min"] <= len(nums) <= cat["max"]):
        need = f"{cat['min']}" + (f"–{cat['max']}" if cat['max'] != cat['min'] else "")
        await update.message.reply_text(
            f"Нужно выбрать именно {need} — ты написала {len(nums)}. Попробуй ещё раз 👇")
        return WAITING_MENU_ITEM

    chosen = [items[n - 1] for n in nums]
    if cat.get("always_extra"):
        chosen.append(cat["always_extra"])
    m["selected"][cat["key"]] = chosen

    return await _menu_advance_category(update, context)

async def _menu_advance_category(update, context):
    """Переходит к следующей категории или финиширует опрос этого человека."""
    m = context.user_data["menu"]
    m["cat"] += 1
    if m["cat"] >= len(MENU_CATEGORIES):
        who = m["who"]
        menu_selections[who] = m["selected"]
        menu_done[who] = True
        context.user_data.pop("menu", None)
        await update.message.reply_text(f"Спасибо, {who}! Твой выбор сохранён 🎉")
        if all(menu_done.values()):
            return await _menu_start_final(update, context)
        waiting_for = [p for p in MENU_PEOPLE if not menu_done[p]]
        await update.message.reply_text(
            f"Жду ещё: {', '.join(waiting_for)} 👀", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP
    await update.message.reply_text(_menu_render_category(context), reply_markup=CANCEL_KB)
    return WAITING_MENU_ITEM

async def _menu_start_final(update, context):
    """Проверяет, есть ли категории, где совпало больше вариантов, чем нужно."""
    need_narrow = []
    for cat in MENU_CATEGORIES:
        a = set(menu_selections["Женя"].get(cat["key"], []))
        b = set(menu_selections["Апполинария"].get(cat["key"], []))
        core = sorted(a & b)
        if len(core) > cat["max"]:
            need_narrow.append({
                "key": cat["key"], "name": cat["name"],
                "min": cat["min"], "max": cat["max"], "core": core,
            })
    if not need_narrow:
        await update.message.reply_text(
            _menu_build_final(), parse_mode="Markdown", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP
    context.user_data["final_narrow"] = {"items": need_narrow, "idx": 0, "chosen": {}}
    await update.message.reply_text(
        "Есть категории, где совпало больше вариантов, чем нужно — уточним, что оставляем 👇")
    await update.message.reply_text(_menu_render_narrow(context), reply_markup=CANCEL_KB)
    return WAITING_FINAL_PICK

def _menu_render_narrow(context: ContextTypes.DEFAULT_TYPE):
    fn = context.user_data["final_narrow"]
    item = fn["items"][fn["idx"]]
    need = f"{item['min']}" + (f"–{item['max']}" if item['max'] != item['min'] else "")
    lines = [f"{item['name']} — совпало у обоих, но нужно оставить {need}:\n"]
    for i, d in enumerate(item["core"], start=1):
        lines.append(f"{i}. {d}")
    lines.append("\n✍️ Напиши номера через запятую")
    return "\n".join(lines)

async def final_pick_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    fn = context.user_data.get("final_narrow")
    if t == "❌ Отмена":
        context.user_data.pop("final_narrow", None)
        await update.message.reply_text("Окей 👌", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP
    if not fn:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP

    item = fn["items"][fn["idx"]]
    core = item["core"]
    nums = sorted(set(int(x) for x in re.findall(r"\d+", t)))
    bad = [n for n in nums if n < 1 or n > len(core)]
    if not nums or bad:
        await update.message.reply_text(
            f"Не поняла номера 🤔 Напиши числа от 1 до {len(core)} через запятую.")
        return WAITING_FINAL_PICK
    if not (item["min"] <= len(nums) <= item["max"]):
        need = f"{item['min']}" + (f"–{item['max']}" if item['max'] != item['min'] else "")
        await update.message.reply_text(
            f"Нужно оставить именно {need} — ты написала {len(nums)}. Попробуй ещё раз 👇")
        return WAITING_FINAL_PICK

    fn["chosen"][item["key"]] = [core[n - 1] for n in nums]
    fn["idx"] += 1
    if fn["idx"] >= len(fn["items"]):
        final_text = _menu_build_final(narrow=fn["chosen"])
        context.user_data.pop("final_narrow", None)
        await update.message.reply_text(final_text, parse_mode="Markdown", reply_markup=MENU_TOP_KB)
        return WAITING_MENU_TOP
    await update.message.reply_text(_menu_render_narrow(context), reply_markup=CANCEL_KB)
    return WAITING_FINAL_PICK

def _menu_person_block(who: str, emoji: str) -> str:
    lines = [f"\n{emoji} *Выбор — {who}:*"]
    has_any = False
    for cat in MENU_CATEGORIES:
        picks = menu_selections[who].get(cat["key"], [])
        if not picks:
            continue
        has_any = True
        lines.append(f"\n{cat['name']}:")
        for d in picks:
            lines.append(f"  • {d}")
    if not has_any:
        lines.append("(пусто)")
    return "\n".join(lines)

def _menu_build_final(narrow=None):
    """narrow — необязательный dict {cat_key: [выбранные из совпавших]},
    используется, если совпадений было больше, чем позволяет категория."""
    narrow = narrow or {}

    lines = ["🎉 *Меню недели готово!*"]
    lines.append(_menu_person_block("Женя", "👨"))
    lines.append(_menu_person_block("Апполинария", "👩"))

    lines.append("\n\n✅ *Итоговое меню (совпадения):*")
    any_discuss = False
    for cat in MENU_CATEGORIES:
        a = set(menu_selections["Женя"].get(cat["key"], []))
        b = set(menu_selections["Апполинария"].get(cat["key"], []))
        core = set(narrow[cat["key"]]) if cat["key"] in narrow else (a & b)
        only_a = a - b
        only_b = b - a
        if not (core or only_a or only_b):
            continue
        lines.append(f"\n{cat['name']}:")
        for d in sorted(core):
            lines.append(f"  ✅ {d}")
        for d in sorted(only_a):
            lines.append(f"  🟡 {d} (только Женя)")
            any_discuss = True
        for d in sorted(only_b):
            lines.append(f"  🟡 {d} (только Апполинария)")
            any_discuss = True
    lines.append("\nВсегда в меню: " + ", ".join(MENU_ALWAYS) + ".")
    if any_discuss:
        lines.append("\n🟡 — совпадений нет, решите вручную кто прав 🙂")
    return "\n".join(lines)

# ── ИНФОРМАЦИЯ ────────────────────────────────────────────────────────────
async def cmd_остатки(update: Update):
    try:
        ws    = get_sheet()
        lines = ["📊 *Остатки:*\n"]
        for cat, row in ROWS.items():
            plan = get_val(ws, row, COL_PLAN)
            fact = get_val(ws, row, COL_FACT)
            rest = plan - fact
            if rest > 0:
                lines.append(f"{cat} — {rest:.0f} PLN")
        if len(lines) == 1:
            lines.append("Всё потрачено 🎉")
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=BUDGET_KB)

async def cmd_per_day(update: Update):
    try:
        ws   = get_sheet()
        left = days_left()
        lines = [f"💡 *На сегодня* (осталось {left} дн.)\n"]
        for row, label in [(28, "🍔 Еда"), (29, "🛍️ Досуг"), (39, "🎲 Прочее")]:
            plan = get_val(ws, row, COL_PLAN)
            fact = get_val(ws, row, COL_FACT)
            rest = plan - fact
            pd   = rest / left
            lines.append(f"{'✅' if pd >= 0 else '⚠️'} {label} — *{pd:.0f} PLN/день*")
        inc_f = get_val(ws, ROW_INC_TOT, COL_FACT)
        exp_f = get_val(ws, ROW_EXP_TOT, COL_FACT)
        rest_total = inc_f - exp_f
        pd_total   = rest_total / left
        lines.append(f"{'✅' if pd_total >= 0 else '⚠️'} 💰 Остаток — *{pd_total:.0f} PLN/день*")
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=BUDGET_KB)

async def cmd_итого(update: Update):
    try:
        ws    = get_sheet()
        inc_p = get_val(ws, ROW_INC_TOT, COL_PLAN)
        inc_f = get_val(ws, ROW_INC_TOT, COL_FACT)
        exp_p = get_val(ws, ROW_EXP_TOT, COL_PLAN)
        exp_f = get_val(ws, ROW_EXP_TOT, COL_FACT)
        rest  = inc_f - exp_f
        msg = (f"📅 *Итого за месяц:*\n\n"
               f"💵 Доходы: {inc_f:.0f} / {inc_p:.0f} PLN\n"
               f"📤 Расходы: {exp_f:.0f} / {exp_p:.0f} PLN\n"
               f"💰 Остаток: *{rest:.0f} PLN*\n\n{month_grade(rest)}")
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=BUDGET_KB)

async def cmd_repeat(update: Update, cid: int):
    act = last_action.get(cid)
    if not act:
        await update.message.reply_text("Нет последней траты 🤷", reply_markup=BUDGET_KB)
        return
    cat, amount = act
    row = ROWS.get(cat)
    try:
        ws  = get_sheet()
        cur = get_val(ws, row, COL_FACT)
        new = cur + amount
        set_fact(ws, row, new)
        rest = get_val(ws, row, COL_REST)
        await update.message.reply_text(
            f"🔁 *{cat}* +{amount:.0f} PLN\n✅ Остаток: {rest:.0f} PLN",
            parse_mode="Markdown", reply_markup=BUDGET_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=BUDGET_KB)

async def cmd_last5(update: Update, cid: int):
    hist = last_5.get(cid, [])
    if not hist:
        await update.message.reply_text("Пока нет трат 🤷", reply_markup=BUDGET_KB)
        return
    lines = ["📋 *Последние траты:*\n"]
    for cat, amt in hist:
        lines.append(f"• {cat} — {amt:.0f} PLN")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=BUDGET_KB)

# ── ВЕБ-СЕРВЕР ────────────────────────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()

# ── MAIN ──────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, menu)],
        states={
            WAITING_BUDGET_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, budget_menu)],
            WAITING_MENU_TOP:    [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_top)],
            WAITING_TYPE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_type)],
            WAITING_CAT_ADD:     [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_cat_add)],
            WAITING_AMOUNT_ADD:  [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount_add)],
            WAITING_COMMENT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_comment_add)],
            WAITING_CAT_DEL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_cat_del)],
            WAITING_AMOUNT_DEL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount_del)],
            WAITING_INCOME_WHO:  [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_income_who)],
            WAITING_INCOME_AMT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_income_amt)],
            WAITING_CAT_REST:    [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_cat_rest)],
            WAITING_REST_AMT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_rest_amt)],
            WAITING_MENU_WHO:    [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_menu_who)],
            WAITING_MENU_ITEM:   [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_item_answer)],
            WAITING_FINAL_PICK:  [MessageHandler(filters.TEXT & ~filters.COMMAND, final_pick_answer)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    main()
