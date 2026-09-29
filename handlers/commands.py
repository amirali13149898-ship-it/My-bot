"""دستور /start، /id و هندلر پیام متنی"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.keyboards import BTN_ADMIN, BTN_STATS, MENU_LABEL_TO_MODE, main_menu
from core.users import USERS, is_admin, is_allowed, register_user
from handlers.admin import admin_menu, build_user_list_keyboard
from handlers.bulk import _clear_bulk
from handlers.modes import activate_mode


def stats_text(user_id):
    mine = int(USERS.get(str(user_id), {}).get("uploads", 0))
    text = f"📊 آمار شما\n📤 آپلودهای موفق: {mine}"

    if is_admin(user_id):
        infos = list(USERS.values())
        total_users = len(infos)
        allowed = sum(1 for i in infos if i.get("allowed"))
        admins = sum(1 for i in infos if i.get("is_admin"))
        total_uploads = sum(int(i.get("uploads", 0)) for i in infos)
        text += (
            "\n\n📈 آمار کل ربات\n"
            f"👥 کاربران: {total_users}\n"
            f"✅ مجاز: {allowed}\n"
            f"👑 ادمین‌ها: {admins}\n"
            f"📤 کل آپلودها: {total_uploads}"
        )
    return text


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
    context.user_data.pop("awaiting_admin_search", None)
    _clear_bulk(context.user_data)
    context.user_data.pop("bulk_status_msg_id", None)
    await update.message.reply_text(
        "یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu()
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # برای اینکه راحت آیدی عددی هر کسی رو بفهمی و به لیست اضافه کنی
    await update.message.reply_text(f"آیدی عددی شما: {update.effective_user.id}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    # آپلود تکی / آپلود گروهی
    if text in MENU_LABEL_TO_MODE:
        if not is_allowed(user_id):
            await update.message.reply_text("⛔ شما اجازه استفاده از این ربات رو ندارید.")
            return
        context.user_data.pop("awaiting_admin_search", None)
        await activate_mode(update.message, context, MENU_LABEL_TO_MODE[text])
        return

    # آمار
    if text == BTN_STATS:
        if not is_allowed(user_id):
            await update.message.reply_text("⛔ شما اجازه استفاده از این ربات رو ندارید.")
            return
        await update.message.reply_text(stats_text(user_id), reply_markup=main_menu())
        return

    # مدیریت (فقط ادمین)
    if text == BTN_ADMIN:
        if not is_admin(user_id):
            await update.message.reply_text("⛔ شما به بخش مدیریت دسترسی ندارید.")
            return
        context.user_data.pop("awaiting_admin_search", None)
        await update.message.reply_text("بخش مدیریت 👇", reply_markup=admin_menu())
        return

    # بقیه‌ی متن‌ها فقط برای جستجوی آیدی توسط ادمین استفاده میشه
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
