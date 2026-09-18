import os
import io
import re
import json
import time
import zipfile
import uuid
import threading
import asyncio
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
import img2pdf
import rarfile
from PIL import Image
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, filters,
    CommandHandler, CallbackQueryHandler
)

# ===== تنظیمات - توکن از Environment Variable خونده میشه =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# ===== تنظیمات Local Bot API Server =====
# اگه USE_LOCAL_BOT_API روشن باشه (پیش‌فرض: روشن)، بات به‌جای
# api.telegram.org به سرور محلی telegram-bot-api که توی همین کانتینر
# اجرا میشه وصل میشه - همینه که محدودیت حجم فایل رو از ۲۰/۵۰ مگابایت
# به ۲ گیگابایت می‌بره بالا.
USE_LOCAL_BOT_API = os.environ.get("USE_LOCAL_BOT_API", "true").lower() == "true"
LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL", "http://localhost:8081")
# ================================================================

# آیدی عددی کاربرهایی که از قبل اجازه استفاده دارن (فقط برای اولین بار / seed)
ALLOWED_USER_IDS = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

# آیدی عددی ادمین‌ها (فقط برای اولین بار / seed - بعدش از فایل خونده میشه)
ADMIN_IDS_SEED = {
    int(uid.strip())
    for uid in os.environ.get("ADMIN_IDS", "").split(",")
    if uid.strip().isdigit()
}
# ================================================================

USERS_FILE = "users_data.json"
LIST_PAGE_SIZE = 6

# ===== ذخیره‌سازی دائمی با Supabase =====
# روی پلن رایگان Render دیسک موقتیه و هر ری‌استارت/دیپلوی فایل رو پاک
# می‌کنه. برای همین اگه این دو متغیر ست شده باشن، به‌جای فایل روی دیسک،
# از جدول bot_kv توی Supabase (رایگان و دائمی) استفاده می‌کنیم.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)
SUPABASE_ROW_KEY = "users_data"


def load_users():
    if USE_SUPABASE:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/bot_kv",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                },
                params={"key": f"eq.{SUPABASE_ROW_KEY}", "select": "value"},
                timeout=15,
            )
            r.raise_for_status()
            rows = r.json()
            if rows:
                return rows[0].get("value") or {}
            return {}
        except Exception as e:
            print(f"⚠️ خواندن از Supabase با خطا مواجه شد: {e}")
            return {}

    # حالت پشتیبان: فایل محلی (روی رندر رایگان دائمی نیست!)
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_users(users):
    if USE_SUPABASE:
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/bot_kv",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/json",
                    # merge-duplicates یعنی اگه ردیف با همین key وجود داشت
                    # آپدیت بشه (upsert) نه اینکه خطای تکراری بده
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                json=[{"key": SUPABASE_ROW_KEY, "value": users}],
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            print(f"⚠️ ذخیره در Supabase با خطا مواجه شد: {e}")
        return

    # حالت پشتیبان: فایل محلی (روی رندر رایگان دائمی نیست!)
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ ذخیره فایل کاربران با خطا مواجه شد: {e}")


# {user_id_str: {first_name, username, allowed, is_admin}}
USERS = load_users()


def register_user(user):
    uid = str(user.id)
    seed_admin = user.id in ADMIN_IDS_SEED
    if uid not in USERS:
        USERS[uid] = {
            "first_name": user.first_name or "",
            "username": user.username or "",
            "allowed": seed_admin or user.id in ALLOWED_USER_IDS,
            "is_admin": seed_admin,
        }
    else:
        USERS[uid]["first_name"] = user.first_name or ""
        USERS[uid]["username"] = user.username or ""
        USERS[uid]["is_admin"] = bool(USERS[uid].get("is_admin", False)) or seed_admin
        if seed_admin:
            USERS[uid]["allowed"] = True
    save_users(USERS)


def is_admin(user_id: int) -> bool:
    info = USERS.get(str(user_id))
    if info is not None:
        return bool(info.get("is_admin", False)) or user_id in ADMIN_IDS_SEED
    return user_id in ADMIN_IDS_SEED


def get_admin_ids():
    ids = {int(uid) for uid, info in USERS.items() if info.get("is_admin")}
    ids |= ADMIN_IDS_SEED
    return ids


def is_allowed(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    info = USERS.get(str(user_id))
    if info is not None:
        return bool(info.get("allowed", False))
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

# ===== وب‌سرور کوچیک برای زنده نگه‌داشتن سرویس روی Render =====
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot is running."


def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)
# ================================================================

CATBOX_API = "https://catbox.moe/user/api.php"

PROGRESS_UPDATE_INTERVAL = 2.0  # ثانیه - هر چند وقت یه‌بار نوار پیشرفت آپدیت بشه


# ===== مدیریت عملیات‌های در حال اجرا (برای دکمه‌ی «❌ لغو») =====
# هر دانلود/آپلود یه task_id کوتاه می‌گیره. دکمه‌ی لغو همین id رو توی
# callback_data می‌بره و button_handler با اون، عملیات درست رو متوقف می‌کنه.
_TASKS = {}


class TaskCancelled(Exception):
    """وقتی کاربر دکمه‌ی «❌ لغو» رو بزنه پرتاب میشه."""


def _new_task(chat_id):
    task_id = uuid.uuid4().hex[:8]
    _TASKS[task_id] = {"chat_id": chat_id, "cancelled": False, "task": None}
    return task_id


def _end_task(task_id):
    _TASKS.pop(task_id, None)


def cancel_keyboard(task_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ لغو", callback_data=f"cancel_task_{task_id}")]
    ])


# قفل جداگانه برای هر کاربر: چون آپدیت‌ها همزمان پردازش میشن (تا دکمه‌ی لغو
# وسط آپلود کار کنه)، فایل‌های یه کاربر باید همچنان یکی‌یکی و به‌ترتیب پردازش بشن.
_USER_LOCKS = {}


def _user_lock(user_id):
    lock = _USER_LOCKS.get(user_id)
    if lock is None:
        lock = _USER_LOCKS[user_id] = asyncio.Lock()
    return lock


def _format_mb(n):
    return f"{(n or 0) / (1024 * 1024):.1f}MB"


def _format_speed(bytes_per_sec):
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f}MB/s"
    return f"{bytes_per_sec / 1024:.0f}KB/s"


