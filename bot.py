import os, json, logging, re, calendar
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           filters, ContextTypes, ConversationHandler,
                           JobQueue)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

BOT_TOKEN  = os.environ["BOT_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = os.environ["GOOGLE_CREDS"]
CHAT_ID    = os.environ.get("CHAT_ID", "")  # для уведомлений — заполним позже

# ── states ────────────────────────────────────────────────────────────────
(WAITING_CAT_ADD, WAITING_AMOUNT_ADD,
 WAITING_CAT_DEL, WAITING_AMOUNT_DEL,
 WAITING_INCOME_WHO, WAITING_INCOME_AMT) = range(6)

# ── keyboards ─────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup([
    ["➕ Добавить трату", "➖ Удалить трату"],
    ["📊 Остатки",        "💡 На сегодня"],
    ["💰 Внести доход",   "📅 Итого за месяц"],
    ["🔁 Повторить",      "📋 Последние траты"],
], resize_keyboard=True)

CANCEL_KB = ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)

CATS_KB = ReplyKeyboardMarkup([
    ["🏠 Аренда",    "🏠 Газ",         "🏠 Свет"],
    ["🍔 Еда",       "🛍️ Досуг",       "🧖 Сауна"],
    ["💆 Массаж Поля","💆 Массаж Женя", "💄 Красивая жена"],
    ["🏋️ Бокс",      "🏋️ Теннис",      "🏋️ Зал"],
    ["🏋️ Тренер Валера","🚌 Транспорт", "🌿 Растения"],
    ["🐕 PsiBufet",  "🐕 Вкусняшки",   "🛋️ Дом"],
    ["🎉 Праздники", "✈️ Отпуск",       "🃏 Покемоны"],
    ["💳 Айфоны",    "💳 Макбук",       "💰 Инвестиции"],
    ["📺 Подписки",  "❌ Отмена"],
], resize_keyboard=True)

INCOME_KB = ReplyKeyboardMarkup([
    ["👨 Женя зп", "👩 Поля зп"],
    ["💵 Поля кэш (USD)", "❌ Отмена"],
], resize_keyboard=True)

# ── data ──────────────────────────────────────────────────────────────────
ROWS = {
    "🏠 Аренда": 14, "🏠 Газ": 15, "🏠 Свет": 16,
    "📱 Телефон Поля": 17, "📱 Телефон Женя": 18,
    "🌐 Интернет": 19, "💳 Айфоны": 20, "💳 Макбук": 21,
    "📺 Netflix": 22, "📺 HBO Max": 23, "📺 Spotify": 24,
    "📺 YouTube": 25, "📺 ChatGPT": 26, "📺 Claude": 27,
    "📺 Telegram Поля": 28, "📺 Telegram Женя": 29,
    "📺 iCloud Поля": 30, "📺 iCloud Женя": 31,
    "📺 LARQ": 32, "📺 UberOne": 33, "📺 DazCam": 34,
    "🍔 Еда": 35, "🛍️ Досуг": 36,
    "🐕 PsiBufet": 37, "🐕 Вкусняшки": 38,
    "💆 Массаж Женя": 39, "💆 Массаж Поля": 40,
    "🏋️ Бокс": 41, "🏋️ Теннис": 42, "🏋️ Зал": 43,
    "🏋️ Тренер Валера": 44, "🚌 Транспорт": 45,
    "🎉 Праздники": 46, "✈️ Отпуск": 47,
    "🌿 Растения": 48, "🛋️ Дом": 49,
    "💄 Красивая жена": 50, "🧖 Сауна": 51,
    "🃏 Покемоны": 52, "💰 Инвестиции": 53,
}

