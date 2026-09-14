import requests
import re
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, filters,
    CommandHandler, CallbackQueryHandler
)

# ===== تنظیمات - این سه تا رو با مقادیر خودت جایگزین کن =====
BOT_TOKEN = "YOUR_BOT_TOKEN"
IA_ACCESS_KEY = "YOUR_ARCHIVE_ACCESS_KEY"
IA_SECRET_KEY = "YOUR_ARCHIVE_SECRET_KEY"
# ============================================================

CATBOX_API = "https://catbox.moe/user/api.php"


def make_identifier(filename: str) -> str:
    base = re.sub(r'[^a-zA-Z0-9._-]', '-', filename)
    return f"{base}-{int(time.time())}"


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


def upload_archive(filename, file_bytes):
    try:
        identifier = make_identifier(filename)
        upload_url = f"https://s3.us.archive.org/{identifier}/{filename}"
        headers = {
            "authorization": f"LOW {IA_ACCESS_KEY}:{IA_SECRET_KEY}",
            "x-archive-meta01-collection": "opensource",
            "x-archive-meta-mediatype": "texts",
            "x-archive-auto-make-bucket": "1",
        }
        r = requests.put(upload_url, headers=headers, data=bytes(file_bytes), timeout=120)
        if r.status_code in (200, 201):
            return f"https://archive.org/download/{identifier}/{filename}"
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


async def process_and_reply(msg, filename, file_bytes, context):
    status = await msg.reply_text("در حال آپلود...")

    link = upload_catbox(filename, file_bytes)
    source = "Catbox"
    if not link:
        link = upload_archive(filename, file_bytes)
        source = "Archive.org"

    if link:
        # لینک با <code> یعنی با یه تپ روش کپی میشه
        await status.edit_text(
            f"✅ آپلود شد ({source})\nلینک مستقیم:\n<code>{link}</code>",
            parse_mode="HTML"
        )
    else:
        await status.edit_text("❌ آپلود ناموفق بود، هر دو سرویس جواب ندادن.")


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

    file_bytes = await file_obj.download_as_bytearray()
    await process_and_reply(msg, filename, file_bytes, context)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.run_polling()


if __name__ == "__main__":
    main()
