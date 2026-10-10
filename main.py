import os
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from flask import Flask
from threading import Thread

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1004426259423"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DATA_FILE = "members.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

flask_app = Flask(__name__)
@flask_app.route("/")
def home():
    return "Bot is running monthly - old code same!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=8080)

# نفس رسالة البداية القديمة تماماً
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("اشترك شهري (30 يوم)", callback_data="subscribe")],
        [InlineKeyboardButton("تواصل مع الادارة", url="https://t.me/ziina_admin")]
    ]
    # نفس النص القديم
    await update.message.reply_text(
        "أهلاً بك في Ziina VIP 🔥\n\n"
        "✨ الاشتراك الشهري:\n"
        "• المدة: 30 يوم\n"
        "• دخول فوري بعد الدفع\n"
        "• تذكير قبل الانتهاء بـ 3 أيام\n"
        "• طرد تلقائي عند الانتهاء\n\n"
        "اضغط اشترك للبدء:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()

    if query.data == "subscribe":
        await query.message.reply_text(
            "💳 طريقة الدفع:\n\n"
            "حول المبلغ وابعت صورة الاثبات هنا\n"
            "المدة 30 يوم من لحظة القبول."
        )

    elif query.data.startswith("approve_"):
        user_id_str = query.data.split("_")[1]
        user_id = int(user_id_str)
        try:
            invite = await context.bot.create_chat_invite_link(chat_id=CHANNEL_ID, member_limit=1)
            expiry = datetime.now() + timedelta(days=30)
            
            data[user_id_str] = {
                "expiry": expiry.isoformat(),
                "joined_at": datetime.now().isoformat()
            }
            save_data(data)

            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ تم تأكيد اشتراكك!\n🔗 رابطك: {invite.invite_link}\n📅 ينتهي: {expiry.strftime('%Y-%m-%d')}"
            )
            await query.edit_message_text(f"✅ تم قبول {user_id_str} - ينتهي {expiry.date()}")
        except Exception as e:
            await query.edit_message_text(f"خطأ: {e}")

    elif query.data.startswith("reject_"):
        user_id_str = query.data.split("_")[1]
        try:
            await context.bot.send_message(chat_id=int(user_id_str), text="❌ تم رفض الاثبات.")
        except:
            pass
        await query.edit_message_text(f"❌ تم رفض {user_id_str}")

async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        return
    user = update.effective_user
    if user.id == ADMIN_ID:
        return

    keyboard = [
        [InlineKeyboardButton("✅ قبول", callback_data=f"approve_{user.id}"),
         InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user.id}")]
    ]
    
    caption = f"📩 اثبات دفع\n👤 {user.first_name}\n🆔 {user.id}\nالمدة: 30 يوم"
    
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=update.message.photo[-1].file_id,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await update.message.reply_text("✅ تم ارسال الاثبات للادارة!")

# هذا فقط يلي اضفناه للطرد
async def check_expiry(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    now = datetime.now()
    to_remove = []
    for uid, info in list(data.items()):
        try:
            expiry = datetime.fromisoformat(info["expiry"])
            if now >= expiry:
                try:
                    await context.bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=int(uid))
                    await context.bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=int(uid))
                except:
                    pass
                try:
                    await context.bot.send_message(chat_id=int(uid), text="❌ انتهى اشتراكك وتمت ازالتك.")
                except:
                    pass
                to_remove.append(uid)
            elif (expiry - now).days == 3 and not info.get("reminded"):
                try:
                    await context.bot.send_message(chat_id=int(uid), text="⏰ اشتراكك ينتهي بعد 3 أيام!")
                    data[uid]["reminded"] = True
                except:
                    pass
        except:
            pass
    for uid in to_remove:
        data.pop(uid, None)
    if to_remove:
        save_data(data)

def main():
    Thread(target=run_flask, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, proof_handler))
    app.job_queue.run_repeating(check_expiry, interval=3600, first=20)
    print(f"Bot running OLD + MONTHLY CHANNEL={CHANNEL_ID}")
    app.run_polling()

if __name__ == "__main__":
    main()
