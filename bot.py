import os
import json
import logging
import re
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
 
logging.basicConfig(level=logging.INFO)
 
BOT_TOKEN  = os.environ["BOT_TOKEN"]
SHEET_ID   = os.environ["SHEET_ID"]
CREDS_JSON = os.environ["GOOGLE_CREDS"]
 
def get_sheet():
    creds_dict = json.loads(CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).worksheet("Month")
 
CATEGORIES = {
    "аренда": (14, "Аренда"), "квартира": (14, "Аренда"),
    "газ": (15, "Газ"),
    "электричество": (16, "Электричество"), "свет": (16, "Электричество"),
    "телефон поля": (17, "Телефон Поля"),
    "телефон женя": (18, "Телефон Женя"),
    "интернет": (19, "Интернет"),
    "айфоны": (20, "Айфоны"),
    "макбук": (21, "Макбук"),
    "netflix": (22, "Netflix"),
    "hbo": (23, "HBO Max"),
    "spotify": (24, "Spotify"),
    "youtube": (25, "YouTube"),
    "chatgpt": (26, "ChatGPT"),
    "claude": (27, "Claude"),
    "telegram поля": (28, "Telegram Поля"),
    "telegram женя": (29, "Telegram Женя"),
    "icloud поля": (30, "iCloud Поля"),
    "icloud женя": (31, "iCloud Женя"),
    "larq": (32, "LARQ"),
    "uberone": (33, "UberOne"),
    "dazcam": (34, "DazCam"),
    "еда": (35, "Еда"), "продукты": (35, "Еда"),
    "досуг": (36, "Досуг"), "ресторан": (36, "Досуг"), "кафе": (36, "Досуг"),
    "псибуфет": (37, "PsiBufet"), "psibufet": (37, "PsiBufet"),
    "вкусняшки": (38, "Вкусняшки Танго"), "танго корм": (38, "Вкусняшки Танго"),
    "массаж женя": (39, "Массаж Женя"),
    "массаж поля": (40, "Массаж Поля"), "массаж": (40, "Массаж Поля"),
    "бокс": (41, "Бокс"),
    "теннис": (42, "Теннис"),
    "зал": (43, "Зал"),
    "валера": (44, "Тренер Валера"),
    "транспорт": (45, "Транспорт"), "абонемент": (45, "Транспорт"),
    "праздники": (46, "Праздники"), "подарок": (46, "Праздники"),
    "отпуск": (47, "Отпуск"),
    "растения": (48, "Растения"), "цветы": (48, "Растения"),
    "дом": (49, "Покупки для дома"),
    "красивая жена": (50, "Красивая жена"), "процедуры": (50, "Красивая жена"),
    "шмотки": (50, "Красивая жена"), "косметика": (50, "Красивая жена"),
    "сауна": (51, "Сауна"),
    "покемоны": (52, "Покемоны"), "карточки": (52, "Покемоны"),
    "инвестиции": (53, "Инвестиции"),
}
 
def parse_message(text):
    text = text.lower().strip()
    numbers = re.findall(r'\d+(?:[.,]\d+)?', text)
    if not numbers:
        return None, None, None
    amount = float(numbers[0].replace(',', '.'))
    for keyword, (row, name) in CATEGORIES.items():
        if keyword in text:
            return amount, row, name
    return amount, None, None
 
def get_current_value(ws, row):
    try:
        val = ws.cell(row, 4).value
        if val and str(val).strip() not in ('', '-'):
            return float(str(val).replace(',', '.').replace(' ', '').replace('\xa0', ''))
    except Exception:
        pass
    return 0.0
 
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот семейного бюджета.\n\n"
        "Пиши трату вот так:\n"
        "• *320 еда*\n• *500 досуг*\n• *540 сауна*\n• *1200 красивая жена*\n\n"
        "Команды:\n/status — остатки\n/help — все категории",
        parse_mode="Markdown"
    )
 
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount, row, cat_name = parse_message(update.message.text)
    if not amount:
        await update.message.reply_text("🤔 Не нашла сумму. Пример: *320 еда*", parse_mode="Markdown")
        return
    if not row:
        await update.message.reply_text("🤔 Не поняла категорию. Пример: *320 еда*, *500 досуг*", parse_mode="Markdown")
        return
    try:
        ws = get_sheet()
        current = get_current_value(ws, row)
        new_val = current + amount
        ws.update_cell(row, 4, new_val)
        try:
            plan_val = float(str(ws.cell(row, 3).value).replace(',', '.').replace(' ', ''))
            rest = plan_val - new_val
            emoji = "✅" if rest >= 0 else "⚠️"
            reply = f"✍️ *{cat_name}* +{amount:.0f} PLN\nИтого: {new_val:.0f} / {plan_val:.0f}\n{emoji} Остаток: {rest:.0f} PLN"
        except Exception:
            reply = f"✍️ *{cat_name}* +{amount:.0f} PLN\nИтого: {new_val:.0f} PLN"
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Ошибка записи. Попробуй ещё раз.")
 
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ws = get_sheet()
        lines = ["📊 *Остатки:*\n"]
        for row, name in [(35,"🍔 Еда"),(36,"🛍️ Досуг"),(50,"💄 Красивая жена"),(51,"🧖 Сауна"),(47,"✈️ Отпуск"),(48,"🌿 Растения")]:
            try:
                p = float(str(ws.cell(row,3).value).replace(',','.').replace(' ',''))
                f = get_current_value(ws, row)
                rest = p - f
                lines.append(f"{'✅' if rest>=0 else '⚠️'} {name}: {f:.0f}/{p:.0f} (ост. {rest:.0f})")
            except Exception:
                lines.append(f"• {name}: нет данных")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ Не могу получить данные.")
 
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Категории:*\n\nеда, досуг, аренда, газ, свет, интернет, "
        "айфоны, макбук, псибуфет, вкусняшки, массаж поля, массаж женя, "
        "бокс, теннис, зал, валера, транспорт, праздники, отпуск, "
        "растения, дом, красивая жена, сауна, покемоны, инвестиции",
        parse_mode="Markdown"
    )
 
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)
 
if __name__ == "__main__":
    main()
 
