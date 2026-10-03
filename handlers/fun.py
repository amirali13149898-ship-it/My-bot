"""بخش سرگرمی: دکمه‌ی کاربر حین آپلود + مدیریت محتوا توسط ادمین"""
import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core import fun
from core.config import LIST_PAGE_SIZE
from core.tasks import _TASKS
from core.users import is_admin


# ==================== سمت کاربر ====================

async def handle_fun_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    user_id = update.effective_user.id

    if data.startswith("fun_ans_"):
        state = _TASKS.get(data[len("fun_ans_"):])
        answer = ((state or {}).get("fun") or {}).get("answer")
        if not answer:
            await query.answer("جواب پیدا نشد.")
            return
        await query.answer(answer[:200], show_alert=True)
        return

    task_id = data[len("fun_next_"):]
    state = _TASKS.get(task_id)
    if not state or state["chat_id"] != query.message.chat_id:
        await query.answer("آپلود تموم شده 🙂")
        return

    left = fun.cooldown_left(user_id)
    if left > 0:
        await query.answer(f"⏳ {int(left) + 1} ثانیه صبر کن", show_alert=False)
        return

    item = fun.pick_item(user_id)
    if item is None:
        await query.answer("فعلاً محتوایی ثبت نشده.", show_alert=True)
        return

    await query.answer()
    try:
        await fun.show_item(context.bot, task_id, item)
    except Exception as e:
        print(f"⚠️ نمایش سرگرمی ناموفق بود: {e}")


# ==================== سمت ادمین ====================

def _menu():
    c = fun.counts()
    text = (
        "🎭 مدیریت سرگرمی\n\n"
        f"😂 جوک: {c.get('joke', 0)}\n"
        f"🧩 چیستان: {c.get('riddle', 0)}\n"
        f"💡 دانستنی: {c.get('fact', 0)}\n\n"
        "محتوا حین آپلود به‌صورت رندوم و بدون تکرار به کاربرها نشون داده میشه."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ جوک", callback_data="admin_fun_add_joke"),
            InlineKeyboardButton("➕ چیستان", callback_data="admin_fun_add_riddle"),
            InlineKeyboardButton("➕ دانستنی", callback_data="admin_fun_add_fact"),
        ],
        [InlineKeyboardButton("📋 لیست و حذف", callback_data="admin_fun_list_0")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
    ])
    return text, kb


async def admin_fun_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    ud = context.user_data

    if data == "admin_fun":
        ud.pop("awaiting_fun", None)
        text, kb = _menu()
        await query.edit_message_text(text, reply_markup=kb)
        return

    if data.startswith("admin_fun_add_"):
        cat = data[len("admin_fun_add_"):]
        if cat not in fun.CAT_TITLES:
            return
        ud["awaiting_fun"] = {"cat": cat, "step": "content"}
        await query.edit_message_text(
            f"➕ افزودن {fun.CAT_TITLES[cat]}\n\n"
            "پیامت رو بفرست. این‌ها پشتیبانی میشه:\n"
            "• متن (لینک و بولد و ... حفظ میشه)\n"
            "• عکس / ویدیو / گیف (با کپشن، حداکثر ۱۰۲۴ کاراکتر)\n"
            "• دکمه‌ی شیشه‌ای لینک‌دار: یه پیام که دکمه‌ی لینکی زیرش هست "
            "(مثلاً از یه کانال یا ربات) رو برام فوروارد کن\n\n"
            "برای انصراف دکمه‌ی بازگشت.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_fun")]]),
        )
        return

    if data.startswith("admin_fun_list_"):
        page = int(data.split("_")[-1])
        items = fun.ITEMS
        if not items:
            text, kb = _menu()
            await query.edit_message_text("هیچ محتوایی نیست.\n\n" + text, reply_markup=kb)
            return
        per = LIST_PAGE_SIZE
        pages = max(1, (len(items) + per - 1) // per)
        page = max(0, min(page, pages - 1))
        rows = []
        for it in items[page * per:(page + 1) * per]:
            icon = fun.CAT_TITLES[it["cat"]].split()[0]
            rows.append([InlineKeyboardButton(f"{icon} {fun.preview(it)}", callback_data=f"admin_fun_item_{it['id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"admin_fun_list_{page - 1}"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"admin_fun_list_{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_fun")])
        await query.edit_message_text(
            f"📋 محتوای سرگرمی (صفحه {page + 1} از {pages})",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("admin_fun_item_"):
        it = fun.get_item(data[len("admin_fun_item_"):])
        if not it:
            text, kb = _menu()
            await query.edit_message_text("پیدا نشد.\n\n" + text, reply_markup=kb)
            return
        extra = f"\n\n💡 جواب: {html.escape(it['answer'])}" if it.get("answer") else ""
        await query.edit_message_text(
            f"{fun.CAT_TITLES[it['cat']]}\n\n{html.escape(fun.preview(it, 300))}{extra}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 حذف", callback_data=f"admin_fun_del_{it['id']}")],
                [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="admin_fun_list_0")],
            ]),
        )
        return

    if data.startswith("admin_fun_del_"):
        fun.delete_item(data[len("admin_fun_del_"):])
        text, kb = _menu()
        await query.edit_message_text("🗑 حذف شد.\n\n" + text, reply_markup=kb)
        return


def _after_add_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 مدیریت سرگرمی", callback_data="admin_fun")],
    ])


