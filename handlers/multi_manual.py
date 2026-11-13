"""آپلود دستی ← «چندچپتری»: آرشیو چندچپتری رو باز می‌کنه و هر چپتر رو جدا،
با شماره‌ی چپتر روی اسم فایل (مثلاً 11.zip) به‌صورت فایل تلگرام می‌فرسته."""
import asyncio
import gc
import io
import os
import zipfile

from telegram.error import RetryAfter

from core.config import TELEGRAM_FILE_TIMEOUT
from core.keyboards import main_menu
from core.tasks import TaskCancelled, _run_heavy
from core.telegram_io import (
    _flood_remaining,
    _in_flood,
    _note_flood,
    _safe_edit,
    download_with_progress,
)
from handlers.convert_flow import _detect_convert_kind
from handlers.site.common import edit, esc
from handlers.site.multi import LIST_TIMEOUT, OPEN_TIMEOUT, _with_heartbeat
from services.chapter_detect import fmt_num
from services.converters import _open_archive, _sniff_archive_kind
from services.multi_split import list_inner_chapters, parse_range

_EXT = {"zip": ".zip", "rar": ".rar", "pdf": ".pdf"}
MAX_LISTED = 40


def _fill_missing_numbers(entries, outer_label):
    """
    اگه اسم فایل بیرونی بازه داشته باشه (مثلاً 11-20) و تعداد چپترهای بدون‌شماره
    دقیقاً برابر شماره‌های جا‌افتاده باشه، به ترتیب بهشون شماره میده.
    خروجی: لیست [name, kind, number, guessed]
    """
    rows = [[n, k, num, False] for n, k, num in entries]
    rng = parse_range(outer_label)
    if not rng:
        return rows
    found = {r[2] for r in rows if r[2] is not None}
    missing = [n for n in range(rng[0], rng[1] + 1) if n not in found]
    blanks = [r for r in rows if r[2] is None]
    if blanks and len(blanks) == len(missing):
        for r, n in zip(blanks, missing):
            r[2], r[3] = n, True
    return rows


def _out_names(rows):
    """اسم نهایی هر فایل: «شماره.پسوند»؛ تکراری‌ها _2، _3 ... و بدون‌شماره‌ها اسم اصلی."""
    used, names = set(), []
    for name, kind, number, _g in rows:
        ext = _EXT.get(kind, "")
        if number is not None:
            base = fmt_num(number)
        else:
            base = os.path.splitext(os.path.basename(name))[0] or "file"
        out, n = f"{base}{ext}", 2
        while out.lower() in used:
            out = f"{base}_{n}{ext}"
            n += 1
        used.add(out.lower())
        names.append(out)
    return names