# кнопки → ключ в ROWS
BTN_MAP = {
    "🏠 Аренда": "🏠 Аренда", "🏠 Газ": "🏠 Газ", "🏠 Свет": "🏠 Свет",
    "🍔 Еда": "🍔 Еда", "🛍️ Досуг": "🛍️ Досуг", "🧖 Сауна": "🧖 Сауна",
    "💆 Массаж Поля": "💆 Массаж Поля", "💆 Массаж Женя": "💆 Массаж Женя",
    "💄 Красивая жена": "💄 Красивая жена",
    "🏋️ Бокс": "🏋️ Бокс", "🏋️ Теннис": "🏋️ Теннис", "🏋️ Зал": "🏋️ Зал",
    "🏋️ Тренер Валера": "🏋️ Тренер Валера", "🚌 Транспорт": "🚌 Транспорт",
    "🌿 Растения": "🌿 Растения", "🐕 PsiBufet": "🐕 PsiBufet",
    "🐕 Вкусняшки": "🐕 Вкусняшки", "🛋️ Дом": "🛋️ Дом",
    "🎉 Праздники": "🎉 Праздники", "✈️ Отпуск": "✈️ Отпуск",
    "🃏 Покемоны": "🃏 Покемоны", "💳 Айфоны": "💳 Айфоны",
    "💳 Макбук": "💳 Макбук", "💰 Инвестиции": "💰 Инвестиции",
    "📺 Подписки": "📺 Netflix",  # открывает подписки отдельно — TODO
}

WARN_ROWS = [35, 36, 50, 51]  # еда, досуг, красивая жена, сауна

last_action = {}  # chat_id → (cat_key, amount)
last_5 = {}       # chat_id → [(cat, amount), ...]

