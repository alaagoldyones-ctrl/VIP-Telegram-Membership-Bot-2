import os
import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from threading import Thread

import httpx
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ====== Logging ======
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
# مهم: بدون هالسطرين، المكتبة بتكتب التوكن وروابط الطلبات بسجلات Render
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
log = logging.getLogger("vipbot")

# ====== Keep Alive for Render ======
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "Coach Sanaa VIP Bot is running"


@flask_app.route("/health")
def health():
    return "ok"


def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# ====== Config ======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
# اختياري: ايميل او معرف تلغرام للدعم (يتحط بمتغيرات Render)
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "").strip()

CHANNEL_ID = -1004426259423
ADMIN_ID = 8520909970
ZIINA_LINK = "https://pay.ziina.com/Tecaanah/yn5Nj7FcV?source=app"
PRICE = "50 AED"
SUBSCRIPTION_DAYS = 30
CHECK_INTERVAL_SECONDS = 3600  # فحص الانتهاء والتذكير كل ساعة

# حالة مؤقتة بالذاكرة (مو مهمة اذا انمسحت)
waiting_for_proof = set()   # مشتركين ضغطوا "انا دفعت" وبانتظار الايصال
approving = set()           # طلبات موافقة قيد المعالجة (لمنع الضغط المزدوج)
kick_alerted = set()        # لتجنب تكرار تنبيه الادمن عن فشل الطرد
state = {"db_alerted": False}


# ====== Helpers ======
def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(value):
    """تحويل نص التاريخ القادم من القاعدة الى datetime بشكل آمن."""
    s = str(value).replace("Z", "+00:00")
    m = re.match(r"^(.*?T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", s)
    if not m:
        return datetime.fromisoformat(s)
    base, frac, tz = m.groups()
    frac = (frac or ".000000")[:7].ljust(7, "0")
    return datetime.fromisoformat(base + frac + tz)


def fmt_date(dt):
    return dt.strftime("%Y-%m-%d")


def support_line():
    return f"\n\n📩 للدعم: {SUPPORT_CONTACT}" if SUPPORT_CONTACT else ""


def approval_keyboard(uid):
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("موافقة", callback_data=f"approve_{uid}"),
            InlineKeyboardButton("رفض", callback_data=f"reject_{uid}"),
        ]]
    )


async def notify_admin(bot, text):
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception as e:
        log.warning("notify_admin failed: %s", e)


async def edit_admin_note(q, text, keyboard=None):
    """تعديل رسالة الطلب عند الادمن (الرسالة فيها صورة/ملف فنعدل الكابشن)."""
    try:
        await q.edit_message_caption(caption=text, reply_markup=keyboard)
    except Exception:
        try:
            await q.edit_message_text(text=text, reply_markup=keyboard)
        except Exception as e:
            log.warning("edit_admin_note failed: %s", e)


