"""آپلود تکی و گروهی چپتر: دریافت ← آپلود ← بررسی شماره‌ها ← ثبت یکجا روی سایت"""
import asyncio
import os

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup

from core.config import SITE_PROMPT_DELAY
from core.keyboards import main_menu
from core.tasks import TaskCancelled
from core.users import bump_upload
from handlers.convert_flow import _detect_convert_kind
from handlers.site.common import (
    cancel_row,
    clear_session,
    delete_quiet,
    edit,
    esc,
    reply_to,
    send,
)
from handlers.site import multi
from handlers.site.pipeline import PipelineError, file_to_pdf_link
from services.chapter_detect import (
    detect_chapter_number,
    fmt_num,
    parse_number,
    to_persian_digits,
)
from services.site_api import SiteAPIError, a_create_chapters, a_get_existing_chapter_numbers

REVIEW_LIMIT = 3500  # سقف طول متن بررسی (تلگرام ۴۰۹۶)


# ================================================================ شروع جریان
async def begin(context, session, manhwa):
    """بعد از انتخاب مانهوا: چپترهای موجود رو می‌گیره و منتظر فایل می‌مونه."""
    session.update(manhwa=manhwa, items=[], failed=[], skipped=[], pending=0,
                   step="files", multi=False)

    note = ""
    try:
        session["existing"] = await a_get_existing_chapter_numbers(manhwa["id"])
        note = f"\n📚 چپترهای ثبت‌شده‌ی فعلی روی سایت: {len(session['existing'])}"
    except SiteAPIError as e:
        session["existing"] = None
        note = ("\n⚠️ لیست چپترهای موجود گرفته نشد؛ تکراری‌ها موقع ثبت توسط سایت چک میشن."
                f"\n({esc(e)})")

    head = f"✅ مانهوا: <b>{esc(manhwa['title'])}</b>{note}\n\n"
    if session["kind"] == "single":
        await send(context, session, head + "یه فایل (PDF / ZIP / RAR) بفرست.",
                   Markup([cancel_row()]))
        return

    # آپلود گروهی: اول می‌پرسیم فایل چندچپتری هم داره یا نه
    session["step"] = "mode"
    await send(
        context, session,
        head + "فایل چندچپتری هم می‌خوای بفرستی؟\n"
        "(یعنی یه آرشیو مثل 11-20 که داخلش چند فایل چپتر هست)",
        Markup([
            [Btn("📦 بله، چندچپتری", callback_data="sx_m_multi"),
             Btn("📄 نه، فایل‌های جدا", callback_data="sx_m_single")],
            cancel_row(),
        ]),
    )


async def cb_mode(query, context, session, multi_mode):
    if session["step"] != "mode":
        return
    session["multi"] = multi_mode
    session["step"] = "files"
    if multi_mode:
        ask = ("📦 فایل چندچپتری (ZIP / RAR) رو بفرست.\n"
               "داخلش باید برای هر چپتر یه ZIP / RAR / PDF جدا باشه که شماره‌ی چپتر "
               "توی اسمش هست (مثل Ch018). هر چپتر جدا PDF و آپلود میشه.")
    else:
        ask = ("فایل‌ها رو (PDF / ZIP / RAR) یکی‌یکی یا پشت‌سرهم بفرست؛ "
               "هر کدوم به‌ترتیب پردازش و آپلود میشه.")
    await edit(query.message, f"{esc(query.message.text)}\n\n{esc(ask)}", Markup([cancel_row()]))


# ================================================================ دریافت فایل‌ها
async def file_arrival(context, session):
    """
    همین لحظه که فایل میرسه (قبل از نوبتش توی صف) صدا زده میشه تا بدونیم چند فایل
    هنوز در انتظاره؛ و پیام «اتمام/ادامه»ی قبلی حذف میشه که وسط آپلود زده نشه.
    """
    session["pending"] += 1
    task = session.get("prompt_task")
    if task is not None and not task.done():
        task.cancel()
    session["prompt_task"] = None
    if session.get("prompt_msg"):
        await delete_quiet(context.bot, session["chat_id"], session["prompt_msg"])
        session["prompt_msg"] = None


