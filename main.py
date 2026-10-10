import os
import asyncio
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= Flask Keep Alive for Render =================
app = Flask(__name__)

@app.route('/')
def home():
    return "VIP Bot is Live 24/7!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ================= Bot Config =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 123456789  # <--- حط الـ ID تبعك هون من @userinfobot

# ================= Bot Handlers =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💎 الاشتراك VIP", callback_data='vip')],
        [InlineKeyboardButton("📞 تواصل مع الكوتش", callback_data='contact')],
        [InlineKeyboardButton("ℹ️ حول البوت", callback_data='about')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "أهلا بيك ببوت الكوتش سناء! 💪\n\n"
        "✅ البوت شغال 24/7\n"
        "اختر من القائمة:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'vip':
        await query.edit_message_text(
            "💎 **الاشتراك VIP**\n\n"
            "1 شهر - 50$ \n"
            "3 شهور - 120$ \n"
            "للتفعيل تواصل مع الإدارة",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='back')]])
        )
    elif query.data == 'contact':
        await query.edit_message_text("📞 تواصل مع الكوتش: @coach_snaa")
    elif query.data == 'about':
        await query.edit_message_text("🤖 بوت VIP - يعمل 24/7 على Render")
    elif query.data == 'back':
        # رجع للقائمة الرئيسية
        keyboard = [
            [InlineKeyboardButton("💎 الاشتراك VIP", callback_data='vip')],
            [InlineKeyboardButton("📞 تواصل مع الكوتش", callback_data='contact')],
            [InlineKeyboardButton("ℹ️ حول البوت", callback_data='about')]
        ]
        await query.edit_message_text(
            "أهلا بيك ببوت الكوتش سناء! 💪\nاختر من القائمة:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

def main():
    keep_alive()
    # Fix for event loop on Python 3.11+
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot Started! Polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
