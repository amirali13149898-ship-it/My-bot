import logging
import json
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ═══════════════════════════════════════════════════════
#  ⚙️  تنظیمات — فقط اینجا رو ویرایش کن
# ═══════════════════════════════════════════════════════
BOT_TOKEN  = "8601670090:AAGC9fHd2EJMQvntxR7DHiRBwwc4wEOSAjk"
ADMIN_IDS  = [8552098001]                       # آیدی عددی ادمین (از @userinfobot بگیر)
DATA_FILE  = "anime_list.json"                 # فایل دیتابیس انیمه‌ها
STATS_FILE = "stats.json"                      # فایل آمار دانلودها

# زمان پیش‌فرض پاک شدن فایل (ثانیه) — ادمین می‌تونه عوضش کنه
DEFAULT_DELETE_SECONDS = 24 * 3600            # ۲۴ ساعت

# ═══════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ───────────────────────────────────────────
# توابع کمکی — دیتابیس
# ───────────────────────────────────────────
def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_stats() -> dict:
    if not os.path.exists(STATS_FILE):
        return {}
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_stats(stats: dict) -> None:
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def load_settings() -> dict:
    if not os.path.exists("settings.json"):
        return {"delete_after": DEFAULT_DELETE_SECONDS}
    with open("settings.json", "r", encoding="utf-8") as f:
        return json.load(f)

def save_settings(settings: dict) -> None:
    with open("settings.json", "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def normalize(text: str) -> str:
    return text.strip().lower()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def increment_stat(anime_name: str) -> None:
    stats = load_stats()
    stats[anime_name] = stats.get(anime_name, 0) + 1
    save_stats(stats)


# ───────────────────────────────────────────
# پاک کردن خودکار پیام بعد از X ثانیه
# ───────────────────────────────────────────
async def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int):
    """بعد از delay ثانیه پیام رو پاک کن"""
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"پیام {message_id} در چت {chat_id} پاک شد.")
    except Exception as e:
        logger.warning(f"نتونستم پیام {message_id} رو پاک کنم: {e}")


# ───────────────────────────────────────────
# تابع ارسال فایل + زمان‌بندی حذف
# ───────────────────────────────────────────
async def send_anime_file(update_or_query, context: ContextTypes.DEFAULT_TYPE, anime_name: str, entry: dict | list):
    settings = load_settings()
    delete_after = settings.get("delete_after", DEFAULT_DELETE_SECONDS)

    # چت‌آیدی رو پیدا کن (چه از پیام، چه از callback)
    if hasattr(update_or_query, "message") and update_or_query.message:
        chat_id = update_or_query.message.chat_id
        reply_func = update_or_query.message
    else:
        chat_id = update_or_query.message.chat_id
        reply_func = update_or_query.message

    files = entry if isinstance(entry, list) else [entry]
    hours = delete_after // 3600
    minutes = (delete_after % 3600) // 60

    if hours > 0:
        time_str = f"{hours} ساعت"
    else:
        time_str = f"{minutes} دقیقه"

    notice_msg = await reply_func.reply_text(
        f"✅ <b>{anime_name}</b>\n"
        f"⏳ این فایل بعد از <b>{time_str}</b> پاک می‌شه — همین الان دانلود کن!",
        parse_mode="HTML",
    )
    asyncio.create_task(schedule_delete(context, chat_id, notice_msg.message_id, delete_after))

    for file_info in files:
        file_id   = file_info.get("file_id")
        file_type = file_info.get("type", "document")
        caption   = file_info.get("caption", anime_name)

        try:
            if file_type == "video":
                sent = await reply_func.reply_video(video=file_id, caption=caption)
            elif file_type == "audio":
                sent = await reply_func.reply_audio(audio=file_id, caption=caption)
            else:
                sent = await reply_func.reply_document(document=file_id, caption=caption)

            # زمان‌بندی پاک شدن فایل
            asyncio.create_task(schedule_delete(context, chat_id, sent.message_id, delete_after))
        except Exception as e:
            logger.error(f"خطا در ارسال فایل: {e}")
            await reply_func.reply_text(f"⚠️ خطا در ارسال فایل: {e}")

    increment_stat(anime_name)


