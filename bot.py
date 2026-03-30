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
(WAITING_CAT_ADD, WAITING_AMOUNT_ADD,
 WAITING_CAT_DEL, WAITING_AMOUNT_DEL,
 WAITING_INCOME_WHO, WAITING_INCOME_AMT,
 WAITING_CAT_REST, WAITING_REST_AMT) = range(8)

# ── KEYBOARDS ─────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup([
    ["➕ Добавить трату",  "➖ Удалить трату"],
    ["📊 Остатки",         "💡 На сегодня"],
    ["💰 Внести доход",    "📅 Итого за месяц"],
    ["🔁 Повторить",       "📋 Последние траты"],
    ["🔄 Ввести остаток"],
], resize_keyboard=True)

CANCEL_KB = ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)

CATS_KB = ReplyKeyboardMarkup([
    ["🏠 Аренда",          "🏠 Газ",             "🏠 Свет"],
    ["🍔 Еда",             "🛍️ Досуг",           "🧖 Сауна"],
    ["💆 Массаж Поля",     "💆 Массаж Женя",     "💄 Красивая жена"],
    ["🏋️ Бокс",            "🏋️ Теннис",          "🏋️ Зал"],
    ["🏋️ Тренер Валера",   "🚌 Транспорт",       "🌿 Растения"],
    ["🐕 PsiBufet",        "🐕 Вкусняшки",       "🛋️ Дом"],
    ["🎉 Праздники",       "✈️ Отпуск",           "🃏 Покемоны"],
    ["💳 Айфоны",          "💳 Макбук",           "💰 Инвестиции"],
    ["💳 Кларна",          "❌ Отмена"],
], resize_keyboard=True)

INCOME_KB = ReplyKeyboardMarkup([
    ["👨 Женя зп",        "👩 Поля зп"],
    ["💵 Поля кэш (USD)", "❌ Отмена"],
], resize_keyboard=True)

# ── ТОЧНАЯ КАРТА СТРОК (проверено по файлу) ───────────────────────────────
# Колонки: C=3(план), D=4(факт), E=5(остаток)
# Доходы
ROW_ZHENIA_INC  = 7
ROW_POLIA_INC   = 8
ROW_CASH_INC    = 9
ROW_INC_TOT     = 10  # ИТОГО ДОХОДЫ — D10

# Расходы
ROWS = {
    "🏠 Аренда":          14,
    "🏠 Газ":             15,
    "🏠 Свет":            16,
    "📱 Телефон Поля":    17,
    "📱 Телефон Женя":    18,
    "🌐 Интернет":        19,
    "💳 Айфоны":          20,
    "💳 Макбук":          21,
    "📺 Netflix":         22,
    "📺 HBO Max":         23,
    "📺 Spotify":         24,
    "📺 YouTube":         25,
    "📺 ChatGPT":         26,
    "📺 Claude":          27,
    "📺 Telegram Поля":   28,
    "📺 Telegram Женя":   29,
    "📺 iCloud Поля":     30,
    "📺 iCloud Женя":     31,
    "📺 LARQ":            32,
    "📺 UberOne":         33,
    "📺 DazCam":          34,
    "🍔 Еда":             35,
    "🛍️ Досуг":           36,
    "🐕 PsiBufet":        37,
    "🐕 Вкусняшки":       38,
    "💆 Массаж Женя":     39,
    "💆 Массаж Поля":     40,
    "🏋️ Бокс":            41,
    "🏋️ Теннис":          42,
    "🏋️ Зал":             43,
    "🏋️ Тренер Валера":   44,
    "🚌 Транспорт":       45,
    "🎉 Праздники":       46,
    "✈️ Отпуск":           47,
    "🌿 Растения":        48,
    "🛋️ Дом":             49,
    "💄 Красивая жена":   50,
    "🧖 Сауна":           51,
    "🃏 Покемоны":        52,
    "💰 Инвестиции":      53,
    "💳 Кларна":          54,
}

ROW_EXP_TOT = 55   # ИТОГО РАСХОДЫ
ROW_BALANCE  = 56  # ОСТАТОК

COL_PLAN = 3  # колонка C
COL_FACT = 4  # колонка D

INCOME_ROWS_MAP = {
    "👨 Женя зп":        (ROW_ZHENIA_INC, "Женя зп"),
    "👩 Поля зп":        (ROW_POLIA_INC,  "Поля зп"),
    "💵 Поля кэш (USD)": (ROW_CASH_INC,   "Поля кэш"),
}

BTN_MAP = {k: k for k in ROWS.keys()}

