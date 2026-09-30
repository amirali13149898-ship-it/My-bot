"""حالت‌ها و هندلر دکمه‌ها"""
from telegram import Update
from telegram.ext import ContextTypes

from core.keyboards import main_menu
from core.tasks import _TASKS, _user_lock
from core.users import is_allowed
from handlers.access import handle_request_access, handle_request_decision
from handlers.admin import admin_callback_handler
from handlers.bulk import _clear_bulk, handle_bulk_done
from handlers.locked import _clear_locked


MODE_TEXTS = {
    "single": (
        "حالت «آپلود تکی» فعال شد ✅\n\n"
        "یه فایل بفرست، نوعش خودکار تشخیص داده میشه:\n"
        "🖼 عکس ← مستقیم آپلود میشه و لینک میگیری\n"
        "📄 PDF ← مستقیم آپلود میشه\n"
        "📦 ZIP / 🗜️ RAR (یا CBZ/CBR) ← عکس‌ها و PDFهای داخلش به ترتیب اسم "
        "توی یه PDF میرن و آپلود میشن؛ آرشیو تودرتو یا با پسوند اشتباه هم مشکلی نیست\n"
        "🔄 فرمت‌های خاص (HEIC، SVG، RAW و ...) ← اول به PDF تبدیل میشن\n\n"
        "چند تا عکس رو می‌خوای به یه PDF وصل کنی؟ زیپشون کن و همون رو بفرست."
    ),
    "bulk_upload": (
        "حالت «آپلود گروهی» فعال شد ✅\n\n"
        "فایل‌های PDF، ZIP یا عکس (به‌صورت فایل) رو یکی‌یکی (یا پشت‌سرهم) بفرست. "
        "هر فایل همون لحظه پردازش میشه: ZIP اول به PDF تبدیل میشه، بعد آپلود "
        "میشه و لینکش زیر همون فایل با اسمش میاد، بعد نوبت فایل بعدیه.\n\n"
        "برای خروج از این حالت /start رو بزن."
    ),
    "locked": (
        "حالت «فایل رمزدار» فعال شد 🔐\n\n"
        "یه فایل PDF، ZIP یا RAR بفرست. اگه رمز داشته باشه رمزش رو ازت می‌پرسم؛ "
        "اگه رمز درست بود:\n"
        "📄 PDF ← رمزش برداشته میشه و آپلود میشه\n"
        "📦 ZIP / 🗜️ RAR ← باز میشه، عکس‌ها و PDFهای داخلش توی یه PDF میرن و آپلود میشن\n\n"
        "اگه رمز اشتباه باشه بهت خبر میدم و می‌تونی دوباره رمز بفرستی. "
        "برای انصراف /start رو بزن."
    ),
}

# دکمه‌های شیشه‌ایِ قدیمیِ منو (پیام‌های قدیمی که هنوز توی چت مونده)
_LEGACY_CALLBACK_TO_MODE = {
    "mode_cover": "single",
    "mode_pdf": "single",
    "mode_to_pdf": "single",
    "mode_connect": "single",
    "mode_archive": "single",
    "mode_bulk": "bulk_upload",
}


async def activate_mode(msg, context, mode):
    """حالت انتخاب‌شده رو فعال می‌کنه و پیام توضیح رو می‌فرسته (با کیبورد کپشنی)."""
    ud = context.user_data
    ud["mode"] = mode
    _clear_locked(ud)
    if mode == "bulk_upload":
        _clear_bulk(ud)
        ud.pop("bulk_status_msg_id", None)
    await msg.reply_text(MODE_TEXTS[mode], reply_markup=main_menu())


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

    if data in _LEGACY_CALLBACK_TO_MODE:
        await activate_mode(query.message, context, _LEGACY_CALLBACK_TO_MODE[data])
    elif data == "mode_menu":
        context.user_data["mode"] = None
        await query.message.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu())
    elif data == "bulk_done":
        async with _user_lock(user_id):
            await handle_bulk_done(update, context)
    elif data == "bulk_cancel":
        context.user_data["mode"] = None
        _clear_bulk(context.user_data)
        context.user_data.pop("bulk_status_msg_id", None)
        await query.edit_message_text("❌ لغو شد.")
        await query.message.reply_text("یکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu())
