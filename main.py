import os
import asyncio
from datetime import datetime, timedelta, timezone
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# ====== Keep Alive for Render ======
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Coach Sanaa VIP Bot Live 24/7!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ====== Config ======
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = -1004426259423
ADMIN_ID = 8520909970
ZIINA_LINK = "https://pay.ziina.com/Tecaanah/yn5Nj7FcV?source=app"
PRICE = "50 AED"
waiting_for_proof = set()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"ادفع {PRICE} عبر Ziina", url=ZIINA_LINK)], [InlineKeyboardButton("انا دفعت", callback_data="paid")]]
    await update.message.reply_text("اهلا بك في نادي Coach Sanaa VIP\nالاشتراك 50 AED شهريا\nاضغطي انا دفعت بعد الدفع", reply_markup=InlineKeyboardMarkup(kb))

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ايديك هو: {update.effective_user.id}")

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    user = q.from_user
    if data.startswith(("approve_", "reject_")) and user.id!= ADMIN_ID:
        await q.answer("هذا للادارة فقط", show_alert=True)
        return
    await q.answer("تم")
    if data == "paid":
        waiting_for_proof.add(user.id)
        try:
            await q.edit_message_caption("بعد الدفع ابعتي سكرين شوت للايصال هنا")
        except:
            try:
                await q.edit_message_text("بعد الدفع ابعتي سكرين شوت للايصال هنا")
            except:
                pass
    elif data.startswith("approve_"):
        uid = int(data.split("_", 1)[1])
        try:
            expire = datetime.now(timezone.utc) + timedelta(hours=24)
            link = await context.bot.create_chat_invite_link(CHANNEL_ID, member_limit=1, expire_date=expire)
            invite = link.invite_link
            await context.bot.send_message(uid, f"تمت الموافقة! رابطك: {invite}")
            msg = f"🔔🔔🔔 تمت الموافقة بنجاح!\n✅ عطيت رابط لـ {uid}\n🔗 {invite}\n\nهذه الرسالة تبقى بالمحادثة"
            try:
                await q.edit_message_caption(msg)
            except:
                try:
                    await q.edit_message_text(msg)
                except:
                    pass
            await context.bot.send_message(chat_id=q.message.chat_id, text=msg, disable_notification=False)
        except Exception as e:
            err = f"خطأ: {e}"
            try:
                await q.edit_message_caption(err)
            except:
                try:
                    await q.edit_message_text(err)
                except:
                    pass
            await context.bot.send_message(chat_id=q.message.chat_id, text=err)
    elif data.startswith("reject_"):
        uid = int(data.split("_", 1)[1])
        try:
            await context.bot.send_message(uid, "لم يتم التأكيد")
            txt = f"تم رفض {uid}"
            try:
                await q.edit_message_caption(txt)
            except:
                try:
                    await q.edit_message_text(txt)
                except:
                    pass
            await context.bot.send_message(chat_id=q.message.chat_id, text=txt)
        except Exception as e:
            await context.bot.send_message(chat_id=q.message.chat_id, text=f"خطأ رفض: {e}")

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    waiting_for_proof.discard(user.id)
    kb = [[InlineKeyboardButton("موافقة", callback_data=f"approve_{user.id}"), InlineKeyboardButton("رفض", callback_data=f"reject_{user.id}")]]
    await context.bot.send_photo(ADMIN_ID, photo=update.message.photo[-1].file_id, caption=f"طلب جديد من {user.id} @{user.username}", reply_markup=InlineKeyboardMarkup(kb))
    await update.message.reply_text("تم الاستلام")

def main():
    keep_alive()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    print("Bot is running FIXED MODE!")
    application.run_polling()

if __name__ == "__main__":
    main()
