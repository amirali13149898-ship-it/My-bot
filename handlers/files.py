"""دریافت فایل‌ها"""
import gc
import html
import os
from telegram import Update
from telegram.ext import ContextTypes

from core.keyboards import main_menu
from core.tasks import TaskCancelled, _run_heavy, _user_lock
from core.telegram_io import (
    _safe_edit,
    download_with_progress,
    upload_catbox_with_progress,
)
from core.users import bump_upload, is_allowed
from handlers.bulk import _label_html
from handlers.convert_flow import _convert_to_pdf_and_upload, _detect_convert_kind
from handlers.locked import handle_locked_file
from handlers.reply import process_and_reply
from services.converters import _archive_to_pdf_with_stats, _describe_stats


# عکس‌هایی که مستقیم (بدون تبدیل) آپلود میشن؛ بقیه‌ی فرمت‌های عکس (HEIC، SVG، RAW و ...) اول PDF میشن
_DIRECT_IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_DIRECT_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


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
            "اول یکی از گزینه‌ها رو انتخاب کن:",
            reply_markup=main_menu()
        )
        return

    if mode == "single":
        await _handle_single(msg, context)
    elif mode == "bulk_upload":
        await _handle_bulk_file(msg, context, user_id)
    elif mode == "locked":
        await handle_locked_file(msg, context)


# ==================== آپلود تکی (تشخیص خودکار نوع فایل) ====================

async def _handle_single(msg, context):
    # عکس فشرده‌ی تلگرام -> مستقیم آپلود (کاور)
    if msg.photo:
        await _download_and_upload(msg, context, msg.photo[-1], "cover.jpg")
        return

    kind = _detect_convert_kind(msg)  # 'pdf' / 'zip' / 'rar' / 'image' / None
    if kind is None:
        await msg.reply_text(
            "⚠️ این نوع فایل پشتیبانی نمیشه. عکس، PDF، ZIP یا RAR (یا CBZ/CBR) بفرست."
        )
        return

    doc = msg.document
    name = doc.file_name or ""

    if kind == "pdf":
        await _download_and_upload(msg, context, doc, name or "file.pdf")

    elif kind in ("zip", "rar"):
        await _convert_to_pdf_and_upload(msg, context, kind, doc, name or f"file.{kind}")

    else:  # image (به‌صورت فایل)
        is_direct = (doc.mime_type or "") in _DIRECT_IMAGE_MIMES or name.lower().endswith(_DIRECT_IMAGE_EXTS)
        if is_direct:
            await _download_and_upload(msg, context, doc, name or "image.jpg")
        else:
            await _convert_to_pdf_and_upload(msg, context, "image", doc, name or "image.jpg")


async def _download_and_upload(msg, context, file_src, filename):
    """فایل رو دانلود می‌کنه و همون‌طور که هست (بدون تبدیل) آپلود می‌کنه."""
    status = None
    try:
        status = await msg.reply_text(f"⬇️ در حال دریافت {filename}...")
        file_bytes = await download_with_progress(
            file_src, status, getattr(file_src, "file_size", None),
            prefix=f"⬇️ در حال دریافت {filename}..."
        )
        await process_and_reply(
            msg, filename, file_bytes, context, status_msg=status,
            host="catbox"
        )
    except TaskCancelled:
        if status:
            await _safe_edit(status, "❌ دریافت فایل لغو شد.")
        context.user_data["mode"] = None
        await msg.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu())
    except Exception as e:
        # هر خطایی که پیش بیاد، ربات کرش نمی‌کنه و به کاربر اطلاع میده
        await msg.reply_text(f"❌ خطایی پیش اومد: {e}")
        context.user_data["mode"] = None
        await msg.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu())


# ==================== آپلود گروهی ====================

