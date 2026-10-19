"""انتخاب مانهوا: جست‌وجو با اسم یا انتخاب از لیست با دکمه‌ی شیشه‌ای"""
from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

from handlers.site.common import cancel_row, esc, send
from services.chapter_detect import search_manhwas
from services.site_api import SiteAPIError, a_get_manhwas

PAGE_SIZE = 8
PROMPT = (
    "🔎 اسم مانهوایی که می‌خوای چپتر براش ثبت کنی رو بفرست.\n"
    "اگه اسمش یادت نیست، «نمایش لیست» رو بزن."
)


def _prompt_markup():
    return Markup([[Btn("📋 نمایش لیست", callback_data="sx_list_0")], cancel_row()])


def _title_button(session, idx):
    m = session["list"][idx]
    return [Btn(m["title"][:45], callback_data=f"sx_p_{idx}")]


async def start(context, session):
    """لیست مانهواها رو از سایت می‌گیره و از کاربر اسم می‌پرسه. False یعنی نشد."""
    status = await send(context, session, "⏳ در حال دریافت لیست مانهواها از سایت...")
    try:
        session["list"] = await a_get_manhwas()
    except SiteAPIError as e:
        await status.edit_text(f"❌ لیست مانهواها از سایت گرفته نشد:\n{e}")
        return False
    except Exception as e:  # هر خطای پیش‌بینی‌نشده‌ای رباتو نکشه
        print(f"⚠️ خطای غیرمنتظره موقع گرفتن لیست مانهوا: {e!r}")
        await status.edit_text("❌ خطای غیرمنتظره موقع ارتباط با سایت.")
        return False

    if not session["list"]:
        await status.edit_text("⚠️ هیچ مانهوایی روی سایت پیدا نشد. اول یه مانهوا ثبت کن.")
        return False

    session["step"] = "pick"
    await status.edit_text(PROMPT, reply_markup=_prompt_markup())
    return True


async def handle_text(msg, session, text):
    found = search_manhwas(session["list"], text)
    if not found:
        await msg.reply_text(
            "چیزی با این اسم پیدا نشد 🤔\nیه بار دیگه اسم رو بفرست یا از لیست انتخاب کن.",
            reply_markup=_prompt_markup(),
        )
        return
    rows = [_title_button(session, i) for i in found]
    rows.append([Btn("📋 نمایش همه", callback_data="sx_list_0")])
    rows.append(cancel_row())
    await msg.reply_text(f"نتایج جست‌وجو برای «{text}»:", reply_markup=Markup(rows))


def list_view(session, page):
    """(متن، کیبورد) صفحه‌ی page از لیست کامل مانهواها."""
    total = len(session["list"])
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    rows = [_title_button(session, i) for i in range(start, min(total, start + PAGE_SIZE))]

    nav = []
    if page > 0:
        nav.append(Btn("⬅️ قبلی", callback_data=f"sx_list_{page - 1}"))
    if page < pages - 1:
        nav.append(Btn("بعدی ➡️", callback_data=f"sx_list_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(cancel_row())
    return f"📋 لیست مانهواها (صفحه {page + 1} از {pages}):", Markup(rows)


def pick(session, idx):
    """مانهوای انتخاب‌شده یا None."""
    if 0 <= idx < len(session["list"]):
        return session["list"][idx]
    return None


def chosen_text(manhwa):
    return f"✅ مانهوا: <b>{esc(manhwa['title'])}</b>"
