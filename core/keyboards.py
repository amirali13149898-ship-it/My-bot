"""منوی اصلی (کیبورد کپشنی)"""
from contextvars import ContextVar

from telegram import ReplyKeyboardMarkup

from core.users import is_admin, is_site_admin


# ----- منوی اصلی (فقط ادمین اصلی): ثبت روی سایت -----
BTN_SITE_BULK = "🌐 آپلود گروهی"
BTN_SITE_SINGLE = "🌐 آپلود تکی"
BTN_SITE_NEW = "🆕 ثبت مانهوای جدید"
BTN_MANUAL = "📁 آپلود دستی"
BTN_BACK_SITE = "🔙 منوی اصلی"
SITE_FLOW_BUTTONS = {
    BTN_SITE_BULK: "bulk",
    BTN_SITE_SINGLE: "single",
    BTN_SITE_NEW: "new",
}

# ----- منوی دستی (همون منوی قبلی ربات) -----
BTN_SINGLE = "📤 آپلود تکی"
BTN_BULK = "📚 آپلود گروهی"
BTN_LOCKED = "🔐 فایل رمزدار"
BTN_STATS = "📊 آمار"
BTN_ADMIN = "⚙️ مدیریت"

# فقط دکمه‌های «آپلود» یه حالت فعال می‌کنن؛ آمار و مدیریت توی handle_text جدا هندل میشن
MENU_LABEL_TO_MODE = {
    BTN_SINGLE: "single",
    BTN_BULK: "bulk_upload",
    BTN_LOCKED: "locked",
}

# آیدی کاربرِ آپدیت جاری. با این، main_menu() همه‌جا (بدون تغییر توی بقیه‌ی فایل‌ها)
# میدونه دکمه‌ی «مدیریت» رو نشون بده یا نه.
_CURRENT_USER_ID = ContextVar("current_user_id", default=None)


async def track_user(update, context):
    user = update.effective_user
    _CURRENT_USER_ID.set(user.id if user else None)


# ادمین‌هایی که الان توی «آپلود دستی» هستن (توی حافظه؛ با /start یا «منوی اصلی» خارج میشن)
_MANUAL_MENU = set()


def set_manual_menu(user_id, on):
    if on:
        _MANUAL_MENU.add(user_id)
    else:
        _MANUAL_MENU.discard(user_id)


def main_menu(user_id=None):
    if user_id is None:
        user_id = _CURRENT_USER_ID.get()

    site_admin = user_id is not None and is_site_admin(user_id)

    # ادمین اصلی: منوی ثبت روی سایت (مگه اینکه «آپلود دستی» رو زده باشه)
    if site_admin and user_id not in _MANUAL_MENU:
        return ReplyKeyboardMarkup(
            [[BTN_SITE_BULK, BTN_SITE_SINGLE], [BTN_SITE_NEW, BTN_MANUAL]],
            resize_keyboard=True,
            is_persistent=False,
            input_field_placeholder="یکی از گزینه‌ها رو انتخاب کن",
        )

    rows = [[BTN_SINGLE, BTN_BULK], [BTN_LOCKED, BTN_STATS]]
    if user_id is not None and is_admin(user_id):
        rows.append([BTN_ADMIN])
    if site_admin:
        rows.append([BTN_BACK_SITE])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=False,
        input_field_placeholder="یکی از گزینه‌ها رو انتخاب کن",
    )