def _format_duration(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} ثانیه"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} دقیقه" + (f" و {secs} ثانیه" if secs else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ساعت" + (f" و {minutes} دقیقه" if minutes else "")


def _progress_bar(done, total, width=14):
    if not total:
        return "░" * width
    pct = max(0.0, min(1.0, done / total))
    filled = int(width * pct)
    return "▓" * filled + "░" * (width - filled)


def _build_upload_text(prefix, done, total, speed):
    done = min(done, total) if total else done
    pct = min(100, int(done * 100 / total)) if total else 0
    lines = [
        prefix,
        f"[{_progress_bar(done, total)}] {pct}%",
        f"{_format_mb(done)} / {_format_mb(total)}",
    ]
    if speed > 0:
        eta = (total - done) / speed
        lines.append(f"🚀 {_format_speed(speed)}  •  ⏳ {_format_duration(eta)} مانده")
    else:
        lines.append("🚀 در حال محاسبه سرعت...")
    return "\n".join(lines)


def upload_catbox(filename, file_bytes, progress_cb=None):
    """
    آپلود به Catbox. اگه progress_cb داده بشه، آپلود به‌صورت استریم (با
    MultipartEncoderMonitor) انجام میشه و progress_cb(bytes_sent, total_bytes)
    هر چند بار که داده واقعاً روی سوکت نوشته بشه صدا زده میشه - یعنی درصدِ
    واقعیِ آپلوده، نه یه تخمین ساختگی.

    progress_cb می‌تونه TaskCancelled پرتاب کنه تا آپلود وسط کار قطع بشه.
    """
    try:
        if progress_cb:
            encoder = MultipartEncoder(fields={
                "reqtype": "fileupload",
                "fileToUpload": (filename, io.BytesIO(bytes(file_bytes)), "application/octet-stream"),
            })
            total = encoder.len  # حجم کل بدنه‌ی درخواست (فایل + هدرهای multipart)

            def _on_read(monitor):
                progress_cb(monitor.bytes_read, total)

            monitor = MultipartEncoderMonitor(encoder, _on_read)
            r = requests.post(
                CATBOX_API,
                data=monitor,
                headers={"Content-Type": monitor.content_type},
                timeout=300,
            )
        else:
            r = requests.post(
                CATBOX_API,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": (filename, bytes(file_bytes))},
                timeout=60,
            )
        if r.status_code == 200 and r.text.startswith("http"):
            return r.text.strip()
    except TaskCancelled:
        raise
    except Exception:
        pass
    return None


async def _safe_edit(status_msg, text, reply_markup=None):
    # نکته: ادیت بدون reply_markup دکمه‌های پیام رو پاک می‌کنه، برای همین
    # هر ادیتِ پیشرفت باید دکمه‌ی لغو رو دوباره بفرسته.
    try:
        await status_msg.edit_text(text, reply_markup=reply_markup)
    except Exception:
        pass


async def upload_catbox_with_progress(filename, file_bytes, status_msg, prefix="", task_id=None):
    """
    upload_catbox رو توی یه ترد جدا اجرا می‌کنه (تا بلاک نکنه) و پیام status_msg
    رو حداکثر هر PROGRESS_UPDATE_INTERVAL ثانیه با درصد واقعیِ آپلود، حجم،
    سرعت و زمان باقی‌مانده آپدیت می‌کنه. زیر پیام دکمه‌ی «❌ لغو» هست.

    اگه task_id داده نشه، خودش یه عملیات جدید می‌سازه (و تهش پاکش می‌کنه).
    اگه کاربر لغو کنه TaskCancelled پرتاب میشه.
    """
    owns_task = task_id is None
    if owns_task:
        task_id = _new_task(status_msg.chat_id)
    state = _TASKS[task_id]
    kb = cancel_keyboard(task_id)

    loop = asyncio.get_running_loop()
    total_size = len(file_bytes)
    start = time.monotonic()
    tracker = {"edit_at": 0.0, "t": start, "done": 0, "speed": 0.0}

    # همون لحظه‌ی شروع، دکمه‌ی لغو رو نشون بده (نه بعد از اولین ۲ ثانیه)
    await _safe_edit(status_msg, _build_upload_text(prefix, 0, total_size or 1, 0.0), kb)

    def progress_cb(done, total_bytes):
        if state["cancelled"]:
            raise TaskCancelled()
        now = time.monotonic()
        if now - tracker["edit_at"] < PROGRESS_UPDATE_INTERVAL and done < total_bytes:
            return
        dt = now - tracker["t"]
        if dt >= 0.5:
            inst = (done - tracker["done"]) / dt
            # میانگین‌گیری نمایی تا سرعت و ETA مدام نپره
            tracker["speed"] = inst if tracker["speed"] == 0 else 0.6 * tracker["speed"] + 0.4 * inst
            tracker["t"], tracker["done"] = now, done
        elif tracker["speed"] == 0 and now - start >= 0.2 and done > 0:
            # آپلودهای خیلی سریع: هنوز نمونه‌ی کافی نداریم، میانگین کل رو نشون بده
            tracker["speed"] = done / (now - start)
        tracker["edit_at"] = now
        text = _build_upload_text(prefix, done, total_bytes, tracker["speed"])
        asyncio.run_coroutine_threadsafe(_safe_edit(status_msg, text, kb), loop)

    try:
        link = await asyncio.to_thread(upload_catbox, filename, file_bytes, progress_cb)
        if state["cancelled"] and not link:
            raise TaskCancelled()
        return link
    finally:
        if owns_task:
            _end_task(task_id)


async def download_with_progress(file_src, status_msg, total_size, prefix="⬇️ در حال دریافت..."):
    """
    file_src یه Document/PhotoSize تلگرامه (چیزی که .get_file() داره).

    توجه: توی حالت Local Bot API Server، دانلود واقعی از سرورهای تلگرام
    داخل خودِ درخواست get_file() انجام میشه و سرور محلی درصد لحظه‌ای بهمون
    نمی‌ده. برای همین اینجا درصد جعلی نمی‌سازیم؛ زمان سپری‌شده و حجم فایل رو
    نشون می‌دیم. (قبلاً get_file بیرون از این تابع صدا زده می‌شد و تیکر فقط
    روی خوندن فایل از دیسک بود - الان کل مرحله‌ی دریافت زیر تیکر و دکمه‌ی لغو هست.)

    اگه کاربر لغو کنه TaskCancelled پرتاب میشه.
    """
    task_id = _new_task(status_msg.chat_id)
    state = _TASKS[task_id]
    kb = cancel_keyboard(task_id)
    size_txt = _format_mb(total_size) if total_size else "نامشخص"

    async def _work():
        file_obj = await file_src.get_file()
        return bytes(await file_obj.download_as_bytearray())

    work = asyncio.ensure_future(_work())
    state["task"] = work
    start = time.monotonic()

    try:
        await _safe_edit(status_msg, f"{prefix}\n📦 حجم فایل: {size_txt}", kb)
        while not work.done():
            await asyncio.wait({work}, timeout=PROGRESS_UPDATE_INTERVAL)
            if work.done():
                break
            elapsed = _format_duration(time.monotonic() - start)
            await _safe_edit(
                status_msg,
                f"{prefix}\n⏱ {elapsed} گذشته\n📦 حجم فایل: {size_txt}",
                kb,
            )
        try:
            return await work
        except asyncio.CancelledError:
            if state["cancelled"]:
                raise TaskCancelled()
            raise
    except BaseException:
        if not work.done():
            work.cancel()
        raise
    finally:
        _end_task(task_id)


def main_menu():
    keyboard = [
        [InlineKeyboardButton("📷 آپلود کاور", callback_data="mode_cover")],
        [InlineKeyboardButton("📄 آپلود PDF", callback_data="mode_pdf")],
        [InlineKeyboardButton("📦 زیپ عکس‌ها به PDF", callback_data="mode_zip")],
        [InlineKeyboardButton("🔗 اتصال عکس‌ها به PDF", callback_data="mode_connect")],
        [InlineKeyboardButton("🗜️ رار عکس‌ها به PDF", callback_data="mode_rar")],
        [InlineKeyboardButton("📚 آپلود گروهی", callback_data="mode_bulk")],
    ]
    return InlineKeyboardMarkup(keyboard)


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff")
NESTED_ARCHIVE_MAX_DEPTH = 3


def _natural_sort_key(name):
    # مرتب‌سازی طبیعی: img2 قبل از img10 بیاد، نه بعدش
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


def _to_img2pdf_bytes(raw):
    """
    اگه بایت خام عکس مستقیم توسط img2pdf قابل embed باشه (بدون دیکد
    شدن به بیت‌مپ)، همون بایت اصلی و دست‌نخورده برگردونده میشه —
    یعنی صفر افت کیفیت و کمترین مصرف رم.
    اگه فرمت مشکل‌دار باشه (PNG با کانال آلفا، حالت پالت، یا فرمتی
    غیر از JPEG/PNG مثل webp/bmp/gif/tiff)، فقط همون یه عکس یک بار
    با کیفیت ۹۵٪ به JPEG تبدیل میشه تا img2pdf بتونه قبولش کنه.
    اگه عکس اصلاً خراب/نامعتبر باشه None برمی‌گردونه.
    """
    try:
        img2pdf.convert([raw])
        return raw
    except Exception:
        pass
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except Exception:
        return None


def images_bytes_to_pdf(entries):
    """
    entries: لیستی از (filename, raw_bytes) که از قبل به ترتیب دلخواه
    مرتب شده. با img2pdf (بدون دیکد کامل به بیت‌مپ خام) توی یه PDF
    چندصفحه‌ای می‌چسبونه تا هم رم کمتر مصرف بشه هم کیفیت کامل حفظ بشه.
    اگه هیچ عکس معتبری توی entries نباشه، None برمی‌گردونه.
    """
    prepared = [
        data for data in (_to_img2pdf_bytes(raw) for _name, raw in entries)
        if data is not None
    ]

    if not prepared:
        return None

    try:
        return img2pdf.convert(prepared)
    except Exception:
        return None


def _open_archive(archive_bytes, kind):
    if kind == "zip":
        return zipfile.ZipFile(io.BytesIO(archive_bytes))
    return rarfile.RarFile(io.BytesIO(archive_bytes))


def _collect_images_from_archive_bytes(archive_bytes, kind, depth=1, max_depth=NESTED_ARCHIVE_MAX_DEPTH):
    """
    یه فایل زیپ/رار رو باز می‌کنه و همه‌ی عکس‌های داخلش رو جمع می‌کنه.
    اگه داخلش یه آرشیو دیگه (زیپ یا رار) پیدا بشه، تا max_depth سطح
    توش هم دنبال عکس می‌گرده. هر فایلی که نه عکسه، نه آرشیوِ قابل‌بازکردن
    (یا عمقش از max_depth بیشتر شده)، به‌سادگی نادیده گرفته میشه - کل
    عملیات به‌خاطر یه فایل غیرعکس متوقف نمیشه.
    خروجی: لیستی از (name, raw_bytes) که هنوز مرتب نشده.
    """
    collected = []
    try:
        with _open_archive(archive_bytes, kind) as af:
            names = [
                n for n in af.namelist()
                if not n.endswith("/")
                and not os.path.basename(n).startswith(".")
                and os.path.basename(n) != ""
            ]
            names.sort(key=_natural_sort_key)

            for name in names:
                try:
                    raw = af.read(name)
                except Exception:
                    continue

                lower = name.lower()
                if lower.endswith(IMAGE_EXTENSIONS):
                    collected.append((name, raw))
                elif lower.endswith(".zip") and depth < max_depth:
                    collected.extend(
                        _collect_images_from_archive_bytes(raw, "zip", depth + 1, max_depth)
                    )
                elif lower.endswith(".rar") and depth < max_depth:
                    collected.extend(
                        _collect_images_from_archive_bytes(raw, "rar", depth + 1, max_depth)
                    )
                # وگرنه: نه عکسه نه آرشیو قابل بازکردن -> نادیده گرفته میشه
    except Exception:
        return []
    return collected


def _archive_to_pdf(archive_bytes, kind):
    entries = _collect_images_from_archive_bytes(archive_bytes, kind)
    if not entries:
        return None

    entries.sort(key=lambda e: _natural_sort_key(e[0]))

    prepared = [
        data for data in (_to_img2pdf_bytes(raw) for _name, raw in entries)
        if data is not None
    ]
    if not prepared:
        return None

    try:
        return img2pdf.convert(prepared)
    except Exception:
        return None


def convert_zip_images_to_pdf(zip_bytes):
    """
    عکس‌های داخل یه فایل زیپ (و زیپ/رارهای تودرتوی داخلش، تا ۳ سطح) رو
    پیدا می‌کنه، به ترتیب اسم مرتب می‌کنه و توی یه PDF چندصفحه‌ای
    می‌چسبونه. اگه هیچ عکسی پیدا نشه یا زیپ خراب باشه، None برمی‌گردونه.
    """
    return _archive_to_pdf(zip_bytes, "zip")


def convert_rar_images_to_pdf(rar_bytes):
    """
    مثل convert_zip_images_to_pdf ولی برای فایل RAR (و زیپ/رارهای
    تودرتوی داخلش، تا ۳ سطح).
    """
    return _archive_to_pdf(rar_bytes, "rar")


def user_display_name(user):
    if user.username:
        return f"{user.first_name or ''} (@{user.username})".strip()
    return user.first_name or str(user.id)


# ==================== بخش ادمین ====================

def admin_menu():
    keyboard = [
        [InlineKeyboardButton("📋 لیست کاربران", callback_data="admin_list_0")],
        [InlineKeyboardButton("🔍 جستجوی آیدی", callback_data="admin_search")],
        [InlineKeyboardButton("👑 مدیریت ادمین‌ها", callback_data="admin_admins_0")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_user_list_keyboard(ids, page, callback_prefix="admin_user_", list_callback_prefix="admin_list_"):
    per_page = LIST_PAGE_SIZE
    total_pages = max(1, (len(ids) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = ids[start:start + per_page]

    keyboard = []
    for uid in chunk:
        info = USERS.get(uid, {})
        status = "✅" if info.get("allowed") else "⛔"
        name = info.get("first_name") or ""
        label = f"{status} {uid}" + (f" - {name}" if name else "")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{callback_prefix}{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"{list_callback_prefix}{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"{list_callback_prefix}{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard), total_pages, page


def build_admin_list_keyboard(ids, page):
    per_page = LIST_PAGE_SIZE
    total_pages = max(1, (len(ids) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = ids[start:start + per_page]

    keyboard = []
    for uid in chunk:
        info = USERS.get(uid, {})
        name = info.get("first_name") or ""
        label = f"👑 {uid}" + (f" - {name}" if name else "")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"admin_admin_detail_{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"admin_admins_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"admin_admins_{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard), total_pages, page


def build_user_detail_keyboard(uid):
    keyboard = [
        [
            InlineKeyboardButton("✅ اجازه بده", callback_data=f"admin_allow_{uid}"),
            InlineKeyboardButton("⛔ اجازه نده", callback_data=f"admin_deny_{uid}"),
        ],
        [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="admin_list_0")],
    ]
    return InlineKeyboardMarkup(keyboard)


def user_detail_text(uid):
    info = USERS.get(uid, {})
    name = info.get("first_name") or "-"
    username = info.get("username") or "-"
    status = "✅ مجاز" if info.get("allowed") else "⛔ غیرمجاز"
    return (
        f"👤 آیدی: <code>{uid}</code>\n"
        f"نام: {name}\n"
        f"یوزرنیم: @{username}\n"
        f"وضعیت فعلی: {status}"
    )


def admin_detail_text(uid):
    info = USERS.get(uid, {})
    name = info.get("first_name") or "-"
    username = info.get("username") or "-"
    return (
        f"👑 آیدی: <code>{uid}</code>\n"
        f"نام: {name}\n"
        f"یوزرنیم: @{username}"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ شما به بخش مدیریت دسترسی ندارید.")
        return
    await update.message.reply_text("بخش مدیریت 👇", reply_markup=admin_menu())


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.answer("⛔ شما به بخش مدیریت دسترسی ندارید.", show_alert=True)
        return

    await query.answer()

    if data == "admin_back":
        context.user_data.pop("admin_filtered_ids", None)
        context.user_data.pop("awaiting_admin_search", None)
        await query.edit_message_text("بخش مدیریت 👇", reply_markup=admin_menu())
        return

    if data == "admin_search":
        context.user_data["awaiting_admin_search"] = True
        await query.edit_message_text(
            "آیدی یا بخشی از آیدی مورد نظر رو به صورت پیام بفرست:"
        )
        return

    if data.startswith("admin_list_"):
        page = int(data.split("_")[-1])
        filtered = context.user_data.get("admin_filtered_ids")
        ids = filtered if filtered is not None else list(USERS.keys())
        if not ids:
            await query.edit_message_text(
                "هیچ کاربری هنوز /start نزده.",
                reply_markup=admin_menu()
            )
            return
        keyboard, total_pages, page = build_user_list_keyboard(ids, page)
        await query.edit_message_text(
            f"لیست کاربران (صفحه {page + 1} از {total_pages}):",
            reply_markup=keyboard
        )
        return

    if data.startswith("admin_user_"):
        uid = data[len("admin_user_"):]
        await query.edit_message_text(
            user_detail_text(uid),
            parse_mode="HTML",
            reply_markup=build_user_detail_keyboard(uid)
        )
        return

    if data.startswith("admin_allow_") or data.startswith("admin_deny_"):
        allow = data.startswith("admin_allow_")
        uid = data.split("_", 2)[-1]
        if uid in USERS:
            USERS[uid]["allowed"] = allow
            save_users(USERS)
        await query.edit_message_text(
            user_detail_text(uid),
            parse_mode="HTML",
            reply_markup=build_user_detail_keyboard(uid)
        )
        return

    if data.startswith("admin_admins_"):
        page = int(data.split("_")[-1])
        ids = [uid for uid, info in USERS.items() if info.get("is_admin")]
        if not ids:
            await query.edit_message_text(
                "هیچ ادمینی ثبت نشده.",
                reply_markup=admin_menu()
            )
            return
        keyboard, total_pages, page = build_admin_list_keyboard(ids, page)
        await query.edit_message_text(
            f"لیست ادمین‌ها (صفحه {page + 1} از {total_pages}):",
            reply_markup=keyboard
        )
        return

    if data.startswith("admin_admin_detail_"):
        uid = data[len("admin_admin_detail_"):]
        keyboard = []
        if uid != str(user_id):
            keyboard.append([InlineKeyboardButton("❌ حذف از ادمین", callback_data=f"admin_demote_{uid}")])
        else:
            keyboard.append([InlineKeyboardButton("این شما هستید", callback_data="admin_noop")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست ادمین‌ها", callback_data="admin_admins_0")])
        await query.edit_message_text(
            admin_detail_text(uid),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("admin_demote_"):
        uid = data[len("admin_demote_"):]
        if uid == str(user_id):
            await query.answer("⛔ نمی‌تونی خودت رو حذف کنی.", show_alert=True)
            return
        if uid in USERS:
            USERS[uid]["is_admin"] = False
            save_users(USERS)
        await query.edit_message_text(
            "✅ کاربر از لیست ادمین‌ها حذف شد.",
            reply_markup=admin_menu()
        )
        return

    if data == "admin_noop":
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فقط برای جستجوی آیدی توسط ادمین استفاده میشه
    user_id = update.effective_user.id
    if is_admin(user_id) and context.user_data.get("awaiting_admin_search"):
        context.user_data.pop("awaiting_admin_search", None)
        term = update.message.text.strip()
        filtered = [uid for uid in USERS.keys() if term in uid]
        context.user_data["admin_filtered_ids"] = filtered

        if not filtered:
            await update.message.reply_text(
                "هیچ کاربری با این آیدی پیدا نشد.",
                reply_markup=admin_menu()
            )
            return

        keyboard, total_pages, page = build_user_list_keyboard(filtered, 0)
        await update.message.reply_text(
            f"نتایج جستجو (صفحه {page + 1} از {total_pages}):",
            reply_markup=keyboard
        )

# ==================== درخواست اجازه‌ی کاربر ====================

async def handle_request_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    uid = str(user.id)

    if is_allowed(user.id):
        await query.answer("شما همین الان هم اجازه دارید ✅", show_alert=True)
        return

    await query.answer("درخواست شما برای ادمین ارسال شد ✅", show_alert=True)

    admin_ids = get_admin_ids()
    if not admin_ids:
        return

    name = user_display_name(user)
    text = (
        f"این {name} درخواست استفاده از بات شما را دارد.\n"
        f"لطفا یکی از گزینه‌های زیر را انتخاب کنید:"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ اجازه دادند", callback_data=f"reqallow_{uid}"),
            InlineKeyboardButton("⛔ اجازه ندادند", callback_data=f"reqdeny_{uid}"),
        ]
    ])

    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
        except Exception as e:
            print(f"⚠️ ارسال درخواست به ادمین {admin_id} با خطا مواجه شد: {e}")


async def handle_request_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    admin_user_id = update.effective_user.id

    if not is_admin(admin_user_id):
        await query.answer("⛔ شما به این بخش دسترسی ندارید.", show_alert=True)
        return

    await query.answer()

    allow = data.startswith("reqallow_")
    uid = data.split("_", 1)[-1]

    if uid in USERS:
        USERS[uid]["allowed"] = allow
        save_users(USERS)

    info = USERS.get(uid, {})
    name = info.get("first_name") or uid
    decision_text = "✅ شما اجازه دادید." if allow else "⛔ شما اجازه ندادید."
    await query.edit_message_text(f"درخواست {name} بررسی شد.\n{decision_text}")

    try:
        if allow:
            await context.bot.send_message(
                chat_id=int(uid),
                text="✅ به شما اجازه‌ی استفاده از ربات داده شد. برای شروع /start رو بزن."
            )
        else:
            await context.bot.send_message(
                chat_id=int(uid),
                text="⛔ درخواست شما برای استفاده از ربات رد شد."
            )
    except Exception as e:
        print(f"⚠️ اطلاع‌رسانی به کاربر {uid} با خطا مواجه شد: {e}")

# ==================== پایان بخش ادمین ====================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    if not is_allowed(user.id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 درخواست اجازه استفاده", callback_data="request_access")]
        ])
        await update.message.reply_text(
            "⛔ شما اجازه استفاده از این ربات رو ندارید.",
            reply_markup=keyboard
        )
        return

    context.user_data["mode"] = None
    context.user_data["connect_images"] = []
    context.user_data.pop("connect_status_msg_id", None)
    context.user_data["bulk_files"] = []
    context.user_data.pop("bulk_status_msg_id", None)
    await update.message.reply_text(
        "یکی از حالت‌ها رو انتخاب کن:",
        reply_markup=main_menu()
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # برای اینکه راحت آیدی عددی هر کسی رو بفهمی و به لیست اضافه کنی
    await update.message.reply_text(f"آیدی عددی شما: {update.effective_user.id}")


async def handle_connect_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    images_list = context.user_data.get("connect_images", [])

    if not images_list:
        await query.answer("هنوز عکسی نفرستادی.", show_alert=True)
        return

    await query.edit_message_text(f"⏳ در حال ساخت PDF از {len(images_list)} عکس...")

    sorted_entries = sorted(images_list, key=lambda entry: _natural_sort_key(entry[0]))
    pdf_bytes = await asyncio.to_thread(images_bytes_to_pdf, sorted_entries)

    context.user_data["mode"] = None
    context.user_data["connect_images"] = []
    context.user_data.pop("connect_status_msg_id", None)

    if pdf_bytes is None:
        await query.message.reply_text("❌ نتونستم از عکس‌های فرستاده‌شده PDF بسازم.")
        await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
        return

    await process_and_reply(query.message, "connected.pdf", pdf_bytes, context)


# ==================== بخش آپلود گروهی ====================

async def handle_bulk_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    وقتی کاربر توی حالت «آپلود گروهی» دکمه‌ی «تمام» رو بزنه، این تابع
    همه‌ی فایل‌های جمع‌شده (PDF یا ZIP) رو یکی‌یکی پردازش و به Catbox
    آپلود می‌کنه، بعد نتیجه رو با اسم هر فایل زیر لینکش گزارش می‌ده.
    """
    query = update.callback_query
    files_list = context.user_data.get("bulk_files", [])

    if not files_list:
        await query.answer("هنوز فایلی نفرستادی.", show_alert=True)
        return

    total = len(files_list)

    # یه دکمه‌ی لغو برای کل دسته: یعنی آپلودِ فایل فعلی قطع میشه و بقیه‌ی فایل‌ها
    # آپلود نمیشن (لینک فایل‌هایی که قبلاً آپلود شدن حفظ میشه).
    batch_task_id = _new_task(query.message.chat_id)
    batch_state = _TASKS[batch_task_id]
    batch_kb = cancel_keyboard(batch_task_id)

    await query.edit_message_text(f"⏳ در حال آپلود {total} فایل...", reply_markup=batch_kb)

    results = []  # لیست (label, link یا None, پیام‌خطا یا None)
    batch_cancelled = False

    try:
        for idx, item in enumerate(files_list, start=1):
            if batch_state["cancelled"]:
                batch_cancelled = True
                break

            label = item["label"]
            raw_bytes = item["bytes"]
            kind = item["kind"]

            header = f"⏳ در حال آپلود {idx} از {total}\n(فایل فعلی: {label})"
            try:
                await context.bot.edit_message_text(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=header,
                    reply_markup=batch_kb
                )
            except Exception:
                pass

            if kind == "zip":
                try:
                    pdf_bytes = await asyncio.to_thread(convert_zip_images_to_pdf, raw_bytes)
                except Exception as e:
                    results.append((label, None, f"خطا در تبدیل زیپ: {e}"))
                    continue

                if pdf_bytes is None:
                    results.append((label, None, "هیچ عکسی توی زیپ پیدا نشد یا زیپ خراب بود"))
                    continue

                upload_bytes = pdf_bytes
                upload_filename = f"{label}.pdf"
            else:
                upload_bytes = raw_bytes
                upload_filename = label if label.lower().endswith(".pdf") else f"{label}.pdf"

            try:
                link = await upload_catbox_with_progress(
                    upload_filename, upload_bytes, query.message, prefix=header,
                    task_id=batch_task_id
                )
            except TaskCancelled:
                batch_cancelled = True
                break

            if link:
                results.append((label, link, None))
            else:
                results.append((label, None, "آپلود به Catbox ناموفق بود"))
    finally:
        _end_task(batch_task_id)

    # ساخت پیام‌های نهایی: اسم فایل بالا، لینک (یا خطا) زیرش
    lines = []
    for label, link, error in results:
        if link:
            lines.append(f"📄 <b>{label}</b>\n<code>{link}</code>")
        else:
            lines.append(f"📄 <b>{label}</b>\n⚠️ {error}")

    # پیام‌ها رو به چند تیکه تقسیم می‌کنیم تا از محدودیت طول پیام تلگرام رد نشیم
    chunks = []
    current = ""
    for line in lines:
        candidate = (current + "\n\n" + line) if current else line
        if len(candidate) > 3500:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)

    success_count = sum(1 for _, link, _ in results if link)
    if batch_cancelled:
        header = f"❌ آپلود گروهی لغو شد ({success_count} از {total} فایل تا اینجا آپلود شده بود):"
    else:
        header = f"✅ آپلود گروهی تمام شد ({success_count} از {total} موفق):"

    try:
        await context.bot.edit_message_text(
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=header
        )
    except Exception:
        await query.message.reply_text(header)

    for chunk in chunks:
        await query.message.reply_text(chunk, parse_mode="HTML")

    context.user_data["mode"] = None
    context.user_data["bulk_files"] = []
    context.user_data.pop("bulk_status_msg_id", None)
    await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())

# ==================== پایان بخش آپلود گروهی ====================


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("admin_"):
        await admin_callback_handler(update, context, data)
        return

    if data == "request_access":
        await handle_request_access(update, context)
        return

    if data.startswith("reqallow_") or data.startswith("reqdeny_"):
        await handle_request_decision(update, context, data)
        return

    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await query.answer("⛔ شما اجازه استفاده از این ربات رو ندارید.", show_alert=True)
        return

    # دکمه‌ی «❌ لغو» زیر پیام‌های دریافت/آپلود
    if data.startswith("cancel_task_"):
        state = _TASKS.get(data[len("cancel_task_"):])
        if not state or state["chat_id"] != query.message.chat_id:
            await query.answer("این عملیات دیگه فعال نیست.")
            return
        state["cancelled"] = True
        running = state.get("task")
        if running is not None and not running.done():
            running.cancel()  # دانلودِ در حال انتظار رو همون لحظه قطع می‌کنه
        await query.answer("⏹ در حال لغو...")
        return

    await query.answer()

    if data == "mode_cover":
        context.user_data["mode"] = "cover"
        await query.edit_message_text(
            "حالت «آپلود کاور» فعال شد ✅\nفقط عکس بفرست (هیچ فرمت دیگه‌ای به جز عکس قبول نمی‌کنیم).",
            reply_markup=main_menu()
        )
    elif data == "mode_pdf":
        context.user_data["mode"] = "pdf"
        await query.edit_message_text(
            "حالت «آپلود PDF» فعال شد ✅\nفقط فایل PDF بفرست (هر فایل دیگه‌ای رد میشه).",
            reply_markup=main_menu()
        )
    elif data == "mode_zip":
        context.user_data["mode"] = "zip_to_pdf"
        await query.edit_message_text(
            "حالت «زیپ عکس‌ها به PDF» فعال شد ✅\n"
            "یه فایل ZIP بفرست که توش عکس باشه؛ عکس‌ها به ترتیب اسمشون "
            "توی یه PDF چندصفحه‌ای چسبونده میشن.\n"
            "اگه داخل زیپ یه زیپ یا رار دیگه هم باشه (تا ۳ سطح تودرتو)، "
            "عکس‌های اونم پیدا میشه؛ فایل‌های غیرعکس نادیده گرفته میشن.",
            reply_markup=main_menu()
        )
    elif data == "mode_connect":
        context.user_data["mode"] = "connect"
        context.user_data["connect_images"] = []
        context.user_data.pop("connect_status_msg_id", None)
        await query.edit_message_text(
            "حالت «اتصال عکس‌ها به PDF» فعال شد ✅\n\n"
            "عکس‌ها رو یکی‌یکی، به‌صورت «فایل» (Document) بفرست — نه عکس فشرده، "
            "چون تلگرام موقع فشرده‌سازی اسم فایل رو حذف می‌کنه.\n"
            "اسم هر عکس باید با شماره باشه (مثل 01.jpg، 02.jpg، ...) — بات بر اساس "
            "همین شماره‌ها ترتیب صفحات PDF نهایی رو تعیین می‌کنه، نه ترتیب ارسال.\n\n"
            "وقتی همه رو فرستادی، زیر آخرین عکس دکمه‌ی «✅ تمام» رو بزن."
        )
    elif data == "connect_done":
        async with _user_lock(user_id):
            await handle_connect_done(update, context)
    elif data == "connect_cancel":
        context.user_data["mode"] = None
        context.user_data["connect_images"] = []
        context.user_data.pop("connect_status_msg_id", None)
        await query.edit_message_text("❌ لغو شد.")
        await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
    elif data == "mode_rar":
        context.user_data["mode"] = "rar_to_pdf"
        await query.edit_message_text(
            "حالت «رار عکس‌ها به PDF» فعال شد ✅\n"
            "یه فایل RAR بفرست که توش عکس باشه؛ عکس‌ها به ترتیب اسمشون "
            "توی یه PDF چندصفحه‌ای چسبونده میشن.\n"
            "اگه داخل رار یه زیپ یا رار دیگه هم باشه (تا ۳ سطح تودرتو)، "
            "عکس‌های اونم پیدا میشه؛ فایل‌های غیرعکس نادیده گرفته میشن.",
            reply_markup=main_menu()
        )
    elif data == "mode_bulk":
        context.user_data["mode"] = "bulk_upload"
        context.user_data["bulk_files"] = []
        context.user_data.pop("bulk_status_msg_id", None)
        await query.edit_message_text(
            "حالت «آپلود گروهی» فعال شد ✅\n\n"
            "فایل‌های PDF یا ZIP رو یکی‌یکی بفرست — نوع هر فایل خودکار تشخیص "
            "داده میشه (PDF مستقیم آپلود میشه، ZIP اول به PDF تبدیل میشه).\n\n"
            "وقتی همه رو فرستادی، زیر آخرین فایل دکمه‌ی «✅ تمام» رو بزن تا "
            "همه یکی‌یکی لینک بشن (اسم هر فایل بالای لینکش نوشته میشه)."
        )
    elif data == "bulk_done":
        async with _user_lock(user_id):
            await handle_bulk_done(update, context)
    elif data == "bulk_cancel":
        context.user_data["mode"] = None
        context.user_data["bulk_files"] = []
        context.user_data.pop("bulk_status_msg_id", None)
        await query.edit_message_text("❌ لغو شد.")
        await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())


async def process_and_reply(msg, filename, file_bytes, context: ContextTypes.DEFAULT_TYPE, status_msg=None):
    status = status_msg or await msg.reply_text("در حال آپلود...")

    cancelled = False
    try:
        link = await upload_catbox_with_progress(
            filename, file_bytes, status, prefix=f"⬆️ در حال آپلود «{filename}»"
        )
    except TaskCancelled:
        cancelled = True
        link = None

    if cancelled:
        await _safe_edit(status, "❌ آپلود لغو شد.")
    elif link:
        # لینک با <code> یعنی با یه تپ روش کپی میشه
        await status.edit_text(
            f"✅ آپلود شد (Catbox)\nلینک مستقیم:\n<code>{link}</code>",
            parse_mode="HTML"
        )
    else:
        await status.edit_text("❌ آپلود ناموفق بود، Catbox جواب نداد.")

    # ریست کردن حالت و نمایش دوباره‌ی منو بعد از هر آپلود
    context.user_data["mode"] = None
    await msg.reply_text(
        "یکی از حالت‌ها رو انتخاب کن:",
        reply_markup=main_menu()
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فایل‌های هر کاربر یکی‌یکی (به‌ترتیب) پردازش میشن، ولی دکمه‌ی لغو آزاده
    async with _user_lock(update.effective_user.id):
        await _handle_file_impl(update, context)


async def _handle_file_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await msg.reply_text("⛔ شما اجازه استفاده از این ربات رو ندارید.")
        return

    mode = context.user_data.get("mode")

    if mode is None:
        await msg.reply_text(
            "اول یکی از حالت‌ها رو انتخاب کن:",
            reply_markup=main_menu()
        )
        return

    if mode == "cover":
        # فقط عکس قبول میشه
        if msg.photo:
            file_src = msg.photo[-1]
            filename = "cover.jpg"
        else:
            await msg.reply_text("⚠️ تو حالت «آپلود کاور» فقط عکس قبول میشه. فایل دیگه‌ای نفرست.")
            return

    elif mode == "pdf":
        # فقط PDF قبول میشه
        is_pdf = (
            msg.document
            and (
                msg.document.mime_type == "application/pdf"
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".pdf"))
            )
        )
        if is_pdf:
            file_src = msg.document
            filename = msg.document.file_name or "file.pdf"
        else:
            await msg.reply_text("⚠️ تو حالت «آپلود PDF» فقط فایل PDF قبول میشه. فایل دیگه‌ای نفرست.")
            return

    elif mode == "zip_to_pdf":
        # فقط ZIP قبول میشه
        is_zip = (
            msg.document
            and (
                msg.document.mime_type in ("application/zip", "application/x-zip-compressed")
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".zip"))
            )
        )
        if not is_zip:
            await msg.reply_text("⚠️ تو حالت «زیپ به PDF» فقط فایل ZIP قبول میشه. فایل دیگه‌ای نفرست.")
            return

        status = await msg.reply_text("⬇️ در حال دریافت فایل زیپ...")
        try:
            zip_bytes = await download_with_progress(
                msg.document, status, msg.document.file_size, prefix="⬇️ در حال دریافت فایل زیپ..."
            )
            await status.edit_text("🛠 در حال استخراج عکس‌ها و ساخت PDF...")
            pdf_bytes = await asyncio.to_thread(convert_zip_images_to_pdf, zip_bytes)
        except TaskCancelled:
            await _safe_edit(status, "❌ دریافت فایل لغو شد.")
            context.user_data["mode"] = None
            await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
            return
        except Exception as e:
            await status.edit_text(f"❌ خطایی توی پردازش زیپ پیش اومد: {e}")
            context.user_data["mode"] = None
            await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
            return

        if pdf_bytes is None:
            await status.edit_text(
                "❌ هیچ عکسی توی فایل زیپ (یا آرشیوهای تودرتوش) پیدا نشد یا فایل زیپ خراب بود."
            )
            context.user_data["mode"] = None
            await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
            return

        base_name = os.path.splitext(msg.document.file_name or "converted")[0]
        await process_and_reply(msg, f"{base_name}.pdf", pdf_bytes, context, status_msg=status)
        return

    elif mode == "rar_to_pdf":
        # فقط RAR قبول میشه
        is_rar = (
            msg.document
            and (
                (msg.document.mime_type or "") in (
                    "application/vnd.rar",
                    "application/x-rar-compressed",
                    "application/x-rar",
                )
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".rar"))
            )
        )
        if not is_rar:
            await msg.reply_text("⚠️ تو حالت «رار به PDF» فقط فایل RAR قبول میشه. فایل دیگه‌ای نفرست.")
            return

        status = await msg.reply_text("⬇️ در حال دریافت فایل رار...")
        try:
            rar_bytes = await download_with_progress(
                msg.document, status, msg.document.file_size, prefix="⬇️ در حال دریافت فایل رار..."
            )
            await status.edit_text("🛠 در حال استخراج عکس‌ها و ساخت PDF...")
            pdf_bytes = await asyncio.to_thread(convert_rar_images_to_pdf, rar_bytes)
        except TaskCancelled:
            await _safe_edit(status, "❌ دریافت فایل لغو شد.")
            context.user_data["mode"] = None
            await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
            return
        except Exception as e:
            await status.edit_text(f"❌ خطایی توی پردازش رار پیش اومد: {e}")
            context.user_data["mode"] = None
            await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
            return

        if pdf_bytes is None:
            await status.edit_text(
                "❌ هیچ عکسی توی فایل رار (یا آرشیوهای تودرتوش) پیدا نشد یا فایل رار خراب بود."
            )
            context.user_data["mode"] = None
            await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
            return

        base_name = os.path.splitext(msg.document.file_name or "converted")[0]
        await process_and_reply(msg, f"{base_name}.pdf", pdf_bytes, context, status_msg=status)
        return
        return

    elif mode == "connect":
        # این حالت عکس (به‌صورت فایل/Document ترجیحاً، یا عکس فشرده) قبول می‌کنه
        images_list = context.user_data.setdefault("connect_images", [])

        is_image_doc = (
            msg.document
            and msg.document.mime_type
            and msg.document.mime_type.startswith("image/")
        )

        if is_image_doc:
            file_obj = await msg.document.get_file()
            filename = msg.document.file_name or f"image_{len(images_list) + 1:03d}.jpg"
        elif msg.photo:
            # عکس فشرده - اسم اصلی نداره، پس بر اساس ترتیب دریافت اسم می‌ذاریم
            file_obj = await msg.photo[-1].get_file()
            filename = f"photo_{len(images_list) + 1:03d}.jpg"
        else:
            await msg.reply_text(
                "⚠️ تو حالت «اتصال عکس‌ها» فقط عکس (فایل یا عکس فشرده) قبول میشه."
            )
            return

        try:
            file_bytes = bytes(await file_obj.download_as_bytearray())
        except Exception as e:
            await msg.reply_text(f"❌ خطا توی دریافت عکس: {e}")
            return

        images_list.append((filename, file_bytes))

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تمام", callback_data="connect_done"),
                InlineKeyboardButton("❌ انصراف", callback_data="connect_cancel"),
            ]
        ])
        status_text = f"📥 {len(images_list)} عکس دریافت شد. وقتی تموم شد «تمام» رو بزن."

        last_status_id = context.user_data.get("connect_status_msg_id")
        edited = False
        if last_status_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=msg.chat_id,
                    message_id=last_status_id,
                    text=status_text,
                    reply_markup=keyboard,
                )
                edited = True
            except Exception:
                edited = False

        if not edited:
            sent = await msg.reply_text(status_text, reply_markup=keyboard)
            context.user_data["connect_status_msg_id"] = sent.message_id

        return

    elif mode == "bulk_upload":
        # این حالت هم PDF قبول می‌کنه هم ZIP - نوعش خودکار تشخیص داده میشه
        files_list = context.user_data.setdefault("bulk_files", [])

        is_pdf = (
            msg.document
            and (
                msg.document.mime_type == "application/pdf"
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".pdf"))
            )
        )
        is_zip = (
            msg.document
            and (
                msg.document.mime_type in ("application/zip", "application/x-zip-compressed")
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".zip"))
            )
        )

        if not (is_pdf or is_zip):
            await msg.reply_text(
                "⚠️ تو حالت «آپلود گروهی» فقط فایل PDF یا ZIP قبول میشه."
            )
            return

        dl_label = msg.document.file_name or "فایل"
        status = await msg.reply_text(f"⬇️ در حال دریافت {dl_label}...")
        try:
            file_bytes = await download_with_progress(
                msg.document, status, msg.document.file_size, prefix=f"⬇️ در حال دریافت {dl_label}..."
            )
        except TaskCancelled:
            # فقط همین فایل لغو میشه؛ حالت گروهی فعال می‌مونه و می‌تونی فایل بعدی رو بفرستی
            await _safe_edit(status, f"❌ دریافت {dl_label} لغو شد.")
            return
        except Exception as e:
            await status.edit_text(f"❌ خطا توی دریافت فایل: {e}")
            return
        await status.delete()

        original_name = msg.document.file_name or f"file_{len(files_list) + 1:02d}"
        label = os.path.splitext(original_name)[0] or f"file_{len(files_list) + 1:02d}"
        kind = "pdf" if is_pdf else "zip"
        files_list.append({"label": label, "bytes": file_bytes, "kind": kind})

        pdf_count = sum(1 for f in files_list if f["kind"] == "pdf")
        zip_count = sum(1 for f in files_list if f["kind"] == "zip")
        status_text = (
            f"📥 {len(files_list)} فایل دریافت شد ({pdf_count} PDF، {zip_count} ZIP).\n"
            "وقتی تموم شد «تمام» رو بزن."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تمام", callback_data="bulk_done"),
                InlineKeyboardButton("❌ انصراف", callback_data="bulk_cancel"),
            ]
        ])

        last_status_id = context.user_data.get("bulk_status_msg_id")
        edited = False
        if last_status_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=msg.chat_id,
                    message_id=last_status_id,
                    text=status_text,
                    reply_markup=keyboard,
                )
                edited = True
            except Exception:
                edited = False

        if not edited:
            sent = await msg.reply_text(status_text, reply_markup=keyboard)
            context.user_data["bulk_status_msg_id"] = sent.message_id

        return

    else:
        return

    status = None
    try:
        status = await msg.reply_text(f"⬇️ در حال دریافت {filename}...")
        file_bytes = await download_with_progress(
            file_src, status, getattr(file_src, "file_size", None),
            prefix=f"⬇️ در حال دریافت {filename}..."
        )
        await process_and_reply(msg, filename, file_bytes, context, status_msg=status)
    except TaskCancelled:
        if status:
            await _safe_edit(status, "❌ دریافت فایل لغو شد.")
        context.user_data["mode"] = None
        await msg.reply_text(
            "یکی از حالت‌ها رو انتخاب کن:",
            reply_markup=main_menu()
        )
    except Exception as e:
        # هر خطایی که پیش بیاد، ربات کرش نمی‌کنه و به کاربر اطلاع میده
        await msg.reply_text(f"❌ خطایی پیش اومد: {e}")
        context.user_data["mode"] = None
        await msg.reply_text(
            "یکی از حالت‌ها رو انتخاب کن:",
            reply_markup=main_menu()
        )


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    # لاگ کردن خطا بدون کرش کردن ربات - برای اینکه سرویس رایگان هیچ‌وقت متوقف نشه
    print(f"⚠️ خطا رخ داد: {context.error}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده! یه Environment Variable به اسم BOT_TOKEN اضافه کن.")

    if USE_SUPABASE:
        print("✅ ذخیره‌سازی: Supabase (دائمی)")
    else:
        print("⚠️ هشدار: SUPABASE_URL / SUPABASE_SERVICE_KEY ست نشده. داده‌ها روی فایل محلی ذخیره میشن "
              "که روی رندر رایگان با هر ری‌استارت پاک میشه!")

    threading.Thread(target=run_web, daemon=True).start()

    # timeoutهای پیش‌فرض کتابخونه فقط ۵ ثانیه‌ست که برای فایل‌های حجیم
    # (بالای ۲۰ مگ که از Local Bot API Server دانلود/آپلود میشن) کافی
    # نیست و باعث خطای "Timed out" می‌شد. اینجا بیشترشون می‌کنیم.
    custom_request = HTTPXRequest(
        connect_timeout=60,
        read_timeout=120,
        write_timeout=120,
        pool_timeout=60,
    )

    # concurrent_updates: بدون این، تلگرام آپدیت‌ها رو یکی‌یکی پردازش می‌کنه و
    # وقتی بات وسط آپلود/دانلود باشه، کلیکِ دکمه‌ی «❌ لغو» تا تموم شدن کار
    # پردازش نمیشه. (ترتیب فایل‌های هر کاربر با _user_lock حفظ میشه.)
    builder = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(custom_request)
        .concurrent_updates(True)
    )

    if USE_LOCAL_BOT_API:
        print(f"✅ اتصال به Local Bot API Server: {LOCAL_BOT_API_URL}")
        builder = (
            builder
            .base_url(f"{LOCAL_BOT_API_URL}/bot")
            .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
            .local_mode(True)
        )
    else:
        print("⚠️ Local Bot API غیرفعاله - از api.telegram.org استفاده میشه (سقف ۲۰/۵۰ مگابایت).")

    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", myid))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    app.run_polling()


if __name__ == "__main__":
    main()
