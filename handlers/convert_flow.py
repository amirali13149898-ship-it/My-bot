"""جریان تبدیل به PDF و آپلود"""
import os

from core.keyboards import main_menu
from core.tasks import TaskCancelled, _run_heavy
from core.telegram_io import _safe_edit, download_with_progress
from handlers.reply import process_and_reply
from services.converters import (
    IMAGE_EXTENSIONS,
    _archive_to_pdf_with_stats,
    _describe_stats,
    images_bytes_to_pdf,
)


def _detect_convert_kind(msg):
    """
    برای حالت «تغییر فرمت به PDF»: نوع فایل ارسالی رو تشخیص میده.
    خروجی یکی از 'pdf' / 'zip' / 'rar' / 'image' / None (فرمت پشتیبانی‌نشده).
    """
    if msg.photo:
        return "image"
    if msg.document:
        name = (msg.document.file_name or "").lower()
        mime = msg.document.mime_type or ""
        if mime == "application/pdf" or name.endswith(".pdf"):
            return "pdf"
        if mime in ("application/zip", "application/x-zip-compressed") or name.endswith((".zip", ".cbz")):
            return "zip"
        if mime in ("application/vnd.rar", "application/x-rar-compressed", "application/x-rar") or name.endswith((".rar", ".cbr")):
            return "rar"
        if mime.startswith("image/") or name.endswith(IMAGE_EXTENSIONS):
            return "image"
    return None


async def _convert_to_pdf_and_upload(msg, context, kind, file_src, filename):
    """
    برای حالت «تغییر فرمت به PDF»: فایل رو دانلود می‌کنه، اگه لازم باشه
    (zip/rar/image) به PDF تبدیل می‌کنه، و نتیجه رو آپلود می‌کنه. اگه از
    قبل PDF باشه، مستقیم (بدون تبدیل) آپلود میشه.
    """
    label = {"zip": "زیپ", "rar": "رار", "image": "عکس", "pdf": "PDF"}[kind]
    status = await msg.reply_text(f"⬇️ در حال دریافت فایل {label}...")
    try:
        raw_bytes = await download_with_progress(
            file_src, status, getattr(file_src, "file_size", None),
            prefix=f"⬇️ در حال دریافت فایل {label}..."
        )
    except TaskCancelled:
        await _safe_edit(status, "❌ دریافت فایل لغو شد.")
        context.user_data["mode"] = None
        await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
        return

    if kind == "pdf":
        # از قبل PDFه، نیازی به تبدیل نیست
        await process_and_reply(msg, filename, raw_bytes, context, status_msg=status)
        return

    await status.edit_text("🛠 در حال تبدیل به PDF...")
    note = None
    stats = None
    try:
        if kind in ("zip", "rar"):
            # پسوند/نوع اعلام‌شده فقط یه حدسه؛ نوع واقعیِ آرشیو (و آرشیوهای
            # تودرتوش) داخل خودِ تابع از روی محتوا تشخیص داده میشه.
            pdf_bytes, stats = await _run_heavy(_archive_to_pdf_with_stats, raw_bytes, kind)
            note = _describe_stats(stats)
        elif raw_bytes[:4] == b"%PDF":
            # خیلی از فایل‌های .ai جدید در واقع یه PDF معتبرن (نسخه‌ی
            # PDF-compatible ایلوستریتور) - نیازی به تبدیل نیست، مستقیم پاس داده میشه
            pdf_bytes = raw_bytes
        else:  # image (شامل عکس معمولی، HEIC/AVIF، SVG، RAW، یا AI/EPS قدیمی)
            pdf_bytes = await _run_heavy(images_bytes_to_pdf, [(filename, raw_bytes)])
    except Exception as e:
        await status.edit_text(f"❌ خطایی توی تبدیل به PDF پیش اومد: {e}")
        context.user_data["mode"] = None
        await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
        return

    if pdf_bytes is None:
        msg_text = "❌ هیچ عکس یا PDF معتبری توی فایل (یا آرشیوهای تودرتوش) پیدا نشد، یا فایل خراب بود."
        if stats and stats["bad_archives"]:
            msg_text += "\n(یه آرشیو خراب یا رمزدار بود و باز نشد.)"
        await status.edit_text(msg_text)
        context.user_data["mode"] = None
        await msg.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
        return

    base_name = os.path.splitext(filename)[0]
    await process_and_reply(msg, f"{base_name}.pdf", pdf_bytes, context, status_msg=status, note=note)
