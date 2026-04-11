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
 WAITING_CAT_REST, WAITING_REST_AMT) = range(10)
 
# ── KEYBOARDS ─────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup([
    ["➕ Добавить трату",  "➖ Удалить трату"],
    ["📊 Остатки",         "💡 На сегодня"],
    ["💰 Внести доход",    "📅 Итого за месяц"],
    ["🔁 Повторить",       "📋 Последние траты"],
    ["🔄 Ввести остаток"],
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
        await update.message.reply_text(
            "Какой тип?", reply_markup=TYPE_KB)
        context.user_data["action"] = "rest"
        return WAITING_TYPE
 
    if t == "💰 Внести доход":
        await update.message.reply_text("Чей доход?", reply_markup=INCOME_KB)
        return WAITING_INCOME_WHO
 
    if t == "📊 Остатки":        await cmd_остатки(update)
    elif t == "💡 На сегодня":   await cmd_per_day(update)
    elif t == "📅 Итого за месяц": await cmd_итого(update)
    elif t == "🔁 Повторить":    await cmd_repeat(update, cid)
    elif t == "📋 Последние траты": await cmd_last5(update, cid)
    elif t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
 
    return ConversationHandler.END
 
# ── ВЫБОР ТИПА ────────────────────────────────────────────────────────────
async def pick_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t      = update.message.text
    action = context.user_data.get("action", "add")
 
    if t == "❌ Отмена":
        context.user_data.clear()
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
 
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
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
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
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    try:
        amount = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Введи только число 👇", reply_markup=CANCEL_KB)
        return WAITING_AMOUNT_ADD
 
    context.user_data["amount"] = amount
    cat = context.user_data.get("cat")
 
    # Для Прочего — спрашиваем комментарий
    if cat == "🎲 Прочее":
        await update.message.reply_text(
            f"Записала {amount:.0f} PLN. Что это? (напиши коротко)",
            reply_markup=CANCEL_KB)
        return WAITING_COMMENT_ADD
 
    # Для остальных — сразу записываем
    await _save_fact(update, context, cid, cat, amount, comment=None)
    return ConversationHandler.END
 
async def enter_comment_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t   = update.message.text
    cid = update.effective_chat.id
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
 
    cat    = context.user_data.get("cat")
    amount = context.user_data.get("amount", 0)
    comment = t
 
    await _save_fact(update, context, cid, cat, amount, comment=comment)
 
    # Также записываем в лист Прочее
    try:
        ws_prochee = get_sheet("Прочее")
        today = datetime.now().strftime("%d.%m.%Y")
        # Найдём первую пустую строку начиная с 3
      all_vals = ws_prochee.col_values(1)
next_row = max(3, len([v for v in all_vals if v]) + 1)
        ws_prochee.update_cell(next_row, 1, today)
        ws_prochee.update_cell(next_row, 2, amount)
        ws_prochee.update_cell(next_row, 3, comment)
    except Exception as e:
        logging.error(f"Прочее sheet error: {e}")
 
    return ConversationHandler.END
 
async def _save_fact(update, context, cid, cat, amount, comment):
    row = ROWS.get(cat)
    try:
        ws  = get_sheet()
        cur = get_val(ws, row, COL_FACT)
        new = cur + amount
        set_fact(ws, row, new)
        rest = get_val(ws, row, COL_REST)
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
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка записи.", reply_markup=MAIN_KB)
    context.user_data.clear()
 
# ── УДАЛИТЬ ───────────────────────────────────────────────────────────────
async def pick_cat_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
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
        set_fact(ws, row, new)
        await update.message.reply_text(
            f"🗑 *{cat}* -{amount:.0f} PLN\nТеперь: {new:.0f} PLN",
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    context.user_data.clear()
    return ConversationHandler.END
 
# ── ВВЕСТИ ОСТАТОК ────────────────────────────────────────────────────────
async def pick_cat_rest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
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
        set_fact(ws, row, fact)
        await update.message.reply_text(
            f"✅ *{cat}*\nПлан: {plan:.0f} PLN\nОстаток: {rest_input:.0f} PLN\nЗаписала факт: {fact:.0f} PLN",
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    context.user_data.clear()
    return ConversationHandler.END
 
# ── ДОХОД ─────────────────────────────────────────────────────────────────
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
        ws.update_cell(row, COL_FACT, amount)
        await update.message.reply_text(
            f"💰 *{name}* = {amount:.0f} PLN — записала!",
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    context.user_data.clear()
    return ConversationHandler.END
 
# ── ИНФОРМАЦИЯ ────────────────────────────────────────────────────────────
async def cmd_остатки(update: Update):
    try:
        ws    = get_sheet()
        lines = ["📊 *Остатки:*\n"]
        for cat, row in ROWS.items():
            rest = get_val(ws, row, COL_REST)
            if rest > 0:
                lines.append(f"{cat} — {rest:.0f} PLN")
        if len(lines) == 1:
            lines.append("Всё потрачено 🎉")
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
 
async def cmd_per_day(update: Update):
    try:
        ws   = get_sheet()
        left = days_left()
        lines = [f"💡 *На сегодня* (осталось {left} дн.)\n"]
        for row, label in [(28, "🍔 Еда"), (29, "🛍️ Досуг"), (39, "🎲 Прочее")]:
            rest = get_val(ws, row, COL_REST)
            pd   = rest / left
            lines.append(f"{'✅' if pd >= 0 else '⚠️'} {label} — *{pd:.0f} PLN/день*")
        # Общий остаток
        rest_total = get_val(ws, ROW_BALANCE, COL_REST)
        pd_total   = rest_total / left
        lines.append(f"{'✅' if pd_total >= 0 else '⚠️'} 💰 Остаток — *{pd_total:.0f} PLN/день*")
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
 
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
 
async def cmd_repeat(update: Update, cid: int):
    act = last_action.get(cid)
    if not act:
        await update.message.reply_text("Нет последней траты 🤷", reply_markup=MAIN_KB)
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
            parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
 
async def cmd_last5(update: Update, cid: int):
    hist = last_5.get(cid, [])
    if not hist:
        await update.message.reply_text("Пока нет трат 🤷", reply_markup=MAIN_KB)
        return
    lines = ["📋 *Последние траты:*\n"]
    for cat, amt in hist:
        lines.append(f"• {cat} — {amt:.0f} PLN")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)
 
# ── ВЕБ-СЕРВЕР ────────────────────────────────────────────────────────────
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
        },
        fallbacks=[CommandHandler("start", start)],
    )
 
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.run_polling(drop_pending_updates=True)
 
if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    main()
 
