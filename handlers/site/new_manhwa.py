"""ثبت مانهوای جدید: کاور ← نام‌ها ← مترجم/نویسنده ← خلاصه ← وضعیت ← امتیاز ← ژانرها ← تأیید"""
import os

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

from core.config import COVER_HOST, IMGUR_CLIENT_ID
from core.keyboards import main_menu
from core.tasks import TaskCancelled
from core.telegram_io import download_with_progress, upload_catbox_with_progress
from handlers.site.common import cancel_row, clear_session, edit, esc, reply_to, send
from services.chapter_detect import to_ascii_digits
from services.site_api import SiteAPIError, a_create_manhwa, a_get_genres
from services.uploaders import upload_catbox, upload_imgur

STATUS_LABELS = {"ongoing": "در حال انتشار", "completed": "پایان یافته"}
GENRE_PAGE = 24

# مرحله ← (متن سؤال، اختیاری بودن)
_TEXT_STEPS = {
    "title_fa": ("📝 اسم مانهوا به <b>فارسی</b> چیه؟", False),
    "title_en": ("🔤 اسم <b>انگلیسی</b> مانهوا چیه؟", False),
    "translators": ("👥 <b>مترجمان</b> کیا هستن؟ (اگه چندتا بود با کاما جدا کن)", True),
    "author": ("✍️ <b>نویسنده</b> کیه؟ (اگه چندتا بود با کاما جدا کن)", True),
    "summary": ("📖 <b>خلاصه‌ی داستان</b> رو بفرست:", True),
    "rating": ("⭐ <b>امتیاز</b> مانهوا؟ (عدد ۰ تا ۱۰؛ اعشار هم مجازه، مثلاً 8.5)", True),
}
_ORDER = ["cover", "title_fa", "title_en", "translators", "author", "summary",
          "status", "rating", "genres", "confirm"]


# ================================================================ شروع و سؤال‌ها
async def begin(context, session):
    session["form"] = {"genre_ids": []}
    await _ask(context, session, "cover")


async def _ask(context, session, step):
    session["step"] = step
    if step == "cover":
        await send(
            context, session,
            "🖼 <b>کاور</b> مانهوا رو بفرست.\n"
            "(بهتره به‌صورت File بفرستی که فشرده نشه؛ عکس معمولی هم قبوله.) "
            "بعد از آپلود، لینک مستقیمش خودکار توی فرم میره.",
            Markup([cancel_row()]),
        )
    elif step in _TEXT_STEPS:
        text, optional = _TEXT_STEPS[step]
        rows = []
        if optional:
            rows.append([Btn("⏭ رد کردن", callback_data="sx_n_skip")])
        rows.append(cancel_row())
        await send(context, session, text, Markup(rows))
    elif step == "status":
        await send(
            context, session, "📌 <b>وضعیت</b> مانهوا؟",
            Markup([
                [Btn("در حال انتشار", callback_data="sx_n_st_ongoing"),
                 Btn("پایان یافته", callback_data="sx_n_st_completed")],
                cancel_row(),
            ]),
        )
    elif step == "genres":
        await _ask_genres(context, session)
    elif step == "confirm":
        await send(context, session, _summary_text(session), _confirm_markup())


async def _next(context, session):
    nxt = _ORDER[_ORDER.index(session["step"]) + 1]
    await _ask(context, session, nxt)


# ================================================================ کاور
def _is_image(msg):
    if msg.photo:
        return True
    doc = msg.document
    if not doc:
        return False
    name = (doc.file_name or "").lower()
    return (doc.mime_type or "").startswith("image/") or name.endswith(
        (".jpg", ".jpeg", ".png", ".webp", ".gif")
    )


