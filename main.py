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
ZIINA_API_KEY = os.environ.get("ZIINA_API_KEY", "").strip()
# وضع التجربة: "1" = دفعات تجريبية بدون خصم مبالغ (الافتراضي). للدفع الحقيقي حطي "0"
PAYMENT_TEST_MODE = os.environ.get("PAYMENT_TEST_MODE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# ايميل الدعم الفني (ممكن تغيره من متغيرات Render)
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "care@tecaanah.net").strip()

CHANNEL_ID = -1004426259423
ADMIN_ID = 8520909970
PRICE_AED = 50
SUBSCRIPTION_DAYS = 30
COMPLAINT_DAYS = 7  # مهلة مراسلة الدعم عند وجود مشكلة
CHECK_INTERVAL_SECONDS = 3600  # فحص الانتهاء والتذكير كل ساعة
PAYMENT_POLL_SECONDS = 60  # فحص الدفعات المعلّقة كل دقيقة
PAYMENT_WINDOW_HOURS = 48  # بعد هالمدة بنوقف متابعة الدفعة غير المكتملة
REUSE_LINK_HOURS = 24  # نعيد استخدام رابط الدفع الاخير اذا لسا حديث

ZIINA_API_URL = "https://api-v2.ziina.com/api"
UNPAID_STATES = ("requires_payment_instrument", "requires_user_action", "pending")

# حالة مؤقتة بالذاكرة
approving = set()  # موافقات يدوية قيد المعالجة (لمنع الضغط المزدوج)
kick_alerted = set()  # لتجنب تكرار تنبيه الادمن عن فشل الطرد
payment_alerted = set()  # لتجنب تكرار تنبيه الادمن عن فشل تفعيل دفعة
state = {"db_alerted": False}
_uid_locks = {}


def get_uid_lock(uid):
    lock = _uid_locks.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        _uid_locks[uid] = lock
    return lock


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


def hours_since(value):
    return (now_utc() - parse_ts(value)).total_seconds() / 3600


def fmt_date(dt):
    return dt.strftime("%Y-%m-%d")


def support_line():
    return f"\n\n📩 للدعم الفني: {SUPPORT_CONTACT}" if SUPPORT_CONTACT else ""


def approval_keyboard(uid):
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("موافقة", callback_data=f"approve_{uid}"),
            InlineKeyboardButton("رفض", callback_data=f"reject_{uid}"),
        ]]
    )


def payment_keyboard(url=None):
    rows = []
    if url:
        rows.append([InlineKeyboardButton("فتح صفحة الدفع الآمنة 💳", url=url)])
    rows.append([InlineKeyboardButton("أنا دفعت", callback_data="paid")])
    rows.append([InlineKeyboardButton("تواصل مع الدعم", callback_data="support")])
    return InlineKeyboardMarkup(rows)


async def notify_admin(bot, text):
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception as e:
        log.warning("notify_admin failed: %s", e)


async def edit_admin_note(q, text, keyboard=None):
    """تعديل رسالة الطلب عند الادمن (ممكن تكون نص او صورة)."""
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


# ---- payments table ----
async def db_insert_payment(uid, ziina_id, is_test):
    await db_request(
        "POST",
        "payments",
        json_body={
            "telegram_id": uid,
            "ziina_id": ziina_id,
            "status": "pending",
            "is_test": bool(is_test),
        },
        prefer="return=minimal",
    )


