import os
import json
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

# ── config from env vars ──────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
SHEET_ID  = os.environ["SHEET_ID"]
CREDS_JSON = os.environ["GOOGLE_CREDS"]

# ── Google Sheets ─────────────────────────────────────────────────────────
def get_sheet():
    creds_dict = json.loads(CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet("Month")

# ── category map: keywords → (row in Month sheet, column D) ──────────────
CATEGORIES = {
    "аренда":      (14, "Аренда"),
    "квартира":    (14, "Аренда"),
    "газ":         (15, "Газ"),
    "электричество":(16, "Электричество"),
    "свет":        (16, "Электричество"),
    "телефон поля":(17, "Телефон Поля"),
    "телефон женя":(18, "Телефон Женя"),
    "интернет":    (19, "Интернет"),
    "айфоны":      (20, "Айфоны"),
    "макбук":      (21, "Макбук"),
    "netflix":     (22, "Netflix"),
    "hbo":         (23, "HBO Max"),
    "spotify":     (24, "Spotify"),
    "youtube":     (25, "YouTube"),
    "chatgpt":     (26, "ChatGPT"),
    "claude":      (27, "Claude"),
    "telegram поля":(28, "Telegram Поля"),
    "telegram женя":(29, "Telegram Женя"),
    "icloud поля": (30, "iCloud Поля"),
    "icloud женя": (31, "iCloud Женя"),
    "larq":        (32, "LARQ"),
    "uberone":     (33, "UberOne"),
    "dazcam":      (34, "DazCam"),
    "еда":         (35, "Еда"),
    "продукты":    (35, "Еда"),
    "досуг":       (36, "Досуг"),
    "ресторан":    (36, "Досуг"),
    "ресторанчик": (36, "Досуг"),
    "кафе":        (36, "Досуг"),
    "псибуфет":    (37, "PsiBufet"),
    "psibufet":    (37, "PsiBufet"),
    "танго":       (38, "Вкусняшки Танго"),
    "вкусняшки":   (38, "Вкусняшки Танго"),
    "массаж женя": (39, "Массаж Женя"),
    "массаж поля": (40, "Массаж Поля"),
    "массаж":      (40, "Массаж Поля"),
    "бокс":        (41, "Бокс"),
    "теннис":      (42, "Теннис"),
    "зал":         (43, "Зал"),
    "валера":      (44, "Тренер Валера"),
    "транспорт":   (45, "Транспорт"),
    "абонемент":   (45, "Транспорт"),
    "праздники":   (46, "Праздники"),
    "подарок":     (46, "Праздники"),
    "отпуск":      (47, "Отпуск"),
    "растения":    (48, "Растения"),
    "цветы":       (48, "Растения"),
    "дом":         (49, "Покупки для дома"),
    "красивая жена":(50, "Красивая жена"),
    "процедуры":   (50, "Красивая жена"),
    "шмотки":      (50, "Красивая жена"),
    "косметика":   (50, "Красивая жена"),
    "сауна":       (51, "Сауна"),
    "покемоны":    (52, "Покемоны"),
    "карточки":    (52, "Покемоны"),
    "инвестиции":  (53, "Инвестиции"),
}

def parse_message(text: str):
    """Parse messages like '320 еда', 'потратила 500 на досуг', 'сауна 540'"""
    text = text.lower().strip()
    
    # find amount — first or last number
    import re
    numbers = re.findall(r'\d+(?:[.,]\d+)?', text)
    if not numbers:
        return None, None, None
    
    amount = float(numbers[0].replace(',', '.'))
    
    # find category
    found_row = None
    found_name = None
    for keyword, (row, name) in CATEGORIES.items():
        if keyword in text:
            found_row = row
            found_name = name
            break
    
    return amount, found_row, found_name

def get_current_value(ws, row):
    """Get current value in column D"""
    try:
        val = ws.cell(row, 4).value
        if val and val != '-':
            return float(str(val).replace(',', '.').replace(' ', ''))
    except:
        pass
    return 0.0

# ── handlers ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Привет! Я бот семейного бюджета.\n\n"
        "Просто напиши мне трату, например:\n"
        "• *320 еда*\n"
        "• *потратила 500 на досуг*\n"
        "• *сауна 540*\n"
        "• *1200 красивая жена*\n\n"
        "Я сам запишу в таблицу 📊\n\n"
        "Команды:\n"
        "/status — остатки по категориям\n"
        "/help — помощь"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    amount, row, cat_name = parse_message(text)
    
    if not amount:
        await update.message.reply_text(
            "🤔 Не нашла сумму. Напиши например: *320 еда*",
            parse_mode="Markdown"
        )
        return
    
    if not row:
        await update.message.reply_text(
            "🤔 Не поняла категорию. Попробуй написать точнее, например:\n"
            "*320 еда*, *500 досуг*, *540 сауна*",
            parse_mode="Markdown"
        )
        return
    
    try:
        ws = get_sheet()
        current = get_current_value(ws, row)
        new_val = current + amount
        ws.update_cell(row, 4, new_val)
        
        # get plan value for comparison
        plan = ws.cell(row, 3).value
        try:
            plan_val = float(str(plan).replace(',', '.').replace(' ', ''))
            rest = plan_val - new_val
            rest_emoji = "✅" if rest >= 0 else "⚠️"
            reply = (
                f"✍️ Записала: *{cat_name}* +{amount:.0f} PLN\n"
                f"Итого: {new_val:.0f} / {plan_val:.0f} PLN\n"
                f"{rest_emoji} Остаток: {rest:.0f} PLN"
            )
        except:
            reply = f"✍️ Записала: *{cat_name}* +{amount:.0f} PLN\nИтого: {new_val:.0f} PLN"
        
        await update.message.reply_text(reply, parse_mode="Markdown")
    
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка записи в таблицу. Попробуй ещё раз.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ws = get_sheet()
        lines = ["📊 *Остатки по категориям:*\n"]
        
        key_rows = [
            (35, "🍔 Еда"),
            (36, "🛍️ Досуг"),
            (14, "🏠 Аренда"),
            (37, "🐕 PsiBufet"),
            (50, "💄 Красивая жена"),
            (51, "🧖 Сауна"),
        ]
        
        for row, name in key_rows:
            plan = ws.cell(row, 3).value
            fact = ws.cell(row, 4).value
            try:
                p = float(str(plan).replace(',', '.').replace(' ', '')) if plan else 0
                f = float(str(fact).replace(',', '.').replace(' ', '')) if fact else 0
                rest = p - f
                emoji = "✅" if rest >= 0 else "⚠️"
                lines.append(f"{emoji} {name}: {f:.0f}/{p:.0f} (ост. {rest:.0f})")
            except:
                lines.append(f"• {name}: данные недоступны")
        
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    
    except Exception as e:
        logging.error(f"Status error: {e}")
        await update.message.reply_text("❌ Не могу получить данные из таблицы.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Как писать траты:*\n\n"
        "Просто сумма + категория:\n"
        "• 320 еда\n"
        "• 500 досуг\n"
        "• 540 сауна\n"
        "• 1200 красивая жена\n"
        "• 221 псибуфет\n"
        "• 100 вкусняшки\n"
        "• 340 массаж женя\n"
        "• 680 массаж поля\n"
        "• 500 растения\n"
        "• 1000 отпуск\n\n"
        "Команды:\n"
        "/status — остатки\n"
        "/start — начало"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