async def handle_file(msg, context, session):
    if session["step"] != "cover":
        await msg.reply_text("الان نیازی به فایل نیست؛ به سؤال ربات جواب بده.")
        return
    if not _is_image(msg):
        await msg.reply_text("⚠️ برای کاور فقط عکس (JPG / PNG / WEBP) قبول میشه.")
        return

    src = msg.photo[-1] if msg.photo else msg.document
    filename = "cover.jpg" if msg.photo else (msg.document.file_name or "cover.jpg")

    use_imgur = COVER_HOST == "imgur" and bool(IMGUR_CLIENT_ID)
    uploader = upload_imgur if use_imgur else upload_catbox

    status = await reply_to(msg, f"⬇️ در حال دریافت {filename}...")
    try:
        data = await download_with_progress(
            src, status, getattr(src, "file_size", None), prefix=f"⬇️ در حال دریافت {filename}..."
        )
        link = await upload_catbox_with_progress(
            filename, data, status, prefix=f"⬆️ در حال آپلود کاور «{filename}»", uploader=uploader
        )
    except TaskCancelled:
        await edit(status, "❌ آپلود کاور لغو شد. دوباره کاور رو بفرست.")
        return
    except Exception as e:
        print(f"⚠️ خطا موقع آپلود کاور: {e!r}")
        await edit(status, f"❌ آپلود کاور ناموفق بود: {esc(e)}\nدوباره کاور رو بفرست.")
        return

    if not link:
        await edit(status, "❌ آپلود کاور ناموفق بود. دوباره کاور رو بفرست.")
        return

    session["form"]["cover_url"] = link
    await edit(status, f"✅ کاور آپلود شد:\n<code>{esc(link)}</code>")
    await _next(context, session)


# ================================================================ متن‌ها
def _parse_rating(text):
    t = to_ascii_digits(text).strip().replace("٫", ".").replace(",", ".")
    try:
        value = float(t)
    except ValueError:
        return None
    return round(value, 2) if 0 <= value <= 10 else None


async def handle_text(msg, context, session, text):
    step = session["step"]
    if step not in _TEXT_STEPS:
        await msg.reply_text("الان با دکمه‌های پیام قبلی ادامه بده 👆")
        return
    value = text.strip()
    if not value:
        return

    if step == "rating":
        rating = _parse_rating(value)
        if rating is None:
            await msg.reply_text("امتیاز باید یه عدد بین ۰ تا ۱۰ باشه (مثلاً 8.5).")
            return
        value = rating

    session["form"][step] = value
    await _next(context, session)


async def cb_skip(query, context, session):
    step = session["step"]
    if step not in _TEXT_STEPS or not _TEXT_STEPS[step][1]:
        await query.answer("این مورد اجباریه.", show_alert=True)
        return True
    await edit(query.message, f"{_TEXT_STEPS[step][0]}\n⏭ رد شد.")
    await _next(context, session)


async def cb_status(query, context, session, value):
    if session["step"] != "status" or value not in STATUS_LABELS:
        return
    session["form"]["status"] = value
    await edit(query.message, f"📌 وضعیت: <b>{STATUS_LABELS[value]}</b>")
    await _next(context, session)


# ================================================================ ژانرها (چندانتخابی)
async def _ask_genres(context, session):
    if not session["genres"]:
        try:
            session["genres"] = await a_get_genres()
        except SiteAPIError as e:
            await send(context, session, f"❌ لیست ژانرها از سایت گرفته نشد:\n{esc(e)}",
                       Markup([[Btn("🔁 تلاش مجدد", callback_data="sx_gp_0")], cancel_row()]))
            return
        if not session["genres"]:
            await send(context, session, "⚠️ هیچ ژانری روی سایت تعریف نشده.", Markup([cancel_row()]))
            return
    session["genre_msg"] = None
    text, markup = _genre_view(session, 0)
    msg = await send(context, session, text, markup)
    session["genre_msg"] = msg.message_id


