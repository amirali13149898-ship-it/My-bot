"""آپلود دستیِ گروهی: تشخیص خودکار فایل چندچپتری و جدا کردن هر چپتر با لینک و شماره‌ی قابل‌کپی"""
import asyncio
import gc
import html
import os
import zipfile

from core.tasks import TaskCancelled, _run_heavy
from core.telegram_io import _safe_edit, upload_catbox_with_progress
from core.users import bump_upload
from services.chapter_detect import fmt_num
from services.converters import _archive_to_pdf_with_stats, _open_archive
from services.multi_split import is_multichapter, list_inner_chapters, parse_range

OPEN_TIMEOUT = 420
LIST_TIMEOUT = 120
MAX_MSG = 3500


def _line(label, number, link=None, error=None):
    """شماره داخل <code> میاد تا با یه تپ کپی بشه؛ لینک هم همین‌طور."""
    head = "📄 چپتر <code>%s</code>" % fmt_num(number) if number is not None else "📄 ❓ شماره نامشخص"
    head += f" — {html.escape(label)}"
    if link:
        return f"{head}\n<code>{link}</code>"
    return f"{head}\n⚠️ {html.escape(error or 'ناموفق')}"


async def try_handle(msg, context, status, raw, kind, orig_name, user_id):
    """
    آرشیو رو باز می‌کنه؛ اگه چندچپتری بود همه‌ی چپترها رو جدا آپلود می‌کنه و True برمی‌گردونه.
    اگه چندچپتری نبود (یا باز نشد) False برمی‌گردونه تا مسیر عادی ادامه بده.
    """
    label = os.path.splitext(orig_name)[0] or "file"
    try:
        archive = await asyncio.wait_for(_run_heavy(_open_archive, raw, kind), OPEN_TIMEOUT)
    except Exception as e:
        print(f"⚠️ [bulk-multi] باز کردن {orig_name} شکست خورد: {e!r}")
        return False

    with archive:
        try:
            entries = await asyncio.wait_for(
                asyncio.to_thread(list_inner_chapters, archive), LIST_TIMEOUT
            )
        except Exception as e:
            print(f"⚠️ [bulk-multi] خوندن محتوای {orig_name} شکست خورد: {e!r}")
            return False
        if not is_multichapter(entries, label):
            return False

        total = len(entries)
        print(f"📦 [bulk-multi] {orig_name}: {total} چپتر")
        results = []  # (label, number, link, error)
        cancelled = False
        for i, (name, ikind, number) in enumerate(entries, 1):
            inner_label = os.path.splitext(os.path.basename(name))[0] or f"part{i}"
            prefix = f"⬆️ ({i}/{total}) «{inner_label}»"
            try:
                await _safe_edit(status, f"🛠 ({i}/{total}) در حال آماده‌سازی «{inner_label}»...")
                inner = await asyncio.to_thread(archive.read, name)
                if ikind == "pdf":
                    data = inner
                else:
                    data, _stats = await _run_heavy(_archive_to_pdf_with_stats, inner, ikind)
                inner = None
                if data is None:
                    results.append((inner_label, number, None, "هیچ عکس یا PDF معتبری داخلش نبود"))
                    continue
                link = await upload_catbox_with_progress(f"{inner_label}.pdf", data, status, prefix=prefix)
                data = None
                if link:
                    bump_upload(user_id)
                    results.append((inner_label, number, link, None))
                else:
                    results.append((inner_label, number, None, "آپلود به Catbox ناموفق بود"))
            except TaskCancelled:
                cancelled = True
                break
            except Exception as e:
                print(f"⚠️ [bulk-multi] چپتر {inner_label} پردازش نشد: {e!r}")
                results.append((inner_label, number, None, f"خطا: {e}"))
            finally:
                gc.collect()

    ok = sum(1 for r in results if r[2])
    head = (f"❌ پردازش «{html.escape(label)}» لغو شد" if cancelled else
            f"✅ فایل چندچپتری «{html.escape(label)}» جدا شد") + f" ({ok} از {total} چپتر آپلود شد)"
    try:
        await status.edit_text(head, parse_mode="HTML")
    except Exception:
        await msg.reply_text(head, parse_mode="HTML")

    chunks, cur = [], ""
    for r in results:
        piece = _line(*r)
        cand = f"{cur}\n\n{piece}" if cur else piece
        if len(cand) > MAX_MSG:
            chunks.append(cur)
            cur = piece
        else:
            cur = cand
    if cur:
        chunks.append(cur)

    notes = []
    rng = parse_range(label)
    if rng and not cancelled:
        found = {r[1] for r in results if r[1] is not None}
        missing = sorted(set(range(rng[0], rng[1] + 1)) - found)
        if missing:
            notes.append("⚠️ از بازه‌ی اسم فایل این چپترها داخلش نبود: " + ", ".join(fmt_num(n) for n in missing))
    if any(r[1] is None for r in results):
        notes.append("❓ شماره‌ی بعضی چپترها از اسم فایلشون معلوم نشد؛ اسم‌شون بالای لینک هست.")
    if notes:
        chunks.append("\n".join(notes))

    for c in chunks:
        await msg.reply_text(c, parse_mode="HTML")
    return True
