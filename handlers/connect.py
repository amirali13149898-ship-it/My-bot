"""اتصال عکس‌ها به PDF"""
from telegram import Update
from telegram.ext import ContextTypes

from core.keyboards import main_menu
from core.tasks import _run_heavy
from handlers.reply import process_and_reply
from services.converters import _natural_sort_key, images_bytes_to_pdf


async def handle_connect_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    images_list = context.user_data.get("connect_images", [])

    if not images_list:
        await query.answer("هنوز عکسی نفرستادی.", show_alert=True)
        return

    await query.edit_message_text(f"⏳ در حال ساخت PDF از {len(images_list)} عکس...")

    sorted_entries = sorted(images_list, key=lambda entry: _natural_sort_key(entry[0]))
    pdf_bytes = await _run_heavy(images_bytes_to_pdf, sorted_entries)

    context.user_data["mode"] = None
    context.user_data["connect_images"] = []
    context.user_data.pop("connect_status_msg_id", None)

    if pdf_bytes is None:
        await query.message.reply_text("❌ نتونستم از عکس‌های فرستاده‌شده PDF بسازم.")
        await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
        return

    await process_and_reply(query.message, "connected.pdf", pdf_bytes, context)
