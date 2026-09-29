"""منوی اصلی (کیبورد کپشنی)"""
from telegram import ReplyKeyboardMarkup


# ===== منوی اصلی: کیبورد کپشنی (Reply Keyboard) =====
# متن هر دکمه همون چیزیه که وقت زدن به‌عنوان پیام فرستاده میشه و handle_text
# اونو به حالت مربوطه تبدیل می‌کنه. دکمه‌های شیشه‌ای فقط برای چیزهایی مثل
# «لغو»/«تمام» زیر پیام‌های پیشرفت می‌مونن.
MENU_BUTTONS = [
    ("📷 آپلود کاور", "cover"),
    ("📄 آپلود PDF", "pdf"),
    ("🔄 تغییر فرمت به PDF", "to_pdf"),
    ("🔗 اتصال عکس‌ها به PDF", "connect"),
    ("🗜️ اصلاح و آپلود آرشیو", "archive_fix"),
    ("📚 آپلود گروهی", "bulk_upload"),
]
MENU_LABEL_TO_MODE = {label: mode for label, mode in MENU_BUTTONS}


def main_menu():
    rows = [[label] for label, _ in MENU_BUTTONS]
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="یکی از حالت‌ها رو انتخاب کن",
    )
