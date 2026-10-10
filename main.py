import os
import asyncio
import json
from datetime import datetime, timedelta, timezone
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# ====== Keep Alive for Render ======
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Coach Sanaa VIP Bot Live 24/7 - MONTHLY!"

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

# ====== Monthly System - جديد ======
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

# ====== Commands (نفس القديم) ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"ادفع {PRICE} عبر Ziina", url=ZIINA_LINK)], [InlineKeyboardButton("انا دفعت", callback_data="paid")]]
    await update.message.reply_text("اهلا بك في نادي Coach Sanaa VIP\nالاشتراك 50 AED شهريا\nاضغطي انا دفعت بعد الدفع\n\n✨ المدة 30 يوم + طرد تلقائي عند الانتهاء", reply_markup=InlineKeyboardMarkup(kb))

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
            # رابط الدخول صالح 24 ساعة
            expire_link = datetime.now(timezone.utc) + timedelta(hours=24)
            link = await context.bot.create_chat_invite_link(CHANNEL_ID, member_limit=1, expire_date=expire_link)
            invite = link.invite_link
            
            # ===== هنا الاضافة الشهرية فقط =====
            members = load_data()
            expiry_date = datetime.now(timezone.utc) + timedelta(days=30)
            members[str(uid)] = {
                "expiry": expiry_date.isoformat(),
                "joined_at": datetime.now(timezone.utc).isoformat()
            }
            save_data(members)
            # ===== نهاية الاضافة =====

            await context.bot.send_message(uid, f"تمت الموافقة! \n🔗 رابطك (صالح 24س): {invite}\n📅 اشتراكك ينتهي: {expiry_date.strftime('%Y-%m-%d')}")
            msg = f"🔔 تمت الموافقة بنجاح!\n✅ عطيت رابط لـ {uid}\n🔗 {invite}\n📅 ينتهي: {expiry_date.date()}\n\nسيطرد تلقائيا بعد 30 يوم"
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

# ====== فحص الانتهاء والطرد التلقائي - جديد ======
async def check_expiry(context: ContextTypes.DEFAULT_TYPE):
    members = load_data()
    now = datetime.now(timezone.utc)
    to_remove = []
    for uid_str, info in list(members.items()):
        try:
            expiry = datetime.fromisoformat(info["expiry"])
            if now >= expiry:
                try:
                    await context.bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=int(uid_str))
                    await context.bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=int(uid_str))
                    print(f"Kicked {uid_str} - expired")
                except Exception as e:
                    print(f"Failed kick {uid_str}: {e}")
                try:
                    await context.bot.send_message(chat_id=int(uid_str), text="❌ انتهى اشتراكك الشهري (30 يوم) وتمت ازالتك من القناة.\nلتجديد الاشتراك ادفعي مرة اخرى عبر /start")
                except:
                    pass
                to_remove.append(uid_str)
            elif (expiry - now).days == 3 and not info.get("reminded"):
                try:
                    await context.bot.send_message(chat_id=int(uid_str), text="⏰ تذكير: اشتراكك ينتهي بعد 3 أيام! جددي عبر /start")
                    members[uid_str]["reminded"] = True
                    save_data(members)
                except:
                    pass
        except Exception as e:
            print(f"Error check {uid_str}: {e}")
    for uid_str in to_remove:
        members.pop(uid_str, None)
    if to_remove:
        save_data(members)

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
    
    # تشغيل فحص الانتهاء كل ساعة
    application.job_queue.run_repeating(check_expiry, interval=3600, first=20)
    
    print("Bot is running MONTHLY MODE - 30 days + auto kick!")
    application.run_polling()

if __name__ == "__main__":
    main()