async def file_done(context, session):
    """بعد از تموم شدن پردازش یه فایل؛ اگه صفی نمونده، بعد از یه مکث «اتمام/ادامه» میاد."""
    session["pending"] = max(0, session["pending"] - 1)
    if session["pending"] == 0 and not session["cancelled"] and session["step"] == "files":
        session["prompt_task"] = asyncio.create_task(_after_batch(context, session))


async def _after_batch(context, session):
    try:
        await asyncio.sleep(SITE_PROMPT_DELAY)
        if session["cancelled"] or session["pending"] != 0 or session["step"] != "files":
            return
        await _after_files(context, session)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"⚠️ خطا بعد از پایان دسته‌ی فایل‌ها: {e!r}")


async def handle_file(msg, context, session):
    """پردازش یه فایل؛ داخل قفل کاربر صدا زده میشه (فایل‌ها یکی‌یکی)."""
    if session["cancelled"]:
        return

    step = session["step"]
    if step == "pick":
        await msg.reply_text("اول مانهوا رو انتخاب کن 👆")
        return
    if step == "mode":
        await msg.reply_text("اول مشخص کن فایل چندچپتری داری یا نه 👆")
        return
    if step == "submitting":
        await msg.reply_text("⏳ در حال ثبت روی سایت هستم؛ چند لحظه صبر کن.")
        return
    if step in ("ask_number", "edit_number", "submit_failed"):
        await msg.reply_text("اول به پیام قبلی ربات جواب بده، بعد فایل بعدی رو بفرست.")
        return
    if session["kind"] == "single" and session["items"]:
        await msg.reply_text("توی «آپلود تکی» فقط یه فایل قبوله. برای ثبتش از دکمه‌های بالا استفاده کن.")
        return

    session["step"] = "files"

    doc = msg.document
    if not doc:
        await msg.reply_text("⚠️ فایل رو به‌صورت Document بفرست (نه عکس فشرده).")
        return
    kind = _detect_convert_kind(msg)
    if kind is None:
        await msg.reply_text("⚠️ فقط PDF، ZIP، RAR (یا CBZ/CBR) یا عکس قبول میشه.")
        return

    # حالت چندچپتری: هر فایل یه آرشیو بیرونیه که داخلش چپترها جدا هستن
    if session.get("multi"):
        if kind not in ("zip", "rar"):
            await msg.reply_text("⚠️ فایل چندچپتری باید ZIP یا RAR باشه.")
            return
        await multi.handle(msg, context, session, doc, kind)
        return

    orig = doc.file_name or "file"
    label = os.path.splitext(orig)[0] or "file"
    safe = esc(label)
    number = detect_chapter_number(label)

    # چپتری که از قبل روی سایته آپلود نمیشه
    if number is not None and session["existing"] and number in session["existing"]:
        session["skipped"].append((label, number))
        await reply_to(
            msg,
            f"⚠️ فایل <b>{safe}</b>: چپتر {fmt_num(number)} قبلاً روی سایت ثبت شده؛ "
            "باید به صورت دستی آپلود بشه.",
            parse_mode="HTML",
        )
        return

    status = await reply_to(msg, f"⬇️ در حال دریافت {orig}...")
    try:
        link = await file_to_pdf_link(doc, kind, status)
    except TaskCancelled:
        await edit(status, f"❌ پردازش فایل <b>{safe}</b> لغو شد.")
        return
    except PipelineError as e:
        session["failed"].append(label)
        await edit(status, f"⚠️ فایل <b>{safe}</b> آپلود نشد.\n{esc(e)}")
        return
    except Exception as e:
        print(f"⚠️ خطای غیرمنتظره موقع پردازش {orig}: {e!r}")
        session["failed"].append(label)
        await edit(status, f"⚠️ فایل <b>{safe}</b> آپلود نشد (خطای غیرمنتظره).")
        return

    if session["cancelled"]:
        return
    session["items"].append({"name": label, "number": number, "link": link})
    bump_upload(msg.chat_id)
    await edit(status, f"✅ فایل <b>{safe}</b> آپلود شد، لینک دریافت شد.")


