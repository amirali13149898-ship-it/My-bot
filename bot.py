import os
import json
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

# آیدی عددی کاربرهایی که از قبل اجازه استفاده دارن (فقط برای اولین بار / seed)
ALLOWED_USER_IDS = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

# آیدی عددی ادمین‌ها (فقط برای اولین بار / seed - بعدش از فایل خونده میشه)
ADMIN_IDS_SEED = {
    int(uid.strip())
    for uid in os.environ.get("ADMIN_IDS", "").split(",")
    if uid.strip().isdigit()
}
# ================================================================

USERS_FILE = "users_data.json"
LIST_PAGE_SIZE = 6


def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_users(users):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ ذخیره فایل کاربران با خطا مواجه شد: {e}")


# {user_id_str: {first_name, username, allowed, is_admin}}
USERS = load_users()


def register_user(user):
    uid = str(user.id)
    if uid not in USERS:
        USERS[uid] = {
            "first_name": user.first_name or "",
            "username": user.username or "",
            "allowed": user.id in ADMIN_IDS_SEED or user.id in ALLOWED_USER_IDS,
            "is_admin": user.id in ADMIN_IDS_SEED,
        }
    else:
        USERS[uid]["first_name"] = user.first_name or ""
        USERS[uid]["username"] = user.username or ""
        USERS[uid].setdefault("is_admin", user.id in ADMIN_IDS_SEED)
    save_users(USERS)


def is_admin(user_id: int) -> bool:
    info = USERS.get(str(user_id))
    if info is not None:
        return bool(info.get("is_admin", False))
    return user_id in ADMIN_IDS_SEED


def get_admin_ids():
    ids = {int(uid) for uid, info in USERS.items() if info.get("is_admin")}
    ids |= ADMIN_IDS_SEED
    return ids


