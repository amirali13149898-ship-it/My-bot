"""نقطه‌ی ورود ربات"""
import threading
import time
from telegram import Update
from telegram.error import RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

from core.config import (
    BOT_TOKEN,
    IMGUR_CLIENT_ID,
    LOCAL_BOT_API_URL,
    USE_LOCAL_BOT_API,
    USE_SUPABASE,
)
from core.dbcheck import supabase_selftest
from core.keyboards import track_user
from core.telegram_io import _note_flood
from core.webserver import run_web
from handlers.admin import admin_command
from handlers.commands import handle_text, myid, start
from handlers.files import handle_file
from handlers.modes import button_handler


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    # لاگ کردن خطا بدون کرش کردن ربات - برای اینکه سرویس رایگان هیچ‌وقت متوقف نشه
    if isinstance(context.error, RetryAfter):
        _note_flood(context.error)
    print(f"⚠️ خطا رخ داد: {context.error}")


def build_app():
    # timeoutهای پیش‌فرض کتابخونه فقط ۵ ثانیه‌ست که برای فایل‌های حجیم
    # (بالای ۲۰ مگ که از Local Bot API Server دانلود/آپلود میشن) کافی
    # نیست و باعث خطای "Timed out" می‌شد. اینجا بیشترشون می‌کنیم.
    #
    # connection_pool_size: وقتی HTTPXRequest رو دستی می‌سازیم، اندازه‌ی استخر
    # اتصال کوچیکه (پیش‌فرض خودِ کلاس). با concurrent_updates و چند ادمین همزمان،
    # یه get_file طولانی می‌تونست تنها اتصال رو اشغال کنه و بقیه‌ی درخواست‌ها
    # (ادیت پیشرفت، ارسال پیام، دانلود ادمین‌های دیگه) پشت سرش صف بکشن و
    # با خطای Pool timeout بمیرن. اینجا صراحتاً بزرگش می‌کنیم.
    custom_request = HTTPXRequest(
        connection_pool_size=256,
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
    app.add_handler(TypeHandler(Update, track_user), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", myid))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.ANIMATION, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    return app


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده! یه Environment Variable به اسم BOT_TOKEN اضافه کن.")

    if USE_SUPABASE:
        print("✅ ذخیره‌سازی: Supabase (دائمی)")
    else:
        print("⚠️ هشدار: SUPABASE_URL / SUPABASE_SERVICE_KEY ست نشده. داده‌ها روی فایل محلی ذخیره میشن "
              "که روی رندر رایگان با هر ری‌استارت پاک میشه!")

    if IMGUR_CLIENT_ID:
        print("✅ کاورها روی Imgur آپلود میشن (بدون فیلتر توی ایران).")
    else:
        print("⚠️ IMGUR_CLIENT_ID ست نشده - کاورها فعلاً روی Catbox آپلود میشن.")

    supabase_selftest()
    threading.Thread(target=run_web, daemon=True).start()

    # اگه تلگرام همون اول (initialize/get_me/getUpdates) RetryAfter بده، قبلاً
    # بات کرش می‌کرد و رندر دائم ری‌استارتش می‌کرد. اینجا صبر می‌کنیم و دوباره امتحان می‌کنیم.
    while True:
        app = build_app()
        try:
            app.run_polling()
            break
        except RetryAfter as e:
            _note_flood(e)
            wait = getattr(e, "retry_after", 30)
            try:
                wait = wait.total_seconds()
            except AttributeError:
                pass
            wait = int(wait) + 5
            print(f"🚦 Flood control موقع استارت. {wait} ثانیه صبر می‌کنم و دوباره امتحان می‌کنم...")
            time.sleep(wait)


if __name__ == "__main__":
    main()
