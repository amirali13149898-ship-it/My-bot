"""پردازش نهایی و پاسخ به کاربر"""
from telegram.ext import ContextTypes

from core.config import IMGUR_CLIENT_ID
from core.keyboards import main_menu
from core.tasks import TaskCancelled
from core.telegram_io import _safe_edit, upload_catbox_with_progress
from core.users import bump_upload
from services.uploaders import upload_catbox, upload_imgur


async def process_and_reply(msg, filename, file_bytes, context: ContextTypes.DEFAULT_TYPE, status_msg=None, host="catbox", note=None):
    status = status_msg or await msg.reply_text("در حال آپلود...")

    # کاورها (عکس) به Imgur میرن (چون توی ایران بدون فیلترشکن بازه)؛
    # اگه Client ID ست نشده باشه، Catbox
    if host == "imgur" and not IMGUR_CLIENT_ID:
        host = "catbox"
    host_name = "Imgur" if host == "imgur" else "Catbox"
    uploader = upload_imgur if host == "imgur" else upload_catbox

    cancelled = False
    try:
        link = await upload_catbox_with_progress(
            filename, file_bytes, status, prefix=f"⬆️ در حال آپلود «{filename}»",
            uploader=uploader
        )
    except TaskCancelled:
        cancelled = True
        link = None

    if cancelled:
        await _safe_edit(status, "❌ آپلود لغو شد.")
    elif link:
        bump_upload(msg.chat_id)
        # لینک با <code> یعنی با یه تپ روش کپی میشه
        text = f"✅ آپلود شد ({host_name})\nلینک مستقیم:\n<code>{link}</code>"
        if note:
            text += f"\n\n{note}"
        await status.edit_text(text, parse_mode="HTML")
    else:
        await status.edit_text(
            f"❌ آپلود ناموفق بود، {host_name} بعد از چند بار تلاش هم جواب نداد. "
            "چند دقیقه‌ی دیگه دوباره امتحان کن."
        )

    # ریست کردن حالت و نمایش دوباره‌ی منو بعد از هر آپلود
    context.user_data["mode"] = None
    await msg.reply_text(
        "یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu()
    )