# ── sheets ────────────────────────────────────────────────────────────────
def get_sheet():
    d = json.loads(CREDS_JSON)
    creds = Credentials.from_service_account_info(
        d, scopes=["https://spreadsheets.google.com/feeds",
                   "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet("Month")

def get_val(ws, row, col=4):
    try:
        v = ws.cell(row, col).value
        if v and str(v).strip() not in ('', '-'):
            return float(str(v).replace(',','.').replace(' ','').replace('\xa0',''))
    except Exception:
        pass
    return 0.0

def days_left():
    t = datetime.now()
    return calendar.monthrange(t.year, t.month)[1] - t.day + 1

def check_warning(ws, row, cat):
    try:
        plan = get_val(ws, row, 3)
        fact = get_val(ws, row, 4)
        if plan > 0 and fact / plan >= 0.8:
            pct = int(fact / plan * 100)
            return f"⚠️ *{cat}* уже {pct}% от бюджета!"
    except Exception:
        pass
    return None

def month_grade(rest_fact, exp_plan):
    if rest_fact > 0:
        return "🏆 Молодцы! Уложились в бюджет!"
    elif rest_fact > -exp_plan * 0.05:
        return "😅 Почти! Небольшой перерасход."
    else:
        return "😬 Перерасход в этом месяце. Разберём?"

# ── /start ────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот бюджета семьи Мушат 💪\n\nЧто делаем?",
        reply_markup=MAIN_KB
    )
    return ConversationHandler.END

# ── main menu handler ─────────────────────────────────────────────────────
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
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

    if t == "📊 Остатки":
        await cmd_остатки(update)
        return ConversationHandler.END

    if t == "💡 На сегодня":
        await cmd_per_day(update)
        return ConversationHandler.END

    if t == "📅 Итого за месяц":
        await cmd_итого(update)
        return ConversationHandler.END

    if t == "🔁 Повторить":
        await cmd_repeat(update, context)
        return ConversationHandler.END

    if t == "📋 Последние траты":
        await cmd_last5(update, cid)
        return ConversationHandler.END

    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END

    return ConversationHandler.END

# ── ADD flow ──────────────────────────────────────────────────────────────
async def pick_cat_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    cat = BTN_MAP.get(t)
    if not cat:
        await update.message.reply_text("Нажми кнопку из списка 👇", reply_markup=CATS_KB)
        return WAITING_CAT_ADD
    context.user_data["cat"] = cat
    await update.message.reply_text(
        f"*{cat}* — сколько? (только цифра)",
        parse_mode="Markdown", reply_markup=CANCEL_KB
    )
    return WAITING_AMOUNT_ADD

async def enter_amount_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    cid = update.effective_chat.id
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    try:
        amount = float(t.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Введи только число, например *320*",
                                        parse_mode="Markdown", reply_markup=CANCEL_KB)
        return WAITING_AMOUNT_ADD

    cat = context.user_data.get("cat")
    row = ROWS.get(cat)
    if not row:
        await update.message.reply_text("Что-то пошло не так, попробуй снова.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    try:
        ws = get_sheet()
        cur = get_val(ws, row)
        new = cur + amount
        ws.update_cell(row, 4, new)

        plan = get_val(ws, row, 3)
        rest = plan - new
        flag = "✅" if rest >= 0 else "⚠️"
        msg = f"✍️ *{cat}* +{amount:.0f} PLN\n{flag} Остаток: {rest:.0f} PLN"

        # save last action
        last_action[cid] = (cat, amount)
        hist = last_5.get(cid, [])
        hist.insert(0, (cat, amount))
        last_5[cid] = hist[:5]

        # warning if >80%
        warn = check_warning(ws, row, cat)
        if warn:
            msg += f"\n\n{warn}"

        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка записи.", reply_markup=MAIN_KB)

    return ConversationHandler.END

# ── DELETE flow ───────────────────────────────────────────────────────────
async def pick_cat_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "❌ Отмена":
        await update.message.reply_text("Окей 👌", reply_markup=MAIN_KB)
        return ConversationHandler.END
    cat = BTN_MAP.get(t)
    if not cat:
        await update.message.reply_text("Нажми кнопку из списка 👇", reply_markup=CATS_KB)
        return WAITING_CAT_DEL
    context.user_data["cat"] = cat
    await update.message.reply_text(
        f"*{cat}* — сколько удалить?",
        parse_mode="Markdown", reply_markup=CANCEL_KB
    )
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
        ws = get_sheet()
        cur = get_val(ws, row)
        new = max(0.0, cur - amount)
        ws.update_cell(row, 4, new)
        await update.message.reply_text(
            f"🗑 *{cat}* -{amount:.0f} PLN\nТеперь: {new:.0f} PLN",
            parse_mode="Markdown", reply_markup=MAIN_KB
        )
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ── INCOME flow ───────────────────────────────────────────────────────────
INCOME_ROWS_MAP = {
    "👨 Женя зп": (7, "Женя зп"),
    "👩 Поля зп": (8, "Поля зп"),
    "💵 Поля кэш (USD)": (9, "Поля кэш"),
}

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
        f"*{info[1]}* — сколько?",
        parse_mode="Markdown", reply_markup=CANCEL_KB
    )
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
    if not row:
        await update.message.reply_text("Ошибка, попробуй снова.", reply_markup=MAIN_KB)
        return ConversationHandler.END
    try:
        ws = get_sheet()
        ws.update_cell(row, 4, amount)
        await update.message.reply_text(
            f"💰 *{name}* = {amount:.0f} PLN — записала!",
            parse_mode="Markdown", reply_markup=MAIN_KB
        )
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)
    return ConversationHandler.END

# ── info commands ─────────────────────────────────────────────────────────
async def cmd_остатки(update: Update):
    try:
        ws = get_sheet()
        lines = ["📊 *Остатки:*\n"]
        for cat, row in ROWS.items():
            plan = get_val(ws, row, 3)
            fact = get_val(ws, row, 4)
            rest = plan - fact
            if rest > 0:
                lines.append(f"{cat} — {rest:.0f} PLN")
        if len(lines) == 1:
            lines.append("Всё потрачено 🎉")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

