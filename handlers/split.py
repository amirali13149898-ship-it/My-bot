"""جدا کردن فایل چندچپتری: آرشیو رو باز می‌کنه و هر چپتر رو جدا (بدون آپلود روی Catbox) توی تلگرام می‌فرسته"""
import asyncio
import gc
import html
import io
import os

from telegram.error import RetryAfter

from core.tasks import TaskCancelled, _run_heavy
from core.telegram_io import _safe_edit, download_with_progress
from services.chapter_detect import fmt_num
from services.converters import _open_archive
from services.multi_split import list_inner_chapters, parse_range

OPEN_TIMEOUT = 420
LIST_TIMEOUT = 120
_ARCHIVE_EXTS = (".zip", ".cbz", ".rar", ".cbr")


def _archive_kind(doc):
    name = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    if name.endswith((".zip", ".cbz")) or mime in ("application/zip", "application/x-zip-compressed"):
        return "zip"
    if name.endswith((".rar", ".cbr")) or mime in ("application/vnd.rar", "application/x-rar-compressed", "application/x-rar"):
        return "rar"
    return None


async def _send_doc(msg, data, filename, caption):
    for _ in range(3):
        try:
            await msg.reply_document(
                document=io.BytesIO(data), filename=filename,
                caption=caption, parse_mode="HTML",
            )
            return
        except RetryAfter as e:
            wait = getattr(e, "retry_after", 10)
            try:
                wait = wait.total_seconds()
            except AttributeError:
                pass
            await asyncio.sleep(int(wait) + 1)
    raise RuntimeError("تلگرام اجازه‌ی ارسال نداد (Flood control)")


async def handle_split_file(msg, context, user_id):
    doc = msg.document
    kind = _archive_kind(doc) if doc else None
    if kind is None:
        await msg.reply_text("⚠️ تو این حالت فقط ZIP یا RAR (یا CBZ/CBR) قبول میشه.")
        return

    orig = doc.file_name or f"file.{kind}"
    label = os.path.splitext(orig)[0] or "file"
    safe = html.escape(label)

    status = await msg.reply_text(f"⬇️ در حال دریافت {orig}...")
    try:
        raw = await download_with_progress(doc, status, doc.file_size, prefix=f"⬇️ در حال دریافت {orig}...")
    except TaskCancelled:
        await _safe_edit(status, f"❌ دریافت {orig} لغو شد.")
        return
    except Exception as e:
        await _safe_edit(status, f"❌ خطا توی دریافت {orig}: {e}")
        return

    await _safe_edit(status, f"📦 در حال باز کردن «{label}»...")
    try:
        archive = await asyncio.wait_for(_run_heavy(_open_archive, raw, kind), OPEN_TIMEOUT)
    except Exception as e:
        raw = None
        await _safe_edit(status, f"⚠️ «{label}» باز نشد (خراب یا رمزدار؟).\n{e}")
        return
    from zipfile import ZipFile
    if not isinstance(archive, ZipFile):
        raw = None  # RAR روی دیسک استخراج شده
        gc.collect()

    sent, failed = [], []
    with archive:
        try:
            entries = await asyncio.wait_for(asyncio.to_thread(list_inner_chapters, archive), LIST_TIMEOUT)
        except Exception as e:
            await _safe_edit(status, f"⚠️ محتوای «{label}» خونده نشد.\n{e}")
            return
        if not entries:
            await _safe_edit(status, f"⚠️ توی «{label}» هیچ فایل چپتری (ZIP/RAR/PDF) پیدا نشد.")
            return

        total = len(entries)
        for i, (name, ikind, number) in enumerate(entries, 1):
            base = os.path.basename(name) or f"part{i}.{ikind}"
            if ikind != "pdf" and not base.lower().endswith(_ARCHIVE_EXTS):
                base = f"{os.path.splitext(base)[0]}.{ikind}"  # پسوند گم‌شده/غلط
            await _safe_edit(status, f"📤 ({i}/{total}) در حال ارسال «{base}»...")
            try:
                data = await asyncio.to_thread(archive.read, name)
                if not data:
                    raise RuntimeError("فایل خالی بود (موقع باز کردن آرشیو درست استخراج نشده)")
                head = f"📄 چپتر <code>{fmt_num(number)}</code>" if number is not None else "📄 ❓ شماره نامشخص"
                await _send_doc(msg, data, base, f"{head}\n{html.escape(base)}")
                sent.append((base, number))
                await asyncio.sleep(1)
            except Exception as e:
                print(f"⚠️ [split] ارسال {base} شکست خورد: {e!r}")
                failed.append(f"{base} ({e})")
            finally:
                data = None
                gc.collect()

    lines = [f"✅ «{safe}» جدا شد: {len(sent)} از {total} چپتر ارسال شد."]
    rng = parse_range(label)
    if rng:
        found = {n for _b, n in sent if n is not None}
        missing = sorted(set(range(rng[0], rng[1] + 1)) - found)
        if missing:
            lines.append("⚠️ از بازه‌ی اسم فایل این چپترها داخلش نبود: " + ", ".join(fmt_num(n) for n in missing))
    if any(n is None for _b, n in sent):
        lines.append("❓ شماره‌ی بعضی چپترها از اسم فایلشون معلوم نشد؛ اسم فایل زیر هر کدوم هست.")
    if failed:
        lines.append("❌ ارسال نشد:\n" + "\n".join(f"• {html.escape(f)}" for f in failed))
    try:
        await status.edit_text("\n".join(lines)[:4000], parse_mode="HTML")
    except Exception:
        await msg.reply_text("\n".join(lines)[:4000], parse_mode="HTML")
