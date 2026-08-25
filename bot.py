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
(WAITING_TYPE,
 WAITING_CAT_ADD, WAITING_AMOUNT_ADD, WAITING_COMMENT_ADD,
 WAITING_CAT_DEL, WAITING_AMOUNT_DEL,
 WAITING_INCOME_WHO, WAITING_INCOME_AMT,
 WAITING_CAT_REST, WAITING_REST_AMT,
 WAITING_MENU_WHO, WAITING_MENU_ITEM, WAITING_FINAL_PICK) = range(13)

# ── KEYBAORDS ─────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup([
    ["➕ Добавить трату",  "➖ Удалить трату"],
    ["📊 Остатки",         "💡 На сегодня"],
    ["💰 Внести доход",    "📅 Итого за месяц"],
    ["🔁 Повторить",       "📋 Последние траты"],
    ["🔄 Ввести остаток"],
    ["🍽 Меню недели"],
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

# все кнопки категорий
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

# Категории, которые не требуют выбора — присутствуют в меню всегда
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
        "👋 Привет! Я бот Семьи Мушат 💪\n\nЧто делаем?",
        reply_markup=MAIN_KB)
    return ConversationHandler.END

# ── ГЛАВНОЕ МЕНЮ ──────────────────────────────────────────────────────────
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t   = update.message.text
    cid = update.effective_chat.id

    if t == "➕ Добавить трату":
        await update.message.reply_text(
            "Какой тип траты?", reply_markup=TYPE_KB)
        return WAITING_TYPE

    if t == "➖ Удалить трату":
        await update.message.reply_text(
            "Какой тип траты удалить?", reply_markup=TYPE_KB)
        context.user_data["action"] = "del"
        return WAITING_TYPE

    if t == "🔄 Ввести остаток":
        await