async def handle_multi_manual(msg, context):
    doc = msg.document
    if not doc:
        await msg.reply_text("⚠️ فایل چندچپتری رو به‌صورت Document (ZIP یا RAR) بفرست.")
        return
    kind = _detect_convert_kind(msg)
    if kind not in ("zip", "rar"):
        await msg.reply_text("⚠️ فایل چندچپتری باید ZIP یا RAR باشه.")
        return

    orig = doc.file_name or f"file.{kind}"
    outer_label = os.path.splitext(orig)[0] or "file"
    safe_outer = esc(outer_label)

    status = await msg.reply_text(f"⬇️ در حال دریافت {orig}...")
    try:
        raw = await download_with_progress(
            doc, status, doc.file_size, prefix=f"⬇️ در حال دریافت {orig}..."
        )
    except TaskCancelled:
        await _safe_edit(status, f"❌ دریافت {orig} لغو شد.")
        return
    except Exception as e:
        await edit(status, f"⚠️ دریافت <b>{safe_outer}</b> ناموفق بود.\n{esc(e)}")
        return

    try:
        size_mb = len(raw) / 1e6
        open_text = f"📦 در حال باز کردن <b>{safe_outer}</b> ({size_mb:.0f} مگابایت)..."
        await edit(status, open_text)
        try:
            archive = await _with_heartbeat(
                status, open_text,
                _run_heavy(_open_archive, raw, _sniff_archive_kind(raw) or kind),
                OPEN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await edit(status, f"⏱ باز کردن <b>{safe_outer}</b> بیشتر از {OPEN_TIMEOUT // 60} دقیقه طول کشید و متوقف شد.")
            return
        except Exception as e:
            print(f"⚠️ [multi-manual] باز کردن {orig} شکست خورد: {e!r}")
            await edit(status, f"⚠️ فایل <b>{safe_outer}</b> باز نشد (خراب یا رمزدار؟).\n{esc(e)}")
            return

        if not isinstance(archive, zipfile.ZipFile):
            raw = None  # RAR روی دیسک استخراج شده
            gc.collect()

        with archive:
            try:
                entries = await _with_heartbeat(
                    status, f"📦 در حال خوندن محتوای <b>{safe_outer}</b>...",
                    asyncio.to_thread(list_inner_chapters, archive), LIST_TIMEOUT,
                )
            except asyncio.TimeoutError:
                await edit(status, f"⏱ خوندن محتوای <b>{safe_outer}</b> بیش از حد طول کشید.")
                return
            except Exception as e:
                await edit(status, f"⚠️ محتوای <b>{safe_outer}</b> خونده نشد.\n{esc(e)}")
                return

            if not entries:
                await edit(
                    status,
                    f"⚠️ توی <b>{safe_outer}</b> هیچ فایل چپتری (ZIP/RAR/PDF) پیدا نشد.\n"
                    "اگه این یه چپترِ معمولیه، از «📤 آپلود تکی» استفاده کن.",
                )
                return

            rows = _fill_missing_numbers(entries, outer_label)
            names = _out_names(rows)
            await _send_all(msg, status, archive, rows, names, outer_label)
    finally:
        raw = None
        gc.collect()

    await msg.reply_text("فایل چندچپتری بعدی رو بفرست، یا یکی از گزینه‌های پایین رو بزن:",
                         reply_markup=main_menu())


async def _send_all(msg, status, archive, rows, names, outer_label):
    total = len(rows)
    sent, failed, guessed, unnumbered = [], [], [], []
    found = {r[2] for r in rows if r[2] is not None}

    for i, ((name, kind, number, was_guessed), out) in enumerate(zip(rows, names), 1):
        await _safe_edit(status, f"📤 ({i}/{total}) در حال ارسال «{out}»...")
        try:
            data = await asyncio.to_thread(archive.read, name)
            if _in_flood():
                wait_left = _flood_remaining()
                if wait_left <= 120:
                    await asyncio.sleep(wait_left)
            caption = f"چپتر {fmt_num(number)}" if number is not None else out
            await msg.reply_document(
                document=io.BytesIO(data),
                filename=out,
                caption=caption,
                read_timeout=TELEGRAM_FILE_TIMEOUT,
                write_timeout=TELEGRAM_FILE_TIMEOUT,
                connect_timeout=60,
            )
            sent.append(out)
            if was_guessed:
                guessed.append(out)
            if number is None:
                unnumbered.append(out)
        except Exception as e:
            if isinstance(e, RetryAfter):
                _note_flood(e)
            print(f"⚠️ [multi-manual] ارسال «{out}» ناموفق بود: {e!r}")
            failed.append(out)
        finally:
            data = None
            gc.collect()

    lines = [f"📦 فایل <b>{esc(outer_label)}</b> جدا شد."]
    if sent:
        lines.append(f"✅ {len(sent)} فایل ارسال شد:")
        lines += [f"• {esc(n)}" for n in sent[:MAX_LISTED]]
        if len(sent) > MAX_LISTED:
            lines.append(f"… و {len(sent) - MAX_LISTED} مورد دیگه")
    if failed:
        lines.append("❌ ارسالشون ناموفق بود: " + ", ".join(esc(n) for n in failed))
    if guessed:
        lines.append("🔢 شماره‌ی این‌ها از روی بازه‌ی اسم فایل حدس زده شد: " + ", ".join(esc(n) for n in guessed))
    if unnumbered:
        lines.append("❓ شماره‌ی چپتر این‌ها از اسمشون معلوم نشد (با اسم اصلی فرستاده شد): "
                     + ", ".join(esc(n) for n in unnumbered))
    rng = parse_range(outer_label)
    if rng:
        missing = sorted(n for n in range(rng[0], rng[1] + 1) if n not in found
                         and n not in {r[2] for r in rows})
        if missing:
            lines.append("⚠️ از بازه‌ی اسم فایل این چپترها داخلش نبود: "
                         + ", ".join(fmt_num(n) for n in missing))
    await edit(status, "\n".join(lines)[:4000])
