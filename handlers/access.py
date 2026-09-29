"""درخواست دسترسی کاربر و تصمیم ادمین"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.users import USERS, get_admin_ids, is_admin, is_allowed, save_users
from handlers.admin import user_display_name


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
