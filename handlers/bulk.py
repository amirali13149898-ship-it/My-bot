"""آپلود گروهی"""
import gc
import html
import os
import re
import tempfile
import uuid
from telegram import Update
from telegram.ext import ContextTypes

from core.keyboards import main_menu
from core.tasks import (
    TaskCancelled,
    _TASKS,
    _end_task,
    _new_task,
    _run_heavy,
    cancel_keyboard,
)
from core.telegram_io import upload_catbox_with_progress
from services.converters import convert_zip_images_to_pdf


def _label_html(label):
    """
    اسم فایل رو برای نمایش HTML آماده می‌کنه: متن معمولی بولد میشه و هر عددی
    توی اسم داخل <code> میره تا با یه تپ کپی بشه (مثلاً «Chapter 12» ->
    Chapter و 12 قابل‌کپی). کاراکترهای خاص HTML escape میشن.
    """
    parts = re.split(r"(\d+(?:\.\d+)?)", label)
    out = []
    for part in parts:
        if not part:
            continue
        esc = html.escape(part)
        if re.fullmatch(r"\d+(?:\.\d+)?", part):
            out.append(f"<code>{esc}</code>")
        else:
            out.append(f"<b>{esc}</b>")
    return "".join(out) or html.escape(label)


# ===== آپلود گروهی: فایل‌ها روی دیسک موقت نگه داشته میشن نه توی رم =====
_BULK_TMP_DIR = os.path.join(tempfile.gettempdir(), "bot_bulk")


def _spool_bytes(data):
    os.makedirs(_BULK_TMP_DIR, exist_ok=True)
    path = os.path.join(_BULK_TMP_DIR, uuid.uuid4().hex)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _remove_quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _clear_bulk(user_data):
    for item in user_data.get("bulk_files", []) or []:
        if item.get("path"):
            _remove_quiet(item["path"])
    user_data["bulk_files"] = []


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

            raw_bytes = upload_bytes = pdf_bytes = None
            gc.collect()

            label = item["label"]
            kind = item["kind"]
            try:
                with open(item["path"], "rb") as f:
                    raw_bytes = f.read()
            except OSError:
                results.append((label, None, "فایل موقت پیدا نشد (سرور ری‌استارت شده؛ دوباره بفرست)"))
                continue
            _remove_quiet(item["path"])

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
                    pdf_bytes = await _run_heavy(convert_zip_images_to_pdf, raw_bytes)
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
    _clear_bulk(context.user_data)
    context.user_data.pop("bulk_status_msg_id", None)
    await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())

# ==================== پایان بخش آپلود گروهی ====================