async def fun_admin_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیامی که ادمین بعد از «افزودن» می‌فرسته (متن/عکس/ویدیو/گیف) یا جواب چیستان."""
    msg = update.message
    ud = context.user_data
    st = ud.get("awaiting_fun")
    if not st:
        return

    # مرحله‌ی دوم چیستان: جواب
    if st["step"] == "answer":
        ans = (msg.text or "").strip()
        if not ans:
            await msg.reply_text("جواب رو به‌صورت متن بفرست.")
            return
        item = st["item"]
        item["answer"] = ans[:200]
        fun.add_item(item)
        ud.pop("awaiting_fun", None)
        await msg.reply_text("✅ چیستان اضافه شد.", reply_markup=_after_add_kb())
        return

    # مرحله‌ی محتوا
    if msg.photo:
        kind, file_id, text = "photo", msg.photo[-1].file_id, msg.caption_html or ""
    elif msg.video:
        kind, file_id, text = "video", msg.video.file_id, msg.caption_html or ""
    elif msg.animation:
        kind, file_id, text = "animation", msg.animation.file_id, msg.caption_html or ""
    elif msg.text:
        kind, file_id, text = "text", None, msg.text_html or ""
    else:
        await msg.reply_text("⚠️ فقط متن، عکس، ویدیو یا گیف قبول میشه.")
        return

    limit = 3500 if kind == "text" else 1000
    if len(text) > limit:
        await msg.reply_text(f"⚠️ متن خیلی طولانیه ({len(text)} از {limit} کاراکتر). کوتاه‌ترش کن و دوباره بفرست.")
        return

    # دکمه‌های شیشه‌ای لینک‌دار (فقط url؛ دکمه‌های callback توی فوروارد حذف میشن)
    buttons = []
    markup = getattr(msg, "reply_markup", None)
    if markup:
        for row in markup.inline_keyboard:
            r = [{"text": b.text, "url": b.url} for b in row if getattr(b, "url", None)]
            if r:
                buttons.append(r)

    item = {"cat": st["cat"], "type": kind, "text": text, "file_id": file_id, "buttons": buttons, "answer": None}

    if st["cat"] == "riddle":
        ud["awaiting_fun"] = {"cat": "riddle", "step": "answer", "item": item}
        await msg.reply_text("حالا جواب چیستان رو به‌صورت متن بفرست (حداکثر ۲۰۰ کاراکتر):")
        return

    fun.add_item(item)
    ud.pop("awaiting_fun", None)
    extra = f" (با {sum(len(r) for r in buttons)} دکمه‌ی لینکی)" if buttons else ""
    await msg.reply_text(f"✅ {fun.CAT_TITLES[st['cat']]} اضافه شد{extra}.", reply_markup=_after_add_kb())