# ================================================================ بعد از پایان دسته
async def _after_files(context, session):
    if session["kind"] == "single" and not session["items"]:
        return  # خطا/رد شده؛ پیامش قبلاً اومده، منتظر فایل دیگه می‌مونیم
    await _ask_next_number(context, session)


async def _ask_next_number(context, session):
    """اگه فایلی بدون شماره موند ازش می‌پرسه؛ وگرنه میره مرحله‌ی بعد."""
    for idx, it in enumerate(session["items"]):
        if it["number"] is None:
            session["step"] = "ask_number"
            session["ask_idx"] = idx
            await send(
                context, session,
                f"❓ شماره‌ی چپتر فایل <b>{esc(it['name'])}</b> رو از اسمش تشخیص ندادم.\n"
                "شماره‌ش چنده؟ (فقط عدد بفرست؛ مثلاً 12 یا 12.5)",
                Markup([[Btn("🗑 این فایل رو حذف کن", callback_data="sx_delask")], cancel_row()]),
            )
            return

    if session["kind"] == "single":
        await show_review(context, session)
    else:
        await _show_prompt(context, session)


async def _show_prompt(context, session):
    session["step"] = "prompt"
    msg = await send(
        context, session, "فایل‌ها تموم شد؟",
        Markup([[Btn("✅ اتمام", callback_data="sx_end"), Btn("➕ ادامه", callback_data="sx_more")]]),
    )
    session["prompt_msg"] = msg.message_id


async def handle_number_text(msg, context, session, text):
    """جواب کاربر به «شماره‌ی چپتر؟» یا «شماره‌ی جدید»."""
    number = parse_number(text)
    if number is None:
        await msg.reply_text("فقط یه عدد معتبر بفرست (مثلاً 12 یا 12.5).")
        return

    step = session["step"]
    idx = session["ask_idx"] if step == "ask_number" else session["edit_idx"]
    if idx is None or idx >= len(session["items"]):
        return
    item = session["items"][idx]
    item["number"] = number

    # شماره‌ی تازه روی سایت هست؟ پس این فایل نباید ثبت بشه
    if session["existing"] and number in session["existing"]:
        session["items"].pop(idx)
        session["skipped"].append((item["name"], number))
        await msg.reply_text(
            f"⚠️ چپتر {fmt_num(number)} قبلاً روی سایت ثبت شده؛ فایل "
            f"«{item['name']}» از لیست حذف شد و باید دستی آپلود بشه."
        )

    if step == "ask_number":
        await _ask_next_number(context, session)
    else:
        await show_review(context, session)


# ================================================================ بررسی شماره‌ها
def _duplicates(items):
    nums = [it["number"] for it in items if it["number"] is not None]
    return sorted({n for n in nums if nums.count(n) > 1})


def review_text(session):
    items = session["items"]
    dups = _duplicates(items)

    def build(compact):
        lines = ["🔎 <b>بررسی شماره‌ی چپترها</b>",
                 f"📚 مانهوا: <b>{esc(session['manhwa']['title'])}</b>", ""]
        for it in items:
            num = fmt_num(it["number"]) if it["number"] is not None else "❓ نامشخص"
            if compact:
                lines.append(f"📄 {esc(it['name'])} ← <code>{num}</code>")
            else:
                lines += [f"📄 {esc(it['name'])}", f"🔢 <code>{num}</code>", ""]
        if not items:
            lines.append("هیچ فایلی آماده‌ی ثبت نیست.")
        lines.append("")
        if dups:
            lines.append("⚠️ چپترهای تکراری: " + ", ".join(fmt_num(n) for n in dups))
            lines.append("(قبل از ثبت، با «اصلاح» شماره‌هاشون رو درست کن.)")
        if session["skipped"]:
            lines.append("⛔ قبلاً روی سایت بوده (دستی آپلود کن): "
                         + ", ".join(esc(f"{n} (چپتر {fmt_num(c)})") for n, c in session["skipped"]))
        if session["failed"]:
            lines.append("❌ آپلودشون ناموفق بود: " + ", ".join(esc(n) for n in session["failed"]))
        lines.append(f"📦 تعداد فایل‌های دریافت‌شده: {len(items)}")
        return "\n".join(lines)

    text = build(False)
    if len(text) > REVIEW_LIMIT:
        text = build(True)
    return text[:4000]