async def cmd_per_day(update: Update):
    try:
        ws = get_sheet()
        left = days_left()
        lines = [f"💡 *На сегодня* (осталось {left} дн.)\n"]
        for row, label in [(35, "🍔 Еда"), (36, "🛍️ Досуг")]:
            plan = get_val(ws, row, 3)
            fact = get_val(ws, row, 4)
            rest = plan - fact
            pd = rest / left if left > 0 else 0
            lines.append(f"{'✅' if pd>=0 else '⚠️'} {label} — *{pd:.0f} PLN/день*")
        # остаток общий
        inc = get_val(ws, 10, 4)
        exp = get_val(ws, 53, 4)
        rest_total = inc - exp
        pd_total = rest_total / left if left > 0 else 0
        lines.append(f"{'✅' if pd_total>=0 else '⚠️'} 💰 Остаток — *{pd_total:.0f} PLN/день*")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

async def cmd_итого(update: Update):
    try:
        ws = get_sheet()
        inc_p = get_val(ws, 10, 3)
        inc_f = get_val(ws, 10, 4)
        exp_p = get_val(ws, 53, 3)
        exp_f = get_val(ws, 53, 4)
        rest_f = inc_f - exp_f
        grade = month_grade(rest_f, exp_p)
        msg = (f"📅 *Итого за месяц:*\n\n"
               f"💵 Доходы: {inc_f:.0f} / {inc_p:.0f} PLN\n"
               f"📤 Расходы: {exp_f:.0f} / {exp_p:.0f} PLN\n"
               f"💰 Остаток: *{rest_f:.0f} PLN*\n\n{grade}")
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

async def cmd_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    act = last_action.get(cid)
    if not act:
        await update.message.reply_text("Нет последней траты 🤷", reply_markup=MAIN_KB)
        return
    cat, amount = act
    row = ROWS.get(cat)
    try:
        ws = get_sheet()
        cur = get_val(ws, row)
        new = cur + amount
        ws.update_cell(row, 4, new)
        plan = get_val(ws, row, 3)
        rest = plan - new
        await update.message.reply_text(
            f"🔁 Повторила: *{cat}* +{amount:.0f} PLN\n✅ Остаток: {rest:.0f} PLN",
            parse_mode="Markdown", reply_markup=MAIN_KB
        )
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка.", reply_markup=MAIN_KB)

async def cmd_last5(update: Update, cid: int):
    hist = last_5.get(cid, [])
    if not hist:
        await update.message.reply_text("Пока нет трат в этой сессии 🤷", reply_markup=MAIN_KB)
        return
    lines = ["📋 *Последние траты:*\n"]
    for cat, amt in hist:
        lines.append(f"• {cat} — {amt:.0f} PLN")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KB)

# ── scheduled jobs ────────────────────────────────────────────────────────
async def job_new_month(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    now = datetime.now()
    if now.day == 1:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(f"🎉 *Новый месяц — {now.strftime('%B %Y')}!*\n\n"
                  "Не забудьте внести доходы:\n"
                  "💰 Внести доход → кнопка в меню"),
            parse_mode="Markdown"
        )

async def job_end_month(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    now = datetime.now()
    last_day = calendar.monthrange(now.year, now.month)[1]
    if now.day == last_day:
        try:
            ws = get_sheet()
            inc_f = get_val(ws, 10, 4)
            exp_f = get_val(ws, 53, 4)
            exp_p = get_val(ws, 53, 3)
            rest_f = inc_f - exp_f
            grade = month_grade(rest_f, exp_p)
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(f"📅 *Итоги месяца:*\n\n"
                      f"💵 Доходы: {inc_f:.0f} PLN\n"
                      f"📤 Расходы: {exp_f:.0f} PLN\n"
                      f"💰 Остаток: *{rest_f:.0f} PLN*\n\n{grade}\n\n"
                      "Не забудь скопировать данные в Архив! 🗂️"),
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(e)

# ── main ──────────────────────────────────────────────────────────────────
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
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    # daily jobs
    if CHAT_ID:
        app.job_queue.run_daily(job_new_month, time=datetime.strptime("09:00", "%H:%M").time())
        app.job_queue.run_daily(job_end_month, time=datetime.strptime("20:00", "%H:%M").time())

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    
    class PingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass
    
    t = threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8080), PingHandler).serve_forever(), daemon=True)
    t.start()
    main()