def is_allowed(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    info = USERS.get(str(user_id))
    if info is not None:
        return bool(info.get("allowed", False))
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

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


def user_display_name(user):
    if user.username:
        return f"{user.first_name or ''} (@{user.username})".strip()
    return user.first_name or str(user.id)


# ==================== بخش ادمین ====================

def admin_menu():
    keyboard = [
        [InlineKeyboardButton("📋 لیست کاربران", callback_data="admin_list_0")],
        [InlineKeyboardButton("🔍 جستجوی آیدی", callback_data="admin_search")],
        [InlineKeyboardButton("👑 مدیریت ادمین‌ها", callback_data="admin_admins_0")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_user_list_keyboard(ids, page, callback_prefix="admin_user_", list_callback_prefix="admin_list_"):
    per_page = LIST_PAGE_SIZE
    total_pages = max(1, (len(ids) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = ids[start:start + per_page]

    keyboard = []
    for uid in chunk:
        info = USERS.get(uid, {})
        status = "✅" if info.get("allowed") else "⛔"
        name = info.get("first_name") or ""
        label = f"{status} {uid}" + (f" - {name}" if name else "")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{callback_prefix}{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"{list_callback_prefix}{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"{list_callback_prefix}{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard), total_pages, page


def build_admin_list_keyboard(ids, page):
    per_page = LIST_PAGE_SIZE
    total_pages = max(1, (len(ids) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = ids[start:start + per_page]

    keyboard = []
    for uid in chunk:
        info = USERS.get(uid, {})
        name = info.get("first_name") or ""
        label = f"👑 {uid}" + (f" - {name}" if name else "")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"admin_admin_detail_{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"admin_admins_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"admin_admins_{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    return InlineKeyboardMarkup(keyboard), total_pages, page


def build_user_detail_keyboard(uid):
    keyboard = [
        [
            InlineKeyboardButton("✅ اجازه بده", callback_data=f"admin_allow_{uid}"),
            InlineKeyboardButton("⛔ اجازه نده", callback_data=f"admin_deny_{uid}"),
        ],
        [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="admin_list_0")],
    ]
    return InlineKeyboardMarkup(keyboard)


def user_detail_text(uid):
    info = USERS.get(uid, {})
    name = info.get("first_name") or "-"
    username = info.get("username") or "-"
    status = "✅ مجاز" if info.get("allowed") else "⛔ غیرمجاز"
    return (
        f"👤 آیدی: <code>{uid}</code>\n"
        f"نام: {name}\n"
        f"یوزرنیم: @{username}\n"
        f"وضعیت فعلی: {status}"
    )


def admin_detail_text(uid):
    info = USERS.get(uid, {})
    name = info.get("first_name") or "-"
    username = info.get("username") or "-"
    return (
        f"👑 آیدی: <code>{uid}</code>\n"
        f"نام: {name}\n"
        f"یوزرنیم: @{username}"
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ شما به بخش مدیریت دسترسی ندارید.")
        return
    await update.message.reply_text("بخش مدیریت 👇", reply_markup=admin_menu())


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.answer("⛔ شما به بخش مدیریت دسترسی ندارید.", show_alert=True)
        return

    await query.answer()

    if data == "admin_back":
        context.user_data.pop("admin_filtered_ids", None)
        context.user_data.pop("awaiting_admin_search", None)
        await query.edit_message_text("بخش مدیریت 👇", reply_markup=admin_menu())
        return

    if data == "admin_search":
        context.user_data["awaiting_admin_search"] = True
        await query.edit_message_text(
            "آیدی یا بخشی از آیدی مورد نظر رو به صورت پیام بفرست:"
        )
        return

    if data.startswith("admin_list_"):
        page = int(data.split("_")[-1])
        filtered = context.user_data.get("admin_filtered_ids")
        ids = filtered if filtered is not None else list(USERS.keys())
        if not ids:
            await query.edit_message_text(
                "هیچ کاربری هنوز /start نزده.",
                reply_markup=admin_menu()
            )
            return
        keyboard, total_pages, page = build_user_list_keyboard(ids, page)
        await query.edit_message_text(
            f"لیست کاربران (صفحه {page + 1} از {total_pages}):",
            reply_markup=keyboard
        )
        return

    if data.startswith("admin_user_"):
        uid = data[len("admin_user_"):]
        await query.edit_message_text(
            user_detail_text(uid),
            parse_mode="HTML",
            reply_markup=build_user_detail_keyboard(uid)
        )
        return

    if data.startswith("admin_allow_") or data.startswith("admin_deny_"):
        allow = data.startswith("admin_allow_")
        uid = data.split("_", 2)[-1]
        if uid in USERS:
            USERS[uid]["allowed"] = allow
            save_users(USERS)
        await query.edit_message_text(
            user_detail_text(uid),
            parse_mode="HTML",
            reply_markup=build_user_detail_keyboard(uid)
        )
        return

    if data.startswith("admin_admins_"):
        page = int(data.split("_")[-1])
        ids = [uid for uid, info in USERS.items() if info.get("is_admin")]
        if not ids:
            await query.edit_message_text(
                "هیچ ادمینی ثبت نشده.",
                reply_markup=admin_menu()
            )
            return
        keyboard, total_pages, page = build_admin_list_keyboard(ids, page)
        await query.edit_message_text(
            f"لیست ادمین‌ها (صفحه {page + 1} از {total_pages}):",
            reply_markup=keyboard
        )
        return

    if data.startswith("admin_admin_detail_"):
        uid = data[len("admin_admin_detail_"):]
        keyboard = []
        if uid != str(user_id):
            keyboard.append([InlineKeyboardButton("❌ حذف از ادمین", callback_data=f"admin_demote_{uid}")])
        else:
            keyboard.append([InlineKeyboardButton("این شما هستید", callback_data="admin_noop")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست ادمین‌ها", callback_data="admin_admins_0")])
        await query.edit_message_text(
            admin_detail_text(uid),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("admin_demote_"):
        uid = data[len("admin_demote_"):]
        if uid == str(user_id):
            await query.answer("⛔ نمی‌تونی خودت رو حذف کنی.", show_alert=True)
            return
        if uid in USERS:
            USERS[uid]["is_admin"] = False
            save_users(USERS)
        await query.edit_message_text(
            "✅ کاربر از لیست ادمین‌ها حذف شد.",
            reply_markup=admin_menu()
        )
        return

    if data == "admin_noop":
        return


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فقط برای جستجوی آیدی توسط ادمین استفاده میشه
    user_id = update.effective_user.id
    if is_admin(user_id) and context.user_data.get("awaiting_admin_search"):
        context.user_data.pop("awaiting_admin_search", None)
        term = update.message.text.strip()
        filtered = [uid for uid in USERS.keys() if term in uid]
        context.user_data["admin_filtered_ids"] = filtered

        if not filtered:
            await update.message.reply_text(
                "هیچ کاربری با این آیدی پیدا نشد.",
                reply_markup=admin_menu()
            )
            return

        keyboard, total_pages, page = build_user_list_keyboard(filtered, 0)
        await update.message.reply_text(
            f"نتایج جستجو (صفحه {page + 1} از {total_pages}):",
            reply_markup=keyboard
        )

# ==================== درخواست اجازه‌ی کاربر ====================

async def handle_request_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    uid = str(user.id)

    if is_allowed(user.id):
        await query.answer("شما همین الان هم اجازه دارید ✅", show_alert=True)
        return

    await query.answer("درخواست شما برای ادمین ارسال شد ✅", show_alert=True)

    admin_ids = get_admin_ids()
    if not admin_ids:
        return

    name = user_display_name(user)
    text = (
        f"این {name} درخواست استفاده از بات شما را دارد.\n"
        f"لطفا یکی از گزینه‌های زیر را انتخاب کنید:"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ اجازه دادند", callback_data=f"reqallow_{uid}"),
            InlineKeyboardButton("⛔ اجازه ندادند", callback_data=f"reqdeny_{uid}"),
        ]
    ])

    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
        except Exception as e:
            print(f"⚠️ ارسال درخواست به ادمین {admin_id} با خطا مواجه شد: {e}")


async def handle_request_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    admin_user_id = update.effective_user.id

    if not is_admin(admin_user_id):
        await query.answer("⛔ شما به این بخش دسترسی ندارید.", show_alert=True)
        return

    await query.answer()

    allow = data.startswith("reqallow_")
    uid = data.split("_", 1)[-1]

    if uid in USERS:
        USERS[uid]["allowed"] = allow
        save_users(USERS)

    info = USERS.get(uid, {})
    name = info.get("first_name") or uid
    decision_text = "✅ شما اجازه دادید." if allow else "⛔ شما اجازه ندادید."
    await query.edit_message_text(f"درخواست {name} بررسی شد.\n{decision_text}")

    try:
        if allow:
            await context.bot.send_message(
                chat_id=int(uid),
                text="✅ به شما اجازه‌ی استفاده از ربات داده شد. برای شروع /start رو بزن."
            )
        else:
            await context.bot.send_message(
                chat_id=int(uid),
                text="⛔ درخواست شما برای استفاده از ربات رد شد."
            )
    except Exception as e:
        print(f"⚠️ اطلاع‌رسانی به کاربر {uid} با خطا مواجه شد: {e}")

# ==================== پایان بخش ادمین ====================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    if not is_allowed(user.id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 درخواست اجازه استفاده", callback_data="request_access")]
        ])
        await update.message.reply_text(
            "⛔ شما اجازه استفاده از این ربات رو ندارید.",
            reply_markup=keyboard
        )
        return

    context.user_data["mode"] = None
    await update.message.reply_text(
        "یکی از حالت‌ها رو انتخاب کن:",
        reply_markup=main_menu()
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # برای اینکه راحت آیدی عددی هر کسی رو بفهمی و به لیست اضافه کنی
    await update.message.reply_text(f"آیدی عددی شما: {update.effective_user.id}")


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

    await query.answer()

    if data == "mode_cover":
        context.user_data["mode"] = "cover"
        await query.edit_message_text(
            "حالت «آپلود کاور» فعال شد ✅\nفقط عکس بفرست (هیچ فرمت دیگه‌ای به جز عکس قبول نمی‌کنیم).",
            reply_markup=main_menu()
        )
    elif data == "mode_pdf":
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
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await msg.reply_text("⛔ شما اجازه استفاده از این ربات رو ندارید.")
        return

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
    app.add_handler(CommandHandler("id", myid))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    app.run_polling()


if __name__ == "__main__":
    main()