# ====== Database (Supabase REST) ======
async def db_request(method, table, params=None, json_body=None, prefer=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.request(
            method, url, params=params, json=json_body, headers=headers
        )
    if r.status_code >= 400:
        raise RuntimeError(f"DB {method} {table} -> {r.status_code}: {r.text[:200]}")
    return r.json() if r.text else None


async def db_get_member(uid):
    rows = await db_request(
        "GET", "members", params={"telegram_id": f"eq.{uid}", "select": "*"}
    )
    return rows[0] if rows else None


async def db_list_members():
    return await db_request("GET", "members", params={"select": "*"}) or []


async def db_upsert_member(row):
    await db_request(
        "POST",
        "members",
        params={"on_conflict": "telegram_id"},
        json_body=row,
        prefer="resolution=merge-duplicates,return=minimal",
    )


async def db_update_member(uid, fields):
    await db_request(
        "PATCH",
        "members",
        params={"telegram_id": f"eq.{uid}"},
        json_body=fields,
        prefer="return=minimal",
    )


async def db_delete_member(uid):
    await db_request(
        "DELETE",
        "members",
        params={"telegram_id": f"eq.{uid}"},
        prefer="return=minimal",
    )


# ====== Commands ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton(f"ادفع {PRICE} عبر Ziina", url=ZIINA_LINK)],
        [InlineKeyboardButton("انا دفعت", callback_data="paid")],
    ]
    await update.message.reply_text(
        "اهلا بك في نادي Coach Sanaa VIP\n"
        "الاشتراك 50 AED شهريا\n"
        "اضغطي انا دفعت بعد الدفع\n\n"
        "✨ المدة 30 يوم + طرد تلقائي عند الانتهاء\n"
        "للاطلاع على حالة اشتراكك: /status" + support_line(),
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "الاوامر:\n/start - الاشتراك والدفع\n/status - حالة اشتراكك\n/myid - رقم حسابك"
        + support_line()
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ايديك هو: {update.effective_user.id}")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        m = await db_get_member(uid)
    except Exception as e:
        log.error("status db error: %s", e)
        await update.message.reply_text("تعذر التحقق حاليا، حاولي بعد قليل." + support_line())
        return
    if not m:
        await update.message.reply_text("ما في اشتراك فعال حاليا. للاشتراك اضغطي /start")
        return
    try:
        expiry = parse_ts(m["expiry"])
        await update.message.reply_text(f"اشتراكك فعال لغاية: {fmt_date(expiry)}")
    except Exception:
        await update.message.reply_text("تعذر قراءة تاريخ الاشتراك." + support_line())


# ====== Buttons ======
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    user = q.from_user

    if data.startswith(("approve_", "reject_")) and user.id != ADMIN_ID:
        await q.answer("هذا للادارة فقط", show_alert=True)
        return
    await q.answer()

    if data == "paid":
        waiting_for_proof.add(user.id)
        await context.bot.send_message(
            user.id, "بعد الدفع ابعتي سكرين شوت (صورة) او ملف PDF للايصال هنا"
        )

    elif data.startswith("approve_"):
        uid = int(data.split("_", 1)[1])
        if uid in approving:
            return
        approving.add(uid)
        try:
            await handle_approve(uid, q, context)
        finally:
            approving.discard(uid)

    elif data.startswith("reject_"):
        uid = int(data.split("_", 1)[1])
        await edit_admin_note(q, f"تم رفض {uid}")
        try:
            await context.bot.send_message(
                uid,
                "لم يتم تأكيد الدفع. اذا كنت قد دفعت، اضغطي /start ثم انا دفعت "
                "وابعتي الايصال مرة اخرى." + support_line(),
            )
        except Exception as e:
            log.warning("reject notify failed: %s", e)


async def handle_approve(uid, q, context):
    await edit_admin_note(q, f"⏳ جاري معالجة الموافقة لـ {uid} ...")

    # 1) قراءة الحالة الحالية
    try:
        member = await db_get_member(uid)
    except Exception as e:
        await edit_admin_note(
            q, f"❌ فشل الاتصال بقاعدة البيانات. لم يتم اي شي.\n{e}", approval_keyboard(uid)
        )
        return

    now = now_utc()
    base = now
    if member:
        try:
            old_expiry = parse_ts(member["expiry"])
            if old_expiry > now:
                base = old_expiry  # تجديد مبكر: نضيف المدة فوق التاريخ الحالي
        except Exception:
            pass
    new_expiry = base + timedelta(days=SUBSCRIPTION_DAYS)

    # 2) رابط الدخول (اذا مو عضو اصلا)
    invite = None
    already_in = False
    try:
        cm = await context.bot.get_chat_member(CHANNEL_ID, uid)
        already_in = cm.status in ("member", "administrator", "creator")
    except Exception as e:
        log.info("get_chat_member failed for %s: %s", uid, e)

    if not already_in:
        try:
            link = await context.bot.create_chat_invite_link(
                CHANNEL_ID, member_limit=1, expire_date=now + timedelta(hours=24)
            )
            invite = link.invite_link
        except Exception as e:
            await edit_admin_note(
                q,
                "❌ تعذر انشاء رابط الدخول (تاكد ان البوت مشرف بالقناة وعنده صلاحية "
                f"دعوة المستخدمين). لم يتم حفظ شي.\n{e}",
                approval_keyboard(uid),
            )
            return

    # 3) حفظ الاشتراك بالقاعدة (قبل ارسال اي رابط)
    try:
        await db_upsert_member(
            {
                "telegram_id": uid,
                "expiry": new_expiry.isoformat(),
                "reminded_7": False,
                "reminded_3": False,
                "reminded_1": False,
            }
        )
    except Exception as e:
        await edit_admin_note(
            q,
            f"❌ فشل حفظ الاشتراك بالقاعدة. لم يتم ارسال رابط للمشترك.\n{e}",
            approval_keyboard(uid),
        )
        return

    # 4) اشعار المشترك والادمن
    date_str = fmt_date(new_expiry)
    if invite:
        user_text = (
            "تمت الموافقة! ✅\n"
            f"🔗 رابط الدخول (صالح 24 ساعة ويعمل مرة وحدة):\n{invite}\n"
            f"📅 اشتراكك ينتهي: {date_str}"
        )
    else:
        user_text = f"تم تجديد اشتراكك ✅\n📅 ينتهي: {date_str}"

    admin_text = f"✅ تمت الموافقة على {uid}\n📅 ينتهي: {date_str}"
    if invite:
        admin_text += f"\n🔗 {invite}"

    try:
        await context.bot.send_message(uid, user_text)
    except Exception as e:
        log.error("approve notify failed for %s: %s", uid, e)
        admin_text += f"\n⚠️ تعذر ارسال الرسالة للمشترك ({e}). ارسل له الرابط يدويا."
    await edit_admin_note(q, admin_text)


# ====== Receipts ======
async def on_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if user.id not in waiting_for_proof:
        await msg.reply_text(
            "اضغطي /start ثم زر انا دفعت، وبعدها ابعتي الايصال." + support_line()
        )
        return

    caption = (
        "طلب جديد\n"
        f"المعرف: {user.id}\n"
        f"المستخدم: @{user.username or '-'}\n"
        f"الاسم: {user.full_name}"
    )
    try:
        if msg.photo:
            await context.bot.send_photo(
                ADMIN_ID,
                photo=msg.photo[-1].file_id,
                caption=caption,
                reply_markup=approval_keyboard(user.id),
            )
        else:
            await context.bot.send_document(
                ADMIN_ID,
                document=msg.document.file_id,
                caption=caption,
                reply_markup=approval_keyboard(user.id),
            )
    except Exception as e:
        log.error("forward receipt failed: %s", e)
        await msg.reply_text("تعذر ارسال الطلب حاليا، حاولي بعد قليل." + support_line())
        return

    waiting_for_proof.discard(user.id)
    await msg.reply_text("تم الاستلام، رح نراجع الدفع ونأكد اشتراكك قريبا.")


# ====== فحص الانتهاء والتذكير ======
async def expire_member(context, uid):
    try:
        await context.bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=uid)
        await context.bot.unban_chat_member(
            chat_id=CHANNEL_ID, user_id=uid, only_if_banned=True
        )
    except Exception as e:
        log.error("kick failed for %s: %s", uid, e)
        if uid not in kick_alerted:
            kick_alerted.add(uid)
            await notify_admin(
                context.bot,
                f"⚠️ تعذر طرد {uid} بعد انتهاء اشتراكه. تاكد ان البوت مشرف وعنده "
                f"صلاحية حظر الاعضاء. رح يعيد المحاولة كل ساعة.\n{e}",
            )
        return  # نبقي السجل ونعيد المحاولة

    kick_alerted.discard(uid)
    try:
        await db_delete_member(uid)
    except Exception as e:
        log.error("delete member failed for %s: %s", uid, e)
        return

    try:
        await context.bot.send_message(
            uid,
            "❌ انتهى اشتراكك الشهري وتمت ازالتك من القناة.\n"
            "لتجديد الاشتراك ادفعي مرة اخرى عبر /start" + support_line(),
        )
    except Exception:
        pass
    await notify_admin(context.bot, f"🚪 تمت ازالة {uid} لانتهاء الاشتراك")