def _genre_view(session, page):
    genres = session["genres"]
    chosen = set(session["form"]["genre_ids"])
    pages = max(1, (len(genres) + GENRE_PAGE - 1) // GENRE_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * GENRE_PAGE

    rows, row = [], []
    for i in range(start, min(len(genres), start + GENRE_PAGE)):
        g = genres[i]
        mark = "✅ " if g["id"] in chosen else ""
        row.append(Btn(f"{mark}{g['name'][:18]}", callback_data=f"sx_g_{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(Btn("⬅️ قبلی", callback_data=f"sx_gp_{page - 1}"))
    if page < pages - 1:
        nav.append(Btn("بعدی ➡️", callback_data=f"sx_gp_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([Btn(f"✅ تأیید ژانرها ({len(chosen)})", callback_data="sx_gd")])
    rows.append(cancel_row())

    session["genre_page"] = page
    return f"🏷 <b>ژانرها</b> رو انتخاب کن (هر چندتا که لازمه)، بعد «تأیید» رو بزن:", Markup(rows)


async def cb_genre_toggle(query, context, session, idx):
    if session["step"] != "genres" or idx >= len(session["genres"]):
        return
    gid = session["genres"][idx]["id"]
    ids = session["form"]["genre_ids"]
    if gid in ids:
        ids.remove(gid)
    else:
        ids.append(gid)
    text, markup = _genre_view(session, session.get("genre_page", 0))
    await edit(query.message, text, markup)


async def cb_genre_page(query, context, session, page):
    if session["step"] != "genres":
        return
    if not session["genres"]:  # دکمه‌ی «تلاش مجدد» بعد از خطای دریافت
        await edit(query.message, "⏳ در حال دریافت ژانرها...")
        await _ask_genres(context, session)
        return
    text, markup = _genre_view(session, page)
    await edit(query.message, text, markup)


async def cb_genre_done(query, context, session):
    if session["step"] != "genres":
        return
    if not session["form"]["genre_ids"]:
        await query.answer("حداقل یه ژانر انتخاب کن.", show_alert=True)
        return True
    names = [g["name"] for g in session["genres"] if g["id"] in session["form"]["genre_ids"]]
    await edit(query.message, "🏷 ژانرها: <b>" + esc("، ".join(names)) + "</b>")
    await _next(context, session)


# ================================================================ تأیید و ثبت
def _confirm_markup():
    return Markup([[Btn("✅ ثبت روی سایت", callback_data="sx_n_ok")], cancel_row()])


def _summary_text(session):
    f = session["form"]
    names = [g["name"] for g in session["genres"] if g["id"] in f.get("genre_ids", [])]
    dash = "—"
    lines = [
        "📋 <b>بررسی نهایی مانهوای جدید</b>", "",
        f"🖼 کاور: <code>{esc(f.get('cover_url', dash))}</code>",
        f"📝 نام فارسی: {esc(f.get('title_fa', dash))}",
        f"🔤 نام انگلیسی: {esc(f.get('title_en', dash))}",
        f"👥 مترجمان: {esc(f.get('translators', dash))}",
        f"✍️ نویسنده: {esc(f.get('author', dash))}",
        f"📌 وضعیت: {STATUS_LABELS.get(f.get('status'), dash)}",
        f"⭐ امتیاز: {esc(f.get('rating', dash))}",
        f"🏷 ژانرها: {esc('، '.join(names)) or dash}",
        "", f"📖 خلاصه:\n{esc(f.get('summary', dash))}",
    ]
    return "\n".join(lines)[:4000]


async def cb_confirm(query, context, session):
    if session["step"] not in ("confirm", "submit_failed"):
        return
    f = session["form"]
    payload = {
        "title_fa": f.get("title_fa"),
        "title_en": f.get("title_en"),
        "translators": f.get("translators"),
        "author": f.get("author"),
        "summary": f.get("summary"),
        "status": f.get("status"),
        "rating": f.get("rating"),
        "cover_url": f.get("cover_url"),
        "genre_ids": f.get("genre_ids"),
    }
    await edit(query.message, "⏳ در حال ثبت مانهوا روی سایت...")
    try:
        result = await a_create_manhwa(payload)
    except SiteAPIError as e:
        session["step"] = "submit_failed"
        await edit(
            query.message,
            f"❌ ثبت مانهوا روی سایت انجام نشد:\n{esc(e)}\n\nاطلاعاتت نگه داشته شده؛ می‌تونی دوباره تلاش کنی.",
            Markup([[Btn("🔁 تلاش مجدد", callback_data="sx_n_ok")], cancel_row()]),
        )
        return
    except Exception as e:
        print(f"⚠️ خطای غیرمنتظره موقع ثبت مانهوا: {e!r}")
        session["step"] = "submit_failed"
        await edit(
            query.message, "❌ خطای غیرمنتظره موقع ثبت مانهوا. می‌تونی دوباره تلاش کنی.",
            Markup([[Btn("🔁 تلاش مجدد", callback_data="sx_n_ok")], cancel_row()]),
        )
        return

    extra = ""
    if result.get("id") is not None:
        extra += f"\n🆔 شناسه: <code>{esc(result['id'])}</code>"
    if result.get("slug"):
        extra += f"\n🔗 slug: <code>{esc(result['slug'])}</code>"
    await edit(query.message, f"✅ مانهوای <b>{esc(f.get('title_fa'))}</b> روی سایت ثبت شد.{extra}")

    chat_id = session["chat_id"]
    user_id = session.get("user_id")
    clear_session(context.user_data)
    await context.bot.send_message(
        chat_id=chat_id, text="یکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu(user_id)
    )
