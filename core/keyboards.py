"""منوی اصلی (کیبورد کپشنی)"""
from contextvars import ContextVar

from telegram import ReplyKeyboardMarkup

from core.users import is_admin


BTN_SINGLE = "📤 آپلود تکی"
BTN_BULK = "📚 آپلود گروهی"
BTN_STATS = "📊 آمار"
BTN_ADMIN = "⚙️ مدیریت"

# فقط دکمه‌های «آپلود» یه حالت فعال می‌کنن؛ آمار و مدیریت توی handle_text جدا هندل میشن
MENU_LABEL_TO_MODE = {
    BTN_SINGLE: "single",
    BTN_BULK: "bulk_upload",
}

# آیدی کاربرِ آپدیت جاری. با این، main_menu() همه‌جا (بدون تغییر توی بقیه‌ی فایل‌ها)
# میدونه دکمه‌ی «مدیریت» رو نشون بده یا نه.
_CURRENT_USER_ID = ContextVar("current_user_id", default=None)


async def track_user(update, context):
    user = update.effective_user
    _CURRENT_USER_ID.set(user.id if user else None)


def main_menu(user_id=None):
    if user_id is None:
        user_id = _CURRENT_USER_ID.get()

    rows = [[BTN_SINGLE, BTN_BULK], [BTN_STATS]]
    if user_id is not None and is_admin(user_id):
        rows[1].append(BTN_ADMIN)

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="یکی از گزینه‌ها رو انتخاب کن",
    )