last_action = {}
last_5      = {}

# ── SHEETS ────────────────────────────────────────────────────────────────
def get_sheet():
    d = json.loads(CREDS_JSON)
    creds = Credentials.from_service_account_info(
        d, scopes=["https://spreadsheets.google.com/feeds",
                   "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet("Month")

def get_val(ws, row, col):
    """Читает значение ячейки. Возвращает float или 0.0."""
    try:
        v = ws.cell(row, col).value
        if v is None:
            return 0.0
        s = str(v).strip().replace('\xa0', '').replace(' ', '')
        if s in ('', '-', 'None', 'nan'):
            return 0.0
        cleaned = s.replace(',', '.')
        result = float(cleaned)
        import math
        if math.isnan(result) or math.isinf(result):
            return 0.0
        return result
    except Exception:
        return 0.0

def set_val(ws, row, val):
    """Записывает значение в колонку D (Факт)."""
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
    if rest > 0:   return "🏆 Молодцы! Уложились в бюджет!"
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
        await update.message.reply_text("Выбери категорию:", reply_markup=CATS_KB)
        return WAITING_CAT_ADD

    if t == "➖ Удалить трату":
        await update.message.reply_text("Из какой категории удалить?", reply_markup=CATS_KB)
        return WAITING_CAT_DEL

    if t == "💰 Внести доход":
        await update.message.reply_text("Чей доход?", reply_markup=INCOME_KB)
        return WAITING_INCOME_WHO

    if t == "🔄 Ввести остаток":
        await update.message.reply_text(
            "Выбери категорию — введёшь сколько *осталось*, я посчитаю сколько потрачено:",
            parse_mode="Markdown", reply_markup=CATS_KB)
        return WAITING_CAT_REST

    if t == "📊 Остатки":        await cmd_остатки(update)
    elif t == "💡 На сегодня":   await cmd_per_day(update)
    elif t == "📅 Итого за месяц": await cmd_итого(update)
    elif t == "🔁 Повторить":    await cmd_repeat(update, cid)
    elif t == "📋 Последние траты": await cmd_last5(update, cid)
    elif t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)

    return ConversationHandler.END

# ── ДОБАВИТЬ ТРАТУ ────────────────────────────────────────────────────────
async def pick_cat_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    cat = BTN_MAP.get(t)
    if not cat:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=CATS_KB)
        return WAITING_CAT_ADD
    context.user_data["cat"] = cat
    await update.message.reply_text(
        f"*{cat}* — сколько?", parse_mode="Markdown", reply_markup=CANCEL_KB)
    return WAITING_AMOUNT_ADD

async def enter_amount_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t   = update.message.text
    cid = update.effective_chat.id
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    try:
        amount = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text(
            "Введи только число, например *320*",
            parse_mode="Markdown", reply_markup=CANCEL_KB)
        return WAITING_AMOUNT_ADD

    cat = context.user_data.get("cat")
    row = ROWS.get(cat)
    try:
        ws   = get_sheet()
        cur  = get_val(ws, row, COL_FACT)
        new  = cur + amount
        set_val(ws, row, new)
        plan = get_val(ws, row, COL_PLAN)
        rest = plan - new
        flag = "✅" if rest >= 0 else "⚠️"
        msg  = f"✍️ *{cat}* +{amount:.0f} PLN\n{flag} Остаток: {rest:.0f} PLN"
        last_action[cid] = (cat, amount)
        hist = last_5.get(cid, [])
        hist.insert(0, (cat, amount))
        last_5[cid] = hist[:5]
        warn = check_warning(ws, row, cat)
        if warn:
            msg += f"\n\n{warn}"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка записи.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ── УДАЛИТЬ ТРАТУ ─────────────────────────────────────────────────────────
async def pick_cat_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    cat = BTN_MAP.get(t)
    if not cat:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=CATS_KB)
        return WAITING_CAT_DEL
    context.user_data["cat"] = cat
    await update.message.reply_text(
        f"*{cat}* — сколько удалить?", parse_mode="Markdown", reply_markup=CANCEL_KB)
    return WAITING_AMOUNT_DEL

