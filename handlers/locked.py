"""حالت «فایل رمزدار»: دریافت فایل، تشخیص رمز، پرسیدن رمز، رمزبرداری و آپلود"""
import gc
import os

from core.keyboards import main_menu
from core.tasks import TaskCancelled, _run_heavy, _user_lock
from core.telegram_io import _safe_edit, download_with_progress
from core.users import is_allowed
from handlers.bulk import _remove_quiet, _spool_bytes
from handlers.reply import process_and_reply
from services.converters import _archive_to_pdf_with_stats, _describe_stats
from services.locked import (
    ToolMissing,
    WrongPassword,
    decrypt_to_plain_zip,
    needs_password,
    sniff_locked_kind,
    unlock_pdf,
)

LOCKED_KEY = "locked_file"  # {"path", "kind", "name"} - فایلِ منتظرِ رمز


def _clear_locked(user_data):
    state = user_data.pop(LOCKED_KEY, None)
    if state and state.get("path"):
        _remove_quiet(state["path"])


def has_pending_locked(user_data):
    return user_data.get("mode") == "locked" and bool(user_data.get(LOCKED_KEY))


async def _back_to_menu(msg, context):
    _clear_locked(context.user_data)
    context.user_data["mode"] = None
    await msg.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu())


def _out_name(name, kind):
    base = os.path.splitext(name or "file")[0] or "file"
    return f"{base}.pdf"


# ==================== مرحله ۱: دریافت فایل ====================

async def handle_locked_file(msg, context):
    """مستقیم از handle_file صدا زده میشه (قفل کاربر از قبل گرفته شده)."""
    _clear_locked(context.user_data)

    if not msg.document:
        await msg.reply_text("⚠️ فایل PDF، ZIP یا RAR رو به‌صورت فایل (Document) بفرست.")
        return

    doc = msg.document
    name = doc.file_name or "file"
    status = await msg.reply_text(f"⬇️ در حال دریافت {name}...")
    try:
        raw = await download_with_progress(
            doc, status, doc.file_size, prefix=f"⬇️ در حال دریافت {name}..."
        )
    except TaskCancelled:
        await _safe_edit(status, "❌ دریافت فایل لغو شد.")
        await _back_to_menu(msg, context)
        return
    except Exception as e:
        await _safe_edit(status, f"❌ خطا توی دریافت فایل: {e}")
        return

    kind = sniff_locked_kind(raw)
    if kind is None:
        await _safe_edit(status, "⚠️ این فایل PDF، ZIP یا RAR نیست. یه فایل دیگه بفرست.")
        return

    try:
        locked = await _run_heavy(needs_password, raw, kind)
    except Exception as e:
        print(f"⚠️ تشخیص رمز شکست خورد (kind={kind}): {e}")
        await _safe_edit(status, "❌ فایل خراب به نظر میاد و باز نشد.")
        return

    if locked:
        path = _spool_bytes(raw)  # روی دیسک نگه می‌داریم تا رم اشغال نشه
        raw = None
        gc.collect()
        context.user_data[LOCKED_KEY] = {"path": path, "kind": kind, "name": name}
        await _safe_edit(status, "🔐 این فایل رمز داره.\nرمزش رو همین‌جا بفرست (پیام رمزت بعد از ارسال پاک میشه).")
        return

    # رمز نداره -> مثل حالت عادی پردازش میشه
    await _safe_edit(status, "ℹ️ این فایل رمز نداره؛ مثل حالت عادی پردازش میشه...")
    try:
        if kind == "pdf":
            await process_and_reply(msg, _out_name(name, kind), raw, context, status_msg=status)
            return
        pdf_bytes, stats = await _run_heavy(_archive_to_pdf_with_stats, raw, kind)
    except Exception as e:
        await _safe_edit(status, f"❌ خطایی توی تبدیل به PDF پیش اومد: {e}")
        await _back_to_menu(msg, context)
        return
    raw = None
    if pdf_bytes is None:
        await _safe_edit(status, "❌ هیچ عکس یا PDF معتبری توی فایل پیدا نشد، یا فایل خراب بود.")
        await _back_to_menu(msg, context)
        return
    await process_and_reply(
        msg, _out_name(name, kind), pdf_bytes, context,
        status_msg=status, note=_describe_stats(stats),
    )


# ==================== مرحله ۲: گرفتن رمز ====================

def _unlock_and_convert(path, kind, password):
    """(pdf_bytes یا None, stats یا None) - اگه رمز غلط باشه WrongPassword پرتاب میشه."""
    with open(path, "rb") as f:
        raw = f.read()
    if kind == "pdf":
        return unlock_pdf(raw, password), None
    plain_zip = decrypt_to_plain_zip(raw, kind, password)
    raw = None
    return _archive_to_pdf_with_stats(plain_zip, "zip")


async def handle_locked_password(update, context):
    user_id = update.effective_user.id
    async with _user_lock(user_id):
        await _password_impl(update, context, user_id)


async def _password_impl(update, context, user_id):
    msg = update.message
    state = context.user_data.get(LOCKED_KEY)
    if not state:
        return
    if not is_allowed(user_id):
        await msg.reply_text("⛔ شما اجازه استفاده از این ربات رو ندارید.")
        return

    password = msg.text or ""
    try:  # رمز توی چت نمونه
        await msg.delete()
    except Exception:
        pass

    status = await msg.reply_text("🔓 در حال بررسی رمز و باز کردن فایل...")

    candidates = [password]
    if password.strip() != password and password.strip():
        candidates.append(password.strip())  # اگه اشتباهی فاصله/اینتر اضافه خورده باشه

    result = None
    try:
        for pw in candidates:
            try:
                result = await _run_heavy(_unlock_and_convert, state["path"], state["kind"], pw)
                break
            except WrongPassword:
                continue
    except ToolMissing:
        await _safe_edit(status, "❌ ابزار باز کردن RAR رمزدار روی سرور نصب نیست (unrar).")
        await _back_to_menu(msg, context)
        return
    except Exception as e:
        print(f"⚠️ رمزبرداری شکست خورد: {e}")
        await _safe_edit(status, f"❌ خطایی موقع باز کردن فایل پیش اومد: {e}")
        await _back_to_menu(msg, context)
        return

    if result is None:
        # فایل نگه داشته میشه تا دوباره رمز بفرسته
        await _safe_edit(status, "❌ رمز اشتباهه. رمز درست رو دوباره بفرست.\n(برای انصراف /start رو بزن.)")
        return

    pdf_bytes, stats = result
    name = state["name"]
    kind = state["kind"]
    _clear_locked(context.user_data)

    if pdf_bytes is None:
        await _safe_edit(status, "✅ رمز درست بود، ولی هیچ عکس یا PDF معتبری توی فایل پیدا نشد.")
        await _back_to_menu(msg, context)
        return

    if kind == "pdf":
        note = "🔓 رمز PDF برداشته شد."
    else:
        note = "🔓 رمز درست بود و فایل باز شد.\n" + (_describe_stats(stats) if stats else "")
    await process_and_reply(
        msg, _out_name(name, kind), pdf_bytes, context,
        status_msg=status, note=note.strip(),
  )