# ───────────────────────────────────────────
# دستورات کاربر
# ───────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("📋 لیست انیمه‌ها", callback_data="show_list")]]
    await update.message.reply_text(
        "👋 سلام!\n\n"
        "🎌 اسم انیمه مورد نظرت رو بفرست یا از لیست انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def list_animes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = load_data()
    if not data:
        await update.message.reply_text("❌ هنوز هیچ انیمه‌ای اضافه نشده.")
        return

    keyboard = []
    for name in sorted(data.keys()):
        keyboard.append([InlineKeyboardButton(f"🎌 {name}", callback_data=f"get:{name}")])

    await update.message.reply_text(
        "📋 <b>لیست انیمه‌های موجود:</b>\n\nیکی رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دریافت اسم انیمه با متن"""
    user_text = update.message.text.strip()
    data = load_data()
    key = normalize(user_text)

    # جستجوی دقیق
    matched_entry, matched_name = None, None
    for anime_name, entry in data.items():
        if normalize(anime_name) == key:
            matched_entry, matched_name = entry, anime_name
            break

    # جستجوی جزئی
    if not matched_entry:
        results = [(n, e) for n, e in data.items() if key in normalize(n)]
        if len(results) == 1:
            matched_name, matched_entry = results[0]
        elif len(results) > 1:
            keyboard = [[InlineKeyboardButton(f"🎌 {n}", callback_data=f"get:{n}")] for n, _ in results]
            await update.message.reply_text(
                f"🔍 چند نتیجه پیدا شد، کدوم رو می‌خوای؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

    if not matched_entry:
        await update.message.reply_text(
            f"❌ انیمه‌ای با نام «{user_text}» پیدا نشد.\n\n"
            "برای دیدن لیست: /list"
        )
        return

    await send_anime_file(update, context, matched_name, matched_entry)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """مدیریت دکمه‌های شیشه‌ای"""
    query = update.callback_query
    await query.answer()
    data_cb = query.data

    if data_cb == "show_list":
        data = load_data()
        if not data:
            await query.message.reply_text("❌ هنوز هیچ انیمه‌ای اضافه نشده.")
            return
        keyboard = [[InlineKeyboardButton(f"🎌 {n}", callback_data=f"get:{n}")] for n in sorted(data.keys())]
        await query.message.reply_text(
            "📋 <b>لیست انیمه‌ها:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    elif data_cb.startswith("get:"):
        anime_name = data_cb[4:]
        data = load_data()
        if anime_name not in data:
            await query.message.reply_text("❌ این انیمه پیدا نشد.")
            return
        await send_anime_file(query, context, anime_name, data[anime_name])


# ───────────────────────────────────────────
# دستورات ادمین
# ───────────────────────────────────────────
async def add_anime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    اضافه کردن انیمه — فایل رو بفرست، کپشن: /add اسم انیمه
    """
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return

    msg = update.message
    file_obj = msg.video or msg.document or msg.audio

    if not file_obj or not context.args:
        await msg.reply_text(
            "روش استفاده:\n"
            "فایل رو بفرست، در کپشن بنویس:\n"
            "<code>/add اسم انیمه</code>",
            parse_mode="HTML",
        )
        return

    anime_name = " ".join(context.args)
    file_id    = file_obj.file_id
    file_type  = "video" if msg.video else ("audio" if msg.audio else "document")

    data = load_data()
    new_entry = {"file_id": file_id, "type": file_type, "caption": anime_name}

    if anime_name in data:
        existing = data[anime_name]
        data[anime_name] = (existing if isinstance(existing, list) else [existing]) + [new_entry]
    else:
        data[anime_name] = new_entry

    save_data(data)
    await msg.reply_text(f"✅ «{anime_name}» اضافه شد!")


async def delete_anime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """حذف انیمه — /delete اسم انیمه"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return
    if not context.args:
        await update.message.reply_text("استفاده: <code>/delete اسم انیمه</code>", parse_mode="HTML")
        return

    anime_name = " ".join(context.args)
    data = load_data()
    if anime_name not in data:
        await update.message.reply_text(f"❌ «{anime_name}» پیدا نشد.")
        return

    del data[anime_name]
    save_data(data)
    await update.message.reply_text(f"🗑️ «{anime_name}» حذف شد.")


async def set_delete_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    تنظیم زمان پاک شدن فایل (فقط ادمین)
    استفاده: /setdelete <عدد> <واحد>
    واحدها: s (ثانیه), m (دقیقه), h (ساعت), d (روز)
    مثال: /setdelete 12 h   →  ۱۲ ساعت
    """
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "استفاده: <code>/setdelete عدد واحد</code>\n"
            "واحدها: <code>s</code> ثانیه | <code>m</code> دقیقه | <code>h</code> ساعت | <code>d</code> روز\n\n"
            "مثال: <code>/setdelete 24 h</code>",
            parse_mode="HTML",
        )
        return

    try:
        amount = int(context.args[0])
        unit   = context.args[1].lower()
    except ValueError:
        await update.message.reply_text("❌ عدد رو درست وارد کن.")
        return

    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if unit not in multipliers:
        await update.message.reply_text("❌ واحد باید s، m، h یا d باشه.")
        return

    seconds = amount * multipliers[unit]
    settings = load_settings()
    settings["delete_after"] = seconds
    save_settings(settings)

    unit_names = {"s": "ثانیه", "m": "دقیقه", "h": "ساعت", "d": "روز"}
    await update.message.reply_text(
        f"✅ زمان پاک شدن فایل‌ها روی <b>{amount} {unit_names[unit]}</b> تنظیم شد.",
        parse_mode="HTML",
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش آمار دانلود (فقط ادمین)"""
    if not is_admin(update.message.from_user.id):
        await update.message.reply_text("⛔ فقط ادمین.")
        return

    stats = load_stats()
    if not stats:
        await update.message.reply_text("📊 هنوز هیچ دانلودی ثبت نشده.")
        return

    sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)
    lines = ["📊 <b>آمار دانلود انیمه‌ها:</b>\n"]
    for i, (name, count) in enumerate(sorted_stats, 1):
        lines.append(f"{i}. {name} — <b>{count}</b> بار")

    total = sum(stats.values())
    lines.append(f"\n🔢 مجموع: <b>{total}</b> دانلود")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.message.from_user.id):
        return
    settings = load_settings()
    delete_after = settings.get("delete_after", DEFAULT_DELETE_SECONDS)
    hours = delete_after // 3600

    await update.message.reply_text(
        "🔧 <b>دستورات ادمین:</b>\n\n"
        "➕ <b>اضافه کردن انیمه:</b>\n"
        "فایل رو بفرست، کپشن:\n"
        "<code>/add اسم انیمه</code>\n\n"
        "🗑️ <b>حذف انیمه:</b>\n"
        "<code>/delete اسم انیمه</code>\n\n"
        "⏳ <b>تنظیم زمان پاک شدن:</b>\n"
        "<code>/setdelete 24 h</code>\n"
        f"(الان: {hours} ساعت)\n\n"
        "📊 <b>آمار دانلود:</b>\n"
        "<code>/stats</code>\n\n"
        "📋 <b>لیست انیمه‌ها:</b>\n"
        "<code>/list</code>",
        parse_mode="HTML",
    )


# ───────────────────────────────────────────
# اجرا
# ───────────────────────────────────────────
def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    # کاربر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list",  list_animes))

    # ادمین
    app.add_handler(CommandHandler("add",       add_anime))
    app.add_handler(CommandHandler("delete",    delete_anime))
    app.add_handler(CommandHandler("setdelete", set_delete_time))
    app.add_handler(CommandHandler("stats",     stats_command))
    app.add_handler(CommandHandler("adminhelp", admin_help))

    # فایل با کپشن /add
    app.add_handler(MessageHandler(
        (filters.VIDEO | filters.Document.ALL | filters.AUDIO) & filters.CAPTION,
        add_anime,
    ))

    # دکمه‌های شیشه‌ای
    app.add_handler(CallbackQueryHandler(callback_handler))

    # پیام متنی عادی
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("✅ بات شروع به کار کرد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