def review_markup(session):
    if session["kind"] == "single":
        rows = [[Btn("✏️ اصلاح", callback_data="sx_edit"), Btn("✅ ثبت", callback_data="sx_next")]]
    else:
        rows = [
            [Btn("✏️ اصلاح", callback_data="sx_edit"),
             Btn("➕ اضافه کردن فایل", callback_data="sx_add")],
            [Btn("➡️ بعدی", callback_data="sx_next")],
        ]
    rows.append(cancel_row())
    return Markup(rows)


async def show_review(context, session, message=None):
    """صفحه‌ی بررسی؛ اگه message بدیم همونو ادیت می‌کنه، وگرنه پیام جدید میفرسته."""
    session["step"] = "review"
    session["edit_idx"] = None
    text, markup = review_text(session), review_markup(session)
    if message is not None:
        await edit(message, text, markup)
        session["review_msg"] = message.message_id
        return
    if session.get("review_msg"):
        await delete_quiet(context.bot, session["chat_id"], session["review_msg"])
    msg = await send(context, session, text, markup)
    session["review_msg"] = msg.message_id


# ================================================================ دکمه‌ها
async def cb_more(query, context, session):
    """«ادامه» یا «اضافه کردن فایل»: دوباره منتظر فایل میشیم."""
    if session["kind"] == "single":
        await query.answer("توی «آپلود تکی» فقط یه فایل قبوله.", show_alert=True)
        return True
    session["step"] = "files"
    session["prompt_msg"] = query.message.message_id
    await edit(query.message, "فایل‌ها رو بفرست 👇",
               Markup([[Btn("✅ اتمام", callback_data="sx_end")]]))


async def cb_end(query, context, session):
    if session["pending"] > 0:
        await query.answer("هنوز فایل‌ها در حال پردازش هستن، صبر کن تموم بشن.", show_alert=True)
        return True
    session["prompt_msg"] = None
    # اگه فایل بی‌شماره مونده باشه اول از کاربر می‌پرسیم
    if any(it["number"] is None for it in session["items"]):
        await edit(query.message, "✅ دریافت فایل‌ها تموم شد.")
        await _ask_next_number(context, session)
        return
    await show_review(context, session, query.message)


async def cb_review(query, context, session):
    await show_review(context, session, query.message)


async def cb_edit(query, context, session):
    if not session["items"]:
        await query.answer("فایلی برای اصلاح نیست.", show_alert=True)
        return True
    rows = []
    for i, it in enumerate(session["items"]):
        num = fmt_num(it["number"]) if it["number"] is not None else "؟"
        rows.append([Btn(f"{num} · {it['name'][:30]}", callback_data=f"sx_e_{i}")])
    rows.append([Btn("🔙 برگشت", callback_data="sx_review")])
    await edit(query.message, "کدوم فایل رو اصلاح کنم؟ (فقط شماره‌ی چپترش عوض میشه)", Markup(rows))


async def cb_edit_item(query, context, session, idx):
    if idx >= len(session["items"]):
        await query.answer("این فایل دیگه وجود نداره.", show_alert=True)
        return True
    it = session["items"][idx]
    session["step"] = "edit_number"
    session["edit_idx"] = idx
    num = fmt_num(it["number"]) if it["number"] is not None else "؟"
    await edit(
        query.message,
        f"شماره‌ی جدید چپتر برای <b>{esc(it['name'])}</b> رو بفرست (الان: <code>{num}</code>):",
        Markup([[Btn("🔙 برگشت", callback_data="sx_review")]]),
    )


async def cb_delete_asked(query, context, session):
    idx = session.get("ask_idx")
    if session["step"] != "ask_number" or idx is None or idx >= len(session["items"]):
        return
    it = session["items"].pop(idx)
    await edit(query.message, f"🗑 فایل <b>{esc(it['name'])}</b> از لیست حذف شد.")
    await _ask_next_number(context, session)