async def maybe_remind(context, member, expiry, now):
    uid = member["telegram_id"]
    remaining = expiry - now
    days_left = max(1, -(-int(remaining.total_seconds()) // 86400))
    date_str = fmt_date(expiry)

    if remaining <= timedelta(days=1):
        already = member.get("reminded_1")
        flags = {"reminded_7": True, "reminded_3": True, "reminded_1": True}
        text = (
            f"⚠️ آخر يوم: اشتراكك ينتهي خلال 24 ساعة ({date_str}) وبعدها تتم "
            "ازالتك من القناة.\nللتجديد اضغطي /start وادفعي."
        )
    elif remaining <= timedelta(days=3):
        already = member.get("reminded_3")
        flags = {"reminded_7": True, "reminded_3": True}
        text = (
            f"⏰ تذكير: اشتراكك ينتهي بتاريخ {date_str} (بعد {days_left} ايام تقريبا).\n"
            "للتجديد اضغطي /start وادفعي."
        )
    elif remaining <= timedelta(days=7):
        already = member.get("reminded_7")
        flags = {"reminded_7": True}
        text = (
            f"⏰ تذكير: اشتراكك ينتهي بتاريخ {date_str} (بعد {days_left} ايام تقريبا).\n"
            "للتجديد اضغطي /start وادفعي."
        )
    else:
        return

    if already:
        return

    try:
        await context.bot.send_message(uid, text)
    except NetworkError as e:
        log.warning("reminder network error for %s: %s", uid, e)
        return  # نعيد المحاولة بالفحص الجاي
    except Exception as e:
        log.warning("reminder not delivered to %s: %s", uid, e)
        # مثلا المشترك حاظر البوت: منعلّم انو انبعت حتى ما نكرر

    await db_update_member(uid, flags)


async def check_expiry(context: ContextTypes.DEFAULT_TYPE):
    try:
        members = await db_list_members()
    except Exception as e:
        log.error("check_expiry db error: %s", e)
        if not state["db_alerted"]:
            state["db_alerted"] = True
            await notify_admin(
                context.bot, f"⚠️ فشل قراءة قاعدة البيانات اثناء فحص الاشتراكات:\n{e}"
            )
        return
    state["db_alerted"] = False

    now = now_utc()
    for m in members:
        uid = m.get("telegram_id")
        try:
            expiry = parse_ts(m["expiry"])
            if now >= expiry:
                await expire_member(context, uid)
            else:
                await maybe_remind(context, m, expiry, now)
        except Exception as e:
            log.error("check error for %s: %s", uid, e)


# ====== Startup / errors ======
async def on_start(app: Application):
    try:
        members = await db_list_members()
        msg = f"✅ البوت اشتغل وقاعدة البيانات متصلة. عدد المشتركين الحاليين: {len(members)}"
    except Exception as e:
        msg = f"❌ البوت اشتغل لكن الاتصال بقاعدة البيانات فشل:\n{e}"
    await notify_admin(app.bot, msg)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error: %s", context.error)


def main():
    missing = [
        name
        for name, value in (
            ("BOT_TOKEN", BOT_TOKEN),
            ("SUPABASE_URL", SUPABASE_URL),
            ("SUPABASE_KEY", SUPABASE_KEY),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))

    keep_alive()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(on_start).build()

    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("start", start, filters=private))
    application.add_handler(CommandHandler("help", help_cmd, filters=private))
    application.add_handler(CommandHandler("myid", myid, filters=private))
    application.add_handler(CommandHandler("status", status, filters=private))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(
        MessageHandler(
            private & (filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF),
            on_receipt,
        )
    )
    application.add_error_handler(on_error)

    application.job_queue.run_repeating(
        check_expiry, interval=CHECK_INTERVAL_SECONDS, first=30
    )

    log.info("Bot is running - Supabase storage, monthly mode")
    application.run_polling()


if __name__ == "__main__":
    main()
