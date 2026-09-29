"""حالت‌ها و هندلر دکمه‌ها"""
from telegram import Update
from telegram.ext import ContextTypes

from core.keyboards import main_menu
from core.tasks import _TASKS, _user_lock
from core.users import is_allowed
from handlers.access import handle_request_access, handle_request_decision
from handlers.admin import admin_callback_handler
from handlers.bulk import _clear_bulk, handle_bulk_done
from handlers.connect import handle_connect_done


MODE_TEXTS = {
    "cover": (
        "حالت «آپلود کاور» فعال شد ✅\nفقط عکس بفرست (هیچ فرمت دیگه‌ای به جز عکس قبول نمی‌کنیم)."
    ),
    "pdf": (
        "حالت «آپلود PDF» فعال شد ✅\nفقط فایل PDF بفرست (هر فایل دیگه‌ای رد میشه)."
    ),
    "to_pdf": (
        "حالت «تغییر فرمت به PDF» فعال شد ✅\n\n"
        "هر کدوم از این‌ها رو بفرستی، خودکار تشخیص داده میشه:\n"
        "📦 ZIP یا 🗜️ RAR (یا CBZ/CBR) — هر ترکیبی از عکس و PDF داخلش رو، به ترتیب اسم، "
        "توی یه PDF واحد می‌چسبونیم. اگه داخل زیپ یه رار باشه (یا برعکس) "
        "پوسته‌ی بیرونی رو کنار میزنیم و محتوای داخلی رو برمی‌داریم؛ تا ۳ سطح "
        "تودرتو، حتی اگه پسوند آرشیو داخلی عوض شده باشه\n"
        "🖼 یه عکس تکی (به یه PDF تک‌صفحه‌ای تبدیل میشه)\n"
        "📄 یه فایل PDF (چون از قبل PDFه، فقط مستقیم آپلود میشه)"
    ),
    "connect": (
        "حالت «اتصال عکس‌ها به PDF» فعال شد ✅\n\n"
        "عکس‌ها رو یکی‌یکی، به‌صورت «فایل» (Document) بفرست — نه عکس فشرده، "
        "چون تلگرام موقع فشرده‌سازی اسم فایل رو حذف می‌کنه.\n"
        "اسم هر عکس باید با شماره باشه (مثل 01.jpg، 02.jpg، ...) — بات بر اساس "
        "همین شماره‌ها ترتیب صفحات PDF نهایی رو تعیین می‌کنه، نه ترتیب ارسال.\n\n"
        "وقتی همه رو فرستادی، زیر آخرین عکس دکمه‌ی «✅ تمام» رو بزن."
    ),
    "archive_fix": (
        "حالت «اصلاح و آپلود آرشیو» فعال شد ✅\n\n"
        "فایل ZIP / RAR / 7z (یا هر فایلی که اسمش با محتواش نمی‌خونه) رو به‌صورت «فایل» بفرست.\n"
        "به PDF تبدیل نمیشه و لینک هم نمیده؛ خودِ فایل خام برات فرستاده میشه:\n"
        "🔧 نوع واقعی از روی محتوا تشخیص داده میشه و با پسوند درست فرستاده میشه "
        "(مثلاً زیپی که در اصل رار بوده → .rar)\n"
        "🔓 اگه آرشیو فقط یه پوسته بود (زیپ توی رار، رار توی زیپ و ...)، "
        "پوسته‌ی بیرونی کنار زده میشه و همون آرشیو داخلی به‌صورت فایل برات میاد\n\n"
        "می‌تونی پشت‌سرهم چند فایل بفرستی."
    ),
    "bulk_upload": (
        "حالت «آپلود گروهی» فعال شد ✅\n\n"
        "فایل‌های PDF یا ZIP رو یکی‌یکی (یا پشت‌سرهم) بفرست. هر فایل همون لحظه "
        "پردازش میشه: ZIP اول به PDF تبدیل میشه، بعد آپلود میشه و لینکش زیر "
        "همون فایل با اسمش میاد، بعد نوبت فایل بعدیه.\n\n"
        "برای خروج از این حالت /start رو بزن."
    ),
}

_LEGACY_CALLBACK_TO_MODE = {
    "mode_cover": "cover",
    "mode_pdf": "pdf",
    "mode_to_pdf": "to_pdf",
    "mode_connect": "connect",
    "mode_archive": "archive_fix",
    "mode_bulk": "bulk_upload",
}


async def activate_mode(msg, context, mode):
    """حالت انتخاب‌شده رو فعال می‌کنه و پیام توضیح رو می‌فرسته (با کیبورد کپشنی)."""
    ud = context.user_data
    ud["mode"] = mode
    if mode == "connect":
        ud["connect_images"] = []
        ud.pop("connect_status_msg_id", None)
    elif mode == "bulk_upload":
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

    # دکمه‌های شیشه‌ای قدیمیِ منو (پیام‌های قدیمی که هنوز توی چت مونده)
    if data in _LEGACY_CALLBACK_TO_MODE:
        await activate_mode(query.message, context, _LEGACY_CALLBACK_TO_MODE[data])
    elif data == "connect_done":
        async with _user_lock(user_id):
            await handle_connect_done(update, context)
    elif data == "connect_cancel":
        context.user_data["mode"] = None
        context.user_data["connect_images"] = []
        context.user_data.pop("connect_status_msg_id", None)
        await query.edit_message_text("❌ لغو شد.")
        await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
    elif data == "mode_menu":
        context.user_data["mode"] = None
        await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
    elif data == "bulk_done":
        async with _user_lock(user_id):
            await handle_bulk_done(update, context)
    elif data == "bulk_cancel":
        context.user_data["mode"] = None
        _clear_bulk(context.user_data)
        context.user_data.pop("bulk_status_msg_id", None)
        await query.edit_message_text("❌ لغو شد.")
        await query.message.reply_text("یکی از حالت‌ها رو انتخاب کن:", reply_markup=main_menu())
