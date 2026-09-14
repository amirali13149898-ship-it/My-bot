import os
import threading
import requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, filters,
    CommandHandler, CallbackQueryHandler
)

# ===== تنظیمات - توکن از Environment Variable خونده میشه =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# ================================================================

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


def upload_catbox(filename, file_bytes):
    try:
        r = requests.post(
            CATBOX_API,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (filename, bytes(file_bytes))},
            timeout=60,
        )
        if r.status_code == 200 and r.text.startswith("http"):
            return r.text.strip()
    except Exception:
        pass
    return None


def main_menu():
    keyboard = [
        [InlineKeyboardButton("📷 آپلود کاور", callback_data="mode_cover")],
        [InlineKeyboardButton("📄 آپلود PDF", callback_data="mode_pdf")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = None
    await update.message.reply_text(
        "یکی از حالت‌ها رو انتخاب کن:",
        reply_markup=main_menu()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "mode_cover":
        context.user_data["mode"] = "cover"
        await query.edit_message_text(
            "حالت «آپلود کاور» فعال شد ✅\nفقط عکس بفرست (هر عکس دیگه‌ای رد میشه).",
            reply_markup=main_menu()
        )
    elif query.data == "mode_pdf":
        context.user_data["mode"] = "pdf"
        await query.edit_message_text(
            "حالت «آپلود PDF» فعال شد ✅\nفقط فایل PDF بفرست (هر فایل دیگه‌ای رد میشه).",
            reply_markup=main_menu()
        )


async def process_and_reply(msg, filename, file_bytes, context: ContextTypes.DEFAULT_TYPE):
    status = await msg.reply_text("در حال آپلود...")

    link = upload_catbox(filename, file_bytes)

    if link:
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
    msg = update.message
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
            file_obj = await msg.photo[-1].get_file()
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
            file_obj = await msg.document.get_file()
            filename = msg.document.file_name or "file.pdf"
        else:
            await msg.reply_text("⚠️ تو حالت «آپلود PDF» فقط فایل PDF قبول میشه. فایل دیگه‌ای نفرست.")
            return
    else:
        return

    try:
        file_bytes = await file_obj.download_as_bytearray()
        await process_and_reply(msg, filename, file_bytes, context)
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

    threading.Thread(target=run_web, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_error_handler(error_handler)
    app.run_polling()


if __name__ == "__main__":
    main()
