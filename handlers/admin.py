"""پنل ادمین"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.config import LIST_PAGE_SIZE
from core.users import USERS, is_admin, save_users


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
