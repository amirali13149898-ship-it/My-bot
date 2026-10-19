"""ابزارهای مشترک جریان‌های سایت: سشن، ارسال/ویرایش امن پیام، کیبوردها"""
import html

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from telegram.error import BadRequest

CB_CANCEL = "sx_cancel"
SITE_MODES = ("site_bulk", "site_single", "site_new")


def esc(text):
    return html.escape(str(text))


def cancel_row():
    return [Btn("❌ لغو", callback_data=CB_CANCEL)]


def cancel_markup():
    return Markup([cancel_row()])


# ---------------------------------------------------------------- سشن
def get_session(user_data):
    return user_data.get("site")


def new_session(user_data, kind, chat_id):
    """kind: 'bulk' | 'single' | 'new'. هر سشنِ قبلی (و تسک‌هاش) پاک میشه."""
    clear_session(user_data)
    session = {
        "kind": kind,
        "chat_id": chat_id,
        "step": None,
        "cancelled": False,
        # انتخاب مانهوا
        "list": [],
        "manhwa": None,
        # چپترها
        "items": [],      # {"name", "number", "link"}
        "failed": [],     # اسم فایل‌هایی که آپلودشون ناموفق بود
        "skipped": [],    # (اسم، شماره): قبلاً روی سایت بوده
        "existing": None,
        "pending": 0,
        "prompt_msg": None,
        "prompt_task": None,
        "review_msg": None,
        "ask_idx": None,
        "edit_idx": None,
        # مانهوای جدید
        "form": {},
        "genres": [],
    }
    user_data["site"] = session
    user_data["mode"] = f"site_{kind}"
    return session


def clear_session(user_data):
    session = user_data.pop("site", None)
    if session:
        session["cancelled"] = True
        task = session.get("prompt_task")
        if task is not None and not task.done():
            task.cancel()
    if str(user_data.get("mode") or "").startswith("site_"):
        user_data["mode"] = None
    return session


# ---------------------------------------------------------------- پیام‌ها
async def reply_to(msg, text, **kwargs):
    """روی پیام کاربر ریپلای می‌کنه (سازگار با نسخه‌های مختلف python-telegram-bot)."""
    try:
        return await msg.reply_text(text, do_quote=True, **kwargs)
    except TypeError:
        return await msg.reply_text(text, quote=True, **kwargs)


async def send(context, session, text, markup=None):
    return await context.bot.send_message(
        chat_id=session["chat_id"], text=text, reply_markup=markup, parse_mode="HTML"
    )


async def edit(message, text, markup=None):
    """ویرایش پیام؛ خطای «تغییری نکرده» و پیام حذف‌شده بی‌صدا رد میشه."""
    try:
        return await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            print(f"⚠️ ویرایش پیام ناموفق بود: {e}")
    except Exception as e:
        print(f"⚠️ ویرایش پیام ناموفق بود: {e}")
    return None


async def delete_quiet(bot, chat_id, message_id):
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
