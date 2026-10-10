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

# Flask keep-alive
flask_app = Flask(__name__)
@flask_app.route("/")
def home():
    return "VIP Monthly Bot is LIVE - 30 Days System"

def run_flask():
    flask_app.run(host="0.0.0.0", port=8080)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💳 اشترك شهري (30 يوم)", callback_data="subscribe")],
        [InlineKeyboardButton("📞 تواصل مع الادارة", url="https://t.me/ziina_admin")]
    ]
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

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data = load_data()
    msg = f"📊 المشتركين الحاليين: {len(data)}\n\n"
    for uid, info in data.items():
        expiry = info.get('expiry','?')
        msg += f"• {uid} -> ينتهي {expiry[:10]}\n"
    await update.message.reply_text(msg or "لا يوجد مشتركين")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()

    if query.data == "subscribe":
        await query.message.reply_text(
            "💳 طريقة الدفع الشهرية:\n\n"
            "1️⃣ حول المبلغ على الحساب البنكي\n"
            "2️⃣ ابعت صورة الاثبات هنا (كصورة مو كملف)\n\n"
            "⏳ بعد التأكيد من الادارة رح توصلك رابط القناة الخاصة فوراً ✅\n"
            "المدة 30 يوم من لحظة القبول."
        )

    elif query.data.startswith("approve_"):
        user_id_str = query.data.split("_")[1]
        user_id = int(user_id_str)
        try:
            # Create one-time invite link
            invite = await context.bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                member_limit=1,
                name=f"VIP_{user_id_str}_{datetime.now().strftime('%d%m')}"
            )
            expiry = datetime.now() + timedelta(days=30)
            
            # Save
            data[user_id_str] = {
                "expiry": expiry.isoformat(),
                "joined_at": datetime.now().isoformat(),
                "invite_link": invite.invite_link
            }
            save_data(data)

            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ تم تأكيد اشتراكك الشهري! 🎉\n\n"
                     f"🔗 رابط قناتك الخاصة (صالح لشخص واحد فقط):\n{invite.invite_link}\n\n"
                     f"📅 الاشتراك ينتهي: {expiry.strftime('%Y-%m-%d')}\n"
                     f"⏰ رح نذكرك قبل 3 أيام من الانتهاء\n\n"
                     f"استمتع بالمحتوى 🔥"
            )
            await query.edit_message_text(f"✅ تم قبول {user_id_str}\nرابط: {invite.invite_link}\nينتهي: {expiry.date()}")
            
        except Exception as e:
            await query.edit_message_text(f"❌ خطأ: {e}\nتأكد البوت ادمن بالقناة وعندو صلاحية Invite + Ban")

    elif query.data.startswith("reject_"):
        user_id_str = query.data.split("_")[1]
        try:
            await context.bot.send_message(chat_id=int(user_id_str), text="❌ تم رفض اثبات الدفع. تواصل مع الادارة للتأكد من التحويل.")
        except:
            pass
        await query.edit_message_text(f"❌ تم رفض {user_id_str}")

async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        return
    
    user = update.effective_user
    
    # Don't process admin's own photos
    if user.id == ADMIN_ID:
        return

    keyboard = [
        [
            InlineKeyboardButton("✅ قبول وارسال الرابط", callback_data=f"approve_{user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user.id}")
        ]
    ]
    
    caption = (
        f"📩 اثبات دفع جديد (شهري)\n"
        f"👤 الاسم: {user.first_name}\n"
        f"🔗 اليوزر: @{user.username or 'بدون'}\n"
        f"🆔 ID: {user.id}\n"
        f"⏰ التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=update.message.photo[-1].file_id,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text("✅ تم ارسال الاثبات للادارة بنجاح!\nانتظر التأكيد خلال دقائق ⏳")
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ بالارسال: {e}")

async def check_expiry(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    now = datetime.now()
    to_remove = []

    for user_id_str, info in list(data.items()):
        try:
            expiry = datetime.fromisoformat(info["expiry"])
            days_left = (expiry - now).days
            
            # Reminder exactly 3 days before
            if days_left == 3:
                # Check if we already reminded today (simple flag)
                if not info.get("reminded_3d"):
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_id_str),
                            text="⏰ تذكير VIP:\n\nاشتراكك ينتهي بعد 3 أيام!\nجدد الآن لتستمر بدون انقطاع 🔥\nتواصل مع الادارة للتجديد."
                        )
                        data[user_id_str]["reminded_3d"] = True
                        save_data(data)
                    except:
                        pass
            
            # Expired
            if now >= expiry:
                try:
                    # Ban then unban to kick
                    await context.bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=int(user_id_str))
                    await context.bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=int(user_id_str))
                    print(f"Kicked expired {user_id_str}")
                except Exception as e:
                    print(f"Kick failed {user_id_str}: {e}")
                
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id_str),
                        text="❌ انتهى اشتراكك الشهري وتمت ازالتك من القناة.\n\nجدد اشتراكك للعودة والاستمتاع بالمحتوى 🔥"
                    )
                except:
                    pass
                to_remove.append(user_id_str)
        except Exception as e:
            print(f"check error {user_id_str}: {e}")

    for uid in to_remove:
        data.pop(uid, None)
    if to_remove:
        save_data(data)

def main():
    Thread(target=run_flask, daemon=True).start()
    
    if not BOT_TOKEN:
        print("BOT_TOKEN missing!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("members", help_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, proof_handler))
    
    # Check every hour
    app.job_queue.run_repeating(check_expiry, interval=3600, first=20)
    
    print(f"Starting monthly bot... CHANNEL={CHANNEL_ID} ADMIN={ADMIN_ID}")
    app.run_polling()

if __name__ == "__main__":
    main()
