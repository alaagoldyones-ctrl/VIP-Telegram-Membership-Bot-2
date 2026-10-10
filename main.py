from flask import Flask
from threading import Thread
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
app = Flask('')
@app.route('/')
def home():
    return "Bot Alive"
def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive():
    Thread(target=run).start()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_TOKEN_HERE")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is Live 24/7")
def main():
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.run_polling()
if __name__ == "__main__":
    main()
