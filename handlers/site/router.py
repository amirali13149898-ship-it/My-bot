"""نقطه‌ی ورود بخش سایت: باز کردن جریان‌ها و مسیردهی متن، فایل و دکمه‌ها"""
from core.config import SITE_ENABLED
from core.users import is_site_admin
from handlers.site import chapters, new_manhwa, picker
from handlers.site.common import (
    SITE_MODES,
    cancel_markup,  # noqa: F401
    clear_session,
    edit,
    get_session,
    new_session,
)

DENIED = "⛔ این بخش فقط برای ادمین اصلی ربات فعاله."
NOT_CONFIGURED = (
    "⚠️ اتصال به سایت تنظیم نشده.\n"
    "توی Environment Variables سرویس، BASE_URL و BOT_SECRET_KEY رو اضافه کن و دوباره دیپلوی کن."
)


def clear(user_data):
    clear_session(user_data)


async def _back_to_menu(context, chat_id, user_id, text="یکی از گزینه‌ها رو انتخاب کن:"):
    from core.keyboards import main_menu
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=main_menu(user_id))


# ================================================================ باز کردن جریان
async def open_flow(kind, update, context):
    """kind: 'bulk' | 'single' | 'new' — از دکمه‌های منوی اصلی صدا زده میشه."""
    user = update.effective_user
    msg = update.message
    if not is_site_admin(user.id):
        await msg.reply_text(DENIED)
        return
    if not SITE_ENABLED:
        await msg.reply_text(NOT_CONFIGURED)
        return

    # حالت‌های قدیمی (آپلود دستی) کنار گذاشته میشن
    from handlers.bulk import _clear_bulk
    from handlers.locked import _clear_locked
    _clear_bulk(context.user_data)
    _clear_locked(context.user_data)
    context.user_data.pop("bulk_status_msg_id", None)

    session = new_session(context.user_data, kind, msg.chat_id)
    session["user_id"] = user.id

    if kind == "new":
        await new_manhwa.begin(context, session)
        return

    ok = await picker.start(context, session)
    if not ok:
        clear_session(context.user_data)
        await _back_to_menu(context, msg.chat_id, user.id)


# ================================================================ فایل‌ها
async def file_arrival(update, context):
    """قبل از نوبت‌گیری از قفل کاربر: شمارنده‌ی فایل‌های منتظر رو زیاد می‌کنه."""
    session = get_session(context.user_data)
    if session and session["kind"] in ("bulk", "single") and is_site_admin(update.effective_user.id):
        await chapters.file_arrival(context, session)
        return session
    return None


async def file_done(update, context, session):
    await chapters.file_done(context, session)


async def handle_file(msg, context):
    session = get_session(context.user_data)
    if not session:
        await msg.reply_text("این بخش دیگه فعال نیست. دوباره از منوی اصلی شروع کن.")
        return
    if session["kind"] == "new":
        await new_manhwa.handle_file(msg, context, session)
    else:
        await chapters.handle_file(msg, context, session)


# ================================================================ متن
async def handle_text(update, context):
    """True یعنی متن مصرف شد (توسط سشن سایت)."""
    session = get_session(context.user_data)
    if not session:
        return False
    msg = update.message
    text = (msg.text or "").strip()
    step = session["step"]

    if session["kind"] == "new":
        await new_manhwa.handle_text(msg, context, session, text)
        return True

    if step == "pick":
        await picker.handle_text(msg, session, text)
    elif step in ("ask_number", "edit_number"):
        await chapters.handle_number_text(msg, context, session, text)
    elif step == "files":
        await msg.reply_text("الان فایل بفرست 📎 (یا از دکمه‌های پیام‌های قبلی استفاده کن).")
    else:
        await msg.reply_text("با دکمه‌های پیام قبلی ادامه بده 👆")
    return True


# ================================================================ دکمه‌ها
async def handle_callback(update, context, data):
    query = update.callback_query
    user = update.effective_user
    if not is_site_admin(user.id):
        await query.answer(DENIED, show_alert=True)
        return

    session = get_session(context.user_data)

    if data == "sx_cancel":
        clear_session(context.user_data)
        await query.answer("لغو شد")
        await edit(query.message, "❌ لغو شد.")
        await _back_to_menu(context, query.message.chat_id, user.id)
        return

    if not session:
        await query.answer("این بخش منقضی شده؛ دوباره از منوی اصلی شروع کن.", show_alert=True)
        return

    try:
        answered = await _dispatch(query, context, session, data)
    except Exception as e:  # هیچ خطایی نباید ربات رو از کار بندازه
        print(f"⚠️ خطا توی دکمه‌ی سایت ({data}): {e!r}")
        await query.answer("⚠️ خطایی پیش اومد. دوباره امتحان کن.", show_alert=True)
        return
    if not answered:
        await query.answer()


async def _dispatch(query, context, session, data):
    """True یعنی هندلر خودش query.answer صدا زده (مثلاً هشدار)."""
    # ---- انتخاب مانهوا
    if data.startswith("sx_list_"):
        if session["step"] != "pick":
            return
        text, markup = picker.list_view(session, int(data[len("sx_list_"):]))
        await edit(query.message, text, markup)
        return

    if data.startswith("sx_p_"):
        if session["step"] != "pick":
            return
        manhwa = picker.pick(session, int(data[len("sx_p_"):]))
        if manhwa is None:
            await query.answer("این مانهوا پیدا نشد.", show_alert=True)
            return True
        await edit(query.message, picker.chosen_text(manhwa))
        await chapters.begin(context, session, manhwa)
        return

    # ---- چپترها
    if session["kind"] in ("bulk", "single"):
        if data in ("sx_m_multi", "sx_m_single"):
            return await chapters.cb_mode(query, context, session, data == "sx_m_multi")
        table = {
            "sx_more": chapters.cb_more,
            "sx_add": chapters.cb_more,
            "sx_end": chapters.cb_end,
            "sx_edit": chapters.cb_edit,
            "sx_review": chapters.cb_review,
            "sx_next": chapters.cb_next,
            "sx_retry": chapters.cb_retry,
            "sx_delask": chapters.cb_delete_asked,
        }
        if data in table:
            return await table[data](query, context, session)
        if data.startswith("sx_e_"):
            return await chapters.cb_edit_item(query, context, session, int(data[len("sx_e_"):]))
        return

    # ---- مانهوای جدید
    if data == "sx_n_skip":
        return await new_manhwa.cb_skip(query, context, session)
    if data.startswith("sx_n_st_"):
        return await new_manhwa.cb_status(query, context, session, data[len("sx_n_st_"):])
    if data.startswith("sx_gp_"):
        return await new_manhwa.cb_genre_page(query, context, session, int(data[len("sx_gp_"):]))
    if data.startswith("sx_g_"):
        return await new_manhwa.cb_genre_toggle(query, context, session, int(data[len("sx_g_"):]))
    if data == "sx_gd":
        return await new_manhwa.cb_genre_done(query, context, session)
    if data == "sx_n_ok":
        return await new_manhwa.cb_confirm(query, context, session)