async def _handle_bulk_file(msg, context, user_id):
    # هر فایل (PDF، ZIP یا عکس) همون لحظه‌ای که میاد پردازش میشه:
    # دریافت -> (اگه ZIP بود) تبدیل به PDF -> آپلود -> فرستادن لینک -> فایل بعدی.
    # هیچ فایلی توی رم یا دیسک جمع نمیشه، پس دکمه‌ی «تمام» لازم نیست.
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
            or (msg.document.file_name and msg.document.file_name.lower().endswith((".zip", ".cbz")))
        )
    )
    is_rar = (
        msg.document
        and (
            (msg.document.mime_type or "") in ("application/vnd.rar", "application/x-rar-compressed", "application/x-rar")
            or (msg.document.file_name and msg.document.file_name.lower().endswith((".rar", ".cbr")))
        )
    )
    is_img = bool(
        msg.document
        and (
            (msg.document.mime_type or "") in _DIRECT_IMAGE_MIMES
            or (msg.document.file_name or "").lower().endswith(_DIRECT_IMAGE_EXTS)
        )
    )

    if not (is_pdf or is_zip or is_rar or is_img):
        await msg.reply_text("⚠️ تو حالت «آپلود گروهی» فقط PDF، ZIP، RAR یا عکس (PNG/JPG/WEBP/GIF) قبول میشه.")
        return

    original_name = msg.document.file_name or "file"
    label = os.path.splitext(original_name)[0] or "file"
    safe_label = _label_html(label)
    dl_label = original_name

    status = await msg.reply_text(f"⬇️ در حال دریافت {dl_label}...")
    try:
        file_bytes = await download_with_progress(
            msg.document, status, msg.document.file_size, prefix=f"⬇️ در حال دریافت {dl_label}..."
        )
    except TaskCancelled:
        await _safe_edit(status, f"❌ دریافت {dl_label} لغو شد.")
        return
    except Exception as e:
        try:
            await status.edit_text(f"❌ خطا توی دریافت {dl_label}: {e}")
        except Exception:
            pass
        return

    note = ""
    if is_zip or is_rar:
        await _safe_edit(status, f"🛠 در حال تبدیل «{label}» به PDF...")
        try:
            upload_bytes, stats = await _run_heavy(
                _archive_to_pdf_with_stats, file_bytes, "rar" if is_rar else "zip"
            )
        except Exception as e:
            file_bytes = None
            try:
                await status.edit_text(f"📄 {safe_label}\n⚠️ خطا در تبدیل آرشیو: {html.escape(str(e))}", parse_mode="HTML")
            except Exception:
                pass
            return
        file_bytes = None
        if upload_bytes is None:
            try:
                await status.edit_text(
                    f"📄 {safe_label}\n⚠️ هیچ عکس یا PDF معتبری توی آرشیو پیدا نشد یا آرشیو خراب/رمزدار بود",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return
        upload_filename = f"{label}.pdf"
        note = _describe_stats(stats)
    else:
        upload_bytes = file_bytes
        file_bytes = None
        if is_img:
            upload_filename = original_name
        else:
            upload_filename = original_name if original_name.lower().endswith(".pdf") else f"{label}.pdf"

    try:
        link = await upload_catbox_with_progress(
            upload_filename, upload_bytes, status,
            prefix=f"⬆️ در حال آپلود «{upload_filename}»",
        )
    except TaskCancelled:
        upload_bytes = None
        await _safe_edit(status, f"❌ آپلود {label} لغو شد.")
        return
    upload_bytes = None
    gc.collect()

    if link:
        bump_upload(user_id)
        text = f"📄 {safe_label}\n<code>{link}</code>"
        if note:
            text += f"\n\n{html.escape(note)}"
    else:
        text = f"📄 {safe_label}\n⚠️ آپلود به Catbox ناموفق بود"
    try:
        await status.edit_text(text, parse_mode="HTML")
    except Exception:
        try:
            await msg.reply_text(text, parse_mode="HTML")
        except Exception:
            pass
    # حالت گروهی فعال می‌مونه تا فایل بعدی رو بفرستی