# ================================================================ ثبت روی سایت
def _problems(session):
    items = session["items"]
    if not items:
        return "هیچ فایلی برای ثبت نیست."
    if session["pending"] > 0:
        return "هنوز فایل‌ها در حال پردازش هستن، صبر کن تموم بشن."
    if any(it["number"] is None for it in items):
        return "یه فایل هنوز شماره‌ی چپتر نداره؛ با «اصلاح» شماره‌ش رو بده."
    if _duplicates(items):
        return "چپتر تکراری داری؛ اول با «اصلاح» درستش کن."
    return None


async def cb_next(query, context, session):
    problem = _problems(session)
    if problem:
        await query.answer(problem, show_alert=True)
        return True
    await _submit(query.message, context, session)


async def cb_retry(query, context, session):
    if session["step"] != "submit_failed":
        return
    await _submit(query.message, context, session)


def _key(n):
    try:
        return float(n)
    except (TypeError, ValueError):
        return None


async def _submit(message, context, session):
    items = sorted(session["items"], key=lambda it: it["number"])
    manhwa = session["manhwa"]
    payload = [
        {
            "chapter_number": it["number"],
            "title": f"چپتر {to_persian_digits(fmt_num(it['number']))}",
            "download_url": it["link"],
        }
        for it in items
    ]
    session["step"] = "submitting"
    await edit(message, f"⏳ در حال ثبت {len(payload)} چپتر روی سایت...")

    try:
        results = await a_create_chapters(manhwa["id"], payload)
    except SiteAPIError as e:
        session["step"] = "submit_failed"
        await edit(
            message,
            f"❌ ثبت روی سایت انجام نشد:\n{esc(e)}\n\n"
            "فایل‌ها قبلاً آپلود شدن و لینک‌هاشون نگه داشته شده؛ می‌تونی دوباره تلاش کنی.",
            Markup([[Btn("🔁 تلاش مجدد", callback_data="sx_retry")], cancel_row()]),
        )
        return
    except Exception as e:
        print(f"⚠️ خطای غیرمنتظره موقع ثبت چپترها: {e!r}")
        session["step"] = "submit_failed"
        await edit(
            message, "❌ خطای غیرمنتظره موقع ثبت روی سایت. می‌تونی دوباره تلاش کنی.",
            Markup([[Btn("🔁 تلاش مجدد", callback_data="sx_retry")], cancel_row()]),
        )
        return

    await _report(message, context, session, payload, results)


async def _report(message, context, session, payload, results):
    by_number = {}
    for r in results:
        k = _key(r.get("chapter_number"))
        if k is not None:
            by_number[k] = r
    ordered = results if len(results) == len(payload) and not by_number else None

    created, dup, err = [], [], []
    for i, ch in enumerate(payload):
        num = ch["chapter_number"]
        r = by_number.get(_key(num)) or (ordered[i] if ordered else None)
        if r is None:
            err.append((num, "از طرف سایت نتیجه‌ای برنگشت"))
        elif r["status"] == "created":
            created.append(num)
        elif r["status"] == "duplicate":
            dup.append(num)
        else:
            err.append((num, r.get("message") or "خطای نامشخص"))

    def nums(lst):
        return ", ".join(fmt_num(n) for n in lst)

    lines = [f"📊 <b>گزارش ثبت چپترها</b> — {esc(session['manhwa']['title'])}", ""]
    lines.append(f"✅ ثبت‌شده ({len(created)}): {nums(created) or '—'}")
    if dup:
        lines.append(f"⚠️ تکراری (از قبل روی سایت بود) ({len(dup)}): {nums(dup)}")
    if err:
        lines.append(f"❌ خطا ({len(err)}):")
        lines += [f"• چپتر {fmt_num(n)}: {esc(m)}" for n, m in err]
    if session["skipped"]:
        lines.append("⛔ قبلاً روی سایت بود و آپلود نشد (دستی آپلود کن): "
                     + ", ".join(fmt_num(c) for _, c in session["skipped"]))
    if session["failed"]:
        lines.append("❌ آپلودشون ناموفق بود: " + ", ".join(esc(n) for n in session["failed"]))

    await edit(message, "\n".join(lines)[:4000])

    clear_session(context.user_data)
    await context.bot.send_message(
        chat_id=session["chat_id"],
        text="یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu(session.get("user_id")),
    )