async def db_latest_pending_payment(uid):
    rows = await db_request(
        "GET",
        "payments",
        params={
            "telegram_id": f"eq.{uid}",
            "status": "eq.pending",
            "is_test": f"eq.{str(PAYMENT_TEST_MODE).lower()}",
            "select": "*",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def db_latest_payment(uid):
    rows = await db_request(
        "GET",
        "payments",
        params={
            "telegram_id": f"eq.{uid}",
            "select": "*",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def db_list_pending_payments():
    return (
        await db_request(
            "GET",
            "payments",
            params={
                "status": "eq.pending",
                "is_test": f"eq.{str(PAYMENT_TEST_MODE).lower()}",
                "select": "*",
                "order": "created_at.asc",
                "limit": "100",
            },
        )
        or []
    )


async def db_claim_payment(ziina_id):
    """يحجز الدفعة (pending -> completed) بشكل ذري. يرجع True اذا نجح الحجز."""
    rows = await db_request(
        "PATCH",
        "payments",
        params={"ziina_id": f"eq.{ziina_id}", "status": "eq.pending"},
        json_body={"status": "completed"},
        prefer="return=representation",
    )
    return bool(rows)


async def db_set_payment_status(ziina_id, status_value):
    await db_request(
        "PATCH",
        "payments",
        params={"ziina_id": f"eq.{ziina_id}"},
        json_body={"status": status_value},
        prefer="return=minimal",
    )


async def db_mark_pending_as_manual(uid):
    await db_request(
        "PATCH",
        "payments",
        params={"telegram_id": f"eq.{uid}", "status": "eq.pending"},
        json_body={"status": "manual"},
        prefer="return=minimal",
    )


# ====== Ziina API ======
async def ziina_request(method, path, json_body=None):
    headers = {
        "Authorization": f"Bearer {ZIINA_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(
            method, f"{ZIINA_API_URL}{path}", json=json_body, headers=headers
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Ziina {method} {path} -> {r.status_code}: {r.text[:200]}")
    return r.json()


async def ziina_create_intent(bot_username):
    back = f"https://t.me/{bot_username}"
    body = {
        "amount": PRICE_AED * 100,  # بالفلس: 50 درهم = 5000
        "currency_code": "AED",
        "message": f"Coach Sanaa VIP - {SUBSCRIPTION_DAYS} days subscription",
        "success_url": back,
        "cancel_url": back,
        "failure_url": back,
        "test": PAYMENT_TEST_MODE,
    }
    return await ziina_request("POST", "/payment_intent", body)


async def ziina_get_intent(intent_id):
    return await ziina_request("GET", f"/payment_intent/{intent_id}")


# ====== Subscription grant (shared by auto-payment and manual approval) ======
async def grant_subscription(bot, uid):
    """يفعّل او يجدد اشتراك المستخدم. يرجع dict فيه ok وتفاصيل."""
    async with get_uid_lock(uid):
        # 1) الحالة الحالية
        try:
            member = await db_get_member(uid)
        except Exception as e:
            return {"ok": False, "stage": "db", "error": str(e)}

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
            cm = await bot.get_chat_member(CHANNEL_ID, uid)
            already_in = cm.status in ("member", "administrator", "creator")
        except Exception as e:
            log.info("get_chat_member failed for %s: %s", uid, e)

        if not already_in:
            try:
                link = await bot.create_chat_invite_link(
                    CHANNEL_ID, member_limit=1, expire_date=now + timedelta(hours=24)
                )
                invite = link.invite_link
            except Exception as e:
                return {"ok": False, "stage": "invite", "error": str(e)}

        # 3) حفظ الاشتراك (قبل ارسال اي رابط)
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
            return {"ok": False, "stage": "save", "error": str(e)}

        # 4) اشعار المشترك
        date_str = fmt_date(new_expiry)
        if invite:
            user_text = (
                "تم تأكيد دفعك وتفعيل اشتراكك 🎉\n"
                f"🔗 رابط القناة الخاصة (صالح 24 ساعة ويعمل مرة واحدة):\n{invite}\n"
                f"📅 اشتراكك ينتهي: {date_str}"
            )
        else:
            user_text = f"تم تجديد اشتراكك ✅\n📅 ينتهي: {date_str}"

        user_notified = True
        notify_error = None
        try:
            await bot.send_message(uid, user_text)
        except Exception as e:
            user_notified = False
            notify_error = str(e)
            log.error("grant notify failed for %s: %s", uid, e)

        return {
            "ok": True,
            "date": date_str,
            "invite": invite,
            "user_notified": user_notified,
            "error": notify_error,
        }


# ====== Payment processing ======
async def process_payment(bot, row):
    """يفحص دفعة عند Ziina ويفعّل الاشتراك اذا اكتملت.
    يرجع: granted / done / mismatch / error / او حالة الدفعة (pending ...)."""
    uid = row["telegram_id"]
    zid = row["ziina_id"]
    intent = await ziina_get_intent(zid)
    st = intent.get("status")

    if st == "completed":
        amount = intent.get("amount") or 0
        currency = str(intent.get("currency_code") or "").upper()
        if amount < PRICE_AED * 100 or currency != "AED":
            await db_set_payment_status(zid, "mismatch")
            await notify_admin(
                bot,
                f"⚠️ دفعة مكتملة لكن المبلغ لا يطابق المطلوب: {zid}\n"
                f"المستخدم: {uid}\nالمبلغ: {amount} {currency}\nلم يتم تفعيل اشتراك.",
            )
            return "mismatch"

        if not await db_claim_payment(zid):
            return "done"  # عالجها طرف تاني (الفحص التلقائي او الزر)

        res = await grant_subscription(bot, uid)
        if not res["ok"]:
            await db_set_payment_status(zid, "pending")  # نعيد المحاولة لاحقا
            if zid not in payment_alerted:
                payment_alerted.add(zid)
                await notify_admin(
                    bot,
                    f"⚠️ دفعة مؤكدة لكن فشل التفعيل للمستخدم {uid} "
                    f"(المرحلة: {res['stage']}).\n{res['error']}\n"
                    "رح يعيد البوت المحاولة تلقائيا كل دقيقة.",
                )
            return "error"

        payment_alerted.discard(zid)
        kind = "تجريبية" if row.get("is_test") else "حقيقية"
        msg = f"💳 دفعة {kind} مؤكدة تلقائيا من {uid}\n📅 الاشتراك حتى: {res['date']}"
        if not res["user_notified"]:
            msg += f"\n⚠️ تعذر ارسال الرسالة للمشترك ({res['error']})."
            if res["invite"]:
                msg += f"\nارسل له الرابط يدويا: {res['invite']}"
        await notify_admin(bot, msg)
        return "granted"

    if st in ("failed", "canceled"):
        await db_set_payment_status(zid, st)
        return st

    return st or "unknown"


async def check_payments(context: ContextTypes.DEFAULT_TYPE):
    try:
        rows = await db_list_pending_payments()
    except Exception as e:
        log.error("check_payments db error: %s", e)
        return
    for r in rows:
        zid = r.get("ziina_id")
        try:
            if hours_since(r["created_at"]) > PAYMENT_WINDOW_HOURS:
                await db_set_payment_status(zid, "expired")
                continue
            await process_payment(context.bot, r)
        except Exception as e:
            log.error("check_payments error for %s: %s", zid, e)


# ====== Commands ======
def welcome_text():
    return f"""أهلاً بكِ في Coach Sanaa VIP 💎

قناتك الخاصة لتعلّم السباحة وممارسة الأيروبك والأكوا، بشكل تفاعلي وأنتِ في منزلك.
لياقة، رشاقة، ومتعة التعلّم ✨

✨ ماذا ستحصلين عليه:
• حصص وكورسات: سباحة، أيروبك، أكوا
• برامج تدريبية متكاملة
• نصائح تغذية عامة
• لايف أسبوعي
• متابعة يومية وتصحيح أخطاء

💳 الاشتراك: {PRICE_AED} درهم لمدة {SUBSCRIPTION_DAYS} يوماً
ولا يتجدد تلقائياً، وستصلك رسالة تذكير قبل انتهاء اشتراكك.

كيف تنضمين؟
1️⃣ اضغطي «ادفعي الآن» وأكملي الدفع ببطاقتك (Visa / Mastercard) أو عبر Apple Pay / Google Pay
2️⃣ بعد إتمام الدفع اضغطي «أنا دفعت» (وإن لم تضغطي، يتم التأكيد تلقائياً خلال دقائق)
3️⃣ بعد تأكيد الدفع يصلك رابط القناة الخاصة (صالح 24 ساعة ولمرة واحدة)

📅 لمعرفة حالة اشتراكك: /status

📋 شروط الاشتراك
• مدة الاشتراك {SUBSCRIPTION_DAYS} يوماً من تاريخ التفعيل، وينتهي الوصول للقناة تلقائياً بنهايتها.
• بعد تفعيل الاشتراك وإرسال رابط الدخول، لا يُسترد المبلغ عن الفترة المنقضية أو عند تغيّر الرغبة.
• إذا تعذّر عليكِ الدخول، أو واجهتِ مشكلة فنية، أو اختلفت الخدمة جوهرياً عن المعلن، راسلينا خلال {COMPLAINT_DAYS} أيام وسنعالج الأمر بإعادة تفعيل الوصول أو تعويض المدة أو الاسترداد بحسب الحالة.
• يُنصح باستشارة الطبيب قبل البدء بأي برنامج رياضي.

📩 للدعم الفني: {SUPPORT_CONTACT}"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("ادفعي الآن 💳", callback_data="pay")]]
    await update.message.reply_text(
        welcome_text(), reply_markup=InlineKeyboardMarkup(kb)
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


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اذا ارسل المشترك صورة/ملف (عادة ايصال) ننبهه ان التأكيد تلقائي."""
    await update.message.reply_text(
        "ما بتحتاجي ترسلي إيصال 🙂 الدفع بيتأكد تلقائياً.\n"
        "اضغطي «أنا دفعت» بعد إتمام الدفع، وإذا واجهتِ مشكلة اضغطي «تواصل مع الدعم»."
        + support_line(),
        reply_markup=payment_keyboard(),
    )


# ====== Buttons ======
async def handle_pay(user, context):
    uid = user.id
    bot = context.bot
    url = None
    try:
        existing = await db_latest_pending_payment(uid)
        if existing and hours_since(existing["created_at"]) < REUSE_LINK_HOURS:
            intent = await ziina_get_intent(existing["ziina_id"])
            st = intent.get("status")
            if st == "completed":
                await process_payment(bot, existing)  # بيفعّل وبيبعت الرسالة
                return
            if st in UNPAID_STATES and intent.get("redirect_url"):
                url = intent["redirect_url"]
        if not url:
            intent = await ziina_create_intent(bot.username)
            await db_insert_payment(uid, intent["id"], PAYMENT_TEST_MODE)
            url = intent["redirect_url"]
    except Exception as e:
        log.error("handle_pay failed for %s: %s", uid, e)
        await bot.send_message(
            uid, "تعذر إنشاء رابط الدفع حالياً، حاولي بعد قليل." + support_line()
        )
        await notify_admin(bot, f"⚠️ فشل إنشاء رابط دفع للمستخدم {uid}:\n{e}")
        return

    text = (
        "رابط الدفع الخاص بك جاهز ✅\n"
        "افتحي الصفحة وأكملي الدفع، وبعدها ارجعي واضغطي «أنا دفعت».\n"
        "حتى لو ما ضغطتي، رح يتأكد الدفع تلقائياً خلال دقائق."
    )
    if PAYMENT_TEST_MODE:
        text += "\n\n⚠️ وضع تجريبي: لن يتم خصم أي مبلغ، استخدمي أي بطاقة تجريبية."
    await bot.send_message(uid, text, reply_markup=payment_keyboard(url))


async def handle_paid(user, context):
    uid = user.id
    bot = context.bot
    try:
        row = await db_latest_pending_payment(uid)
    except Exception as e:
        log.error("handle_paid db error: %s", e)
        await bot.send_message(uid, "تعذر التحقق حاليا، حاولي بعد قليل." + support_line())
        return

    if not row:
        await bot.send_message(
            uid,
            "ما في عملية دفع معلّقة حالياً.\n"
            "إذا دفعتِ وتأكد دفعك، يصلك رابط القناة تلقائياً، "
            "واكتبي /status لتشوفي حالة اشتراكك.\n"
            "وإلا اضغطي «ادفعي الآن» من /start." + support_line(),
        )
        return

    try:
        result = await process_payment(bot, row)
    except Exception as e:
        log.error("handle_paid process error: %s", e)
        await bot.send_message(
            uid,
            "تعذر التحقق من الدفع حالياً، جرّبي بعد دقيقة." + support_line(),
            reply_markup=payment_keyboard(),
        )
        return

    if result == "granted":
        return  # رسالة التفعيل انبعتت من grant_subscription
    if result == "done":
        text = "تمت معالجة دفعك. اكتبي /status لتشوفي حالة اشتراكك."
        kb = None
    elif result == "requires_payment_instrument":
        text = (
            "لم تكتمل عملية الدفع بعد.\n"
            "أكملي الدفع من رسالة الرابط السابقة، أو اضغطي «ادفعي الآن» في /start "
            "لتحصلي على الرابط مرة ثانية."
        )
        kb = payment_keyboard()
    elif result in ("pending", "requires_user_action"):
        text = "الدفع قيد المعالجة. جرّبي بعد دقيقة، ورح يتأكد تلقائياً كمان."
        kb = payment_keyboard()
    elif result in ("failed", "canceled"):
        text = "لم تتم عملية الدفع. اضغطي «ادفعي الآن» في /start لإنشاء رابط جديد."
        kb = None
    else:
        text = "حدث خطأ أثناء التأكيد وتم إبلاغ الدعم. إذا لم يُفعّل اشتراكك قريباً راسلينا."
        kb = payment_keyboard()
    await bot.send_message(uid, text + support_line(), reply_markup=kb)


async def handle_support(user, context):
    uid = user.id
    bot = context.bot
    info = "لا توجد عملية دفع مسجّلة."
    try:
        row = await db_latest_payment(uid)
        if row:
            info = (
                f"آخر عملية: {row['ziina_id']}\n"
                f"الحالة عندنا: {row['status']}\n"
                f"تجريبية: {row['is_test']}\n"
                f"الوقت: {row['created_at']}"
            )
    except Exception as e:
        log.warning("support lookup failed: %s", e)

    text = (
        "🆘 طلب دعم\n"
        f"المعرف: {uid}\n"
        f"المستخدم: @{user.username or '-'}\n"
        f"الاسم: {user.full_name}\n"
        f"{info}\n\n"
        "تحقق من الدفعة بتطبيق Ziina، ثم وافق أو ارفض."
    )
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=approval_keyboard(uid))
    except Exception as e:
        log.error("support forward failed: %s", e)
        await bot.send_message(
            uid, "تعذر إرسال الطلب حاليا، راسلينا مباشرة." + support_line()
        )
        return
    await bot.send_message(
        uid, "تم إرسال طلبك إلى الدعم ✅ سنراجع الدفع ونرد عليك قريباً." + support_line()
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    user = q.from_user

    if data.startswith(("approve_", "reject_")) and user.id != ADMIN_ID:
        await q.answer("هذا للادارة فقط", show_alert=True)
        return
    await q.answer()

    if data == "pay":
        await handle_pay(user, context)

    elif data == "paid":
        await handle_paid(user, context)

    elif data == "support":
        await handle_support(user, context)

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
                "لم نتمكن من تأكيد الدفع. اضغطي /start ثم «ادفعي الآن». "
                "وإذا دفعتِ فعلاً ولم يُفعّل اشتراكك، راسلينا." + support_line(),
            )
        except Exception as e:
            log.warning("reject notify failed: %s", e)


async def handle_approve(uid, q, context):
    """موافقة يدوية من الادمن (مثلا بعد طلب دعم)."""
    await edit_admin_note(q, f"⏳ جاري معالجة الموافقة لـ {uid} ...")

    res = await grant_subscription(context.bot, uid)
    if not res["ok"]:
        reasons = {
            "db": "فشل الاتصال بقاعدة البيانات",
            "invite": "تعذر انشاء رابط الدخول (تاكد ان البوت مشرف بالقناة وعنده صلاحية دعوة المستخدمين)",
            "save": "فشل حفظ الاشتراك بالقاعدة",
        }
        await edit_admin_note(
            q,
            f"❌ {reasons.get(res['stage'], 'فشل')}. لم يتم ارسال شي للمشترك.\n{res['error']}",
            approval_keyboard(uid),
        )
        return

    try:
        await db_mark_pending_as_manual(uid)  # حتى ما ينعالج نفس الدفع مرتين
    except Exception as e:
        log.warning("mark manual failed: %s", e)

    admin_text = f"✅ تمت الموافقة على {uid}\n📅 ينتهي: {res['date']}"
    if res["invite"]:
        admin_text += f"\n🔗 {res['invite']}"
    if not res["user_notified"]:
        admin_text += f"\n⚠️ تعذر ارسال الرسالة للمشترك ({res['error']}). ارسل له الرابط يدويا."
    await edit_admin_note(q, admin_text)


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
                f"⚠️ تعذر ايقاف وصول {uid} بعد انتهاء اشتراكه. تاكد ان البوت مشرف وعنده "
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
            "انتهى اشتراكك الشهري وتم إيقاف وصولك للقناة.\n"
            "يسعدنا انضمامك مجدداً، لتجديد الاشتراك اضغطي /start" + support_line(),
        )
    except Exception:
        pass
    await notify_admin(context.bot, f"🚪 انتهى اشتراك {uid} وتم ايقاف وصوله")


async def maybe_remind(context, member, expiry, now):
    uid = member["telegram_id"]
    remaining = expiry - now
    days_left = max(1, -(-int(remaining.total_seconds()) // 86400))
    date_str = fmt_date(expiry)

    if remaining <= timedelta(days=1):
        already = member.get("reminded_1")
        flags = {"reminded_7": True, "reminded_3": True, "reminded_1": True}
        text = (
            f"⚠️ آخر يوم: اشتراكك ينتهي خلال 24 ساعة ({date_str}) وبعدها يتوقف "
            "وصولك للقناة.\nللتجديد اضغطي /start."
        )
    elif remaining <= timedelta(days=3):
        already = member.get("reminded_3")
        flags = {"reminded_7": True, "reminded_3": True}
        text = (
            f"⏰ تذكير: اشتراكك ينتهي بتاريخ {date_str} (بعد {days_left} ايام تقريبا).\n"
            "للتجديد اضغطي /start."
        )
    elif remaining <= timedelta(days=7):
        already = member.get("reminded_7")
        flags = {"reminded_7": True}
        text = (
            f"⏰ تذكير: اشتراكك ينتهي بتاريخ {date_str} (بعد {days_left} ايام تقريبا).\n"
            "للتجديد اضغطي /start."
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
    mode = (
        "🧪 تجريبي (لا يتم خصم أي مبلغ)"
        if PAYMENT_TEST_MODE
        else "💰 حقيقي (يتم خصم مبالغ فعلية)"
    )
    try:
        members = await db_list_members()
        msg = (
            "✅ البوت اشتغل وقاعدة البيانات متصلة.\n"
            f"عدد المشتركين الحاليين: {len(members)}\n"
            f"وضع الدفع: {mode}"
        )
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
            ("ZIINA_API_KEY", ZIINA_API_KEY),
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
            on_media,
        )
    )
    application.add_error_handler(on_error)

    application.job_queue.run_repeating(
        check_expiry, interval=CHECK_INTERVAL_SECONDS, first=30
    )
    application.job_queue.run_repeating(
        check_payments, interval=PAYMENT_POLL_SECONDS, first=45
    )

    log.info(
        "Bot is running - Supabase storage + Ziina auto-verification (test mode: %s)",
        PAYMENT_TEST_MODE,
    )
    application.run_polling()


if __name__ == "__main__":
    main()