async def enter_amount_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
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
        set_val(ws, row, new)
        await update.message.reply_text(
            f"🗑 *{cat}* -{amount:.0f} PLN\nТеперь: {new:.0f} PLN",
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ── ВВЕСТИ ОСТАТОК (обратная формула) ─────────────────────────────────────
async def pick_cat_rest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    cat = BTN_MAP.get(t)
    if not cat:
        await update.message.reply_text("Нажми кнопку 👇", reply_markup=CATS_KB)
        return WAITING_CAT_REST
    context.user_data["cat"] = cat
    try:
        ws   = get_sheet()
        plan = get_val(ws, ROWS[cat], COL_PLAN)
        await update.message.reply_text(
            f"*{cat}*\nПлан: {plan:.0f} PLN\n\nСколько осталось?",
            parse_mode="Markdown", reply_markup=CANCEL_KB)
    except Exception:
        await update.message.reply_text(
            f"*{cat}* — сколько осталось?",
            parse_mode="Markdown", reply_markup=CANCEL_KB)
    return WAITING_REST_AMT

async def enter_rest_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
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
                reply_markup=MAIN_KB)
            return ConversationHandler.END
        set_val(ws, row, fact)
        await update.message.reply_text(
            f"✅ *{cat}*\nПлан: {plan:.0f} PLN\nОстаток: {rest_input:.0f} PLN\nЗаписала факт: {fact:.0f} PLN",
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ── ВНЕСТИ ДОХОД ──────────────────────────────────────────────────────────
async def pick_income_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
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
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    try:
        amount = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Введи только число", reply_markup=CANCEL_KB)
        return WAITING_INCOME_AMT
    row, name = context.user_data.get("income", (None, None))
    try:
        ws = get_sheet()
        set_val(ws, row, amount)
        await update.message.reply_text(
            f"💰 *{name}* = {amount:.0f} PLN — записала!",
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ── ОСТАТКИ ───────────────────────────────────────────────────────────────
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
            "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

# ── НА СЕГОДНЯ (per day) ──────────────────────────────────────────────────
async def cmd_per_day(update: Update):
    try:
        ws   = get_sheet()
        left = days_left()
        lines = [f"💡 *На сегодня* (осталось {left} дн.)\n"]
        for row, label in [(35, "🍔 Еда"), (36, "🛍️ Досуг")]:
            plan = get_val(ws, row, COL_PLAN)
            fact = get_val(ws, row, COL_FACT)
            rest = plan - fact
            pd   = rest / left
            lines.append(f"{'✅' if pd >= 0 else '⚠️'} {label} — *{pd:.0f} PLN/день*")
        # Общий остаток
        inc_f = get_val(ws, ROW_INC_TOT, COL_FACT)
        exp_f = get_val(ws, ROW_EXP_TOT, COL_FACT)
        rest_total = inc_f - exp_f
        pd_total   = rest_total / left
        lines.append(f"{'✅' if pd_total >= 0 else '⚠️'} 💰 Остаток — *{pd_total:.0f} PLN/день*")
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

# ── ИТОГО ─────────────────────────────────────────────────────────────────
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
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

# ── ПОВТОРИТЬ ─────────────────────────────────────────────────────────────
async def cmd_repeat(update: Update, cid: int):
    act = last_action.get(cid)
    if not act:
        await update.message.reply_text("Нет последней траты 🤷", reply_markup=MAIN_KB)
        return
    cat, amount = act
    row = ROWS.get(cat)
    try:
        ws   = get_sheet()
        cur  = get_val(ws, row, COL_FACT)
        new  = cur + amount
        set_val(ws, row, new)
        plan = get_val(ws, row, COL_PLAN)
        rest = plan - new
        await update.message.reply_text(
            f"🔁 Повторила: *{cat}* +{amount:.0f} PLN\n✅ Остаток: {rest:.0f} PLN",
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

# ── ПОСЛЕДНИЕ ТРАТЫ ───────────────────────────────────────────────────────
async def cmd_last5(update: Update, cid: int):
    hist = last_5.get(cid, [])
    if not hist:
        await update.message.reply_text(
            "Пока нет трат в этой сессии 🤷", reply_markup=MAIN_KB)
        return
    lines = ["📋 *Последние траты:*\n"]
    for cat, amt in hist:
        lines.append(f"• {cat} — {amt:.0f} PLN")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)

# ── ВЕБ-СЕРВЕР (UptimeRobot) ──────────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_web_server():
    HTTPServer(("0.0.0.0", 8080), PingHandler).serve_forever()

# ── MAIN ──────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, menu)],
        states={
            WAITING_CAT_ADD:    [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_cat_add)],
            WAITING_AMOUNT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount_add)],
            WAITING_CAT_DEL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_cat_del)],
            WAITING_AMOUNT_DEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount_del)],
            WAITING_INCOME_WHO: [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_income_who)],
            WAITING_INCOME_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_income_amt)],
            WAITING_CAT_REST:   [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_cat_rest)],
            WAITING_REST_AMT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_rest_amt)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    main()
