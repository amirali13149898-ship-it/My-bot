"""اصلاح و آپلود آرشیو"""
import asyncio
import html
import io
import os
import time
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
from services.converters import (
    IMAGE_EXTENSIONS,
    NESTED_ARCHIVE_MAX_DEPTH,
    _open_archive,
    _sniff_archive_kind,
)


# ==================== بخش «اصلاح و آپلود آرشیو» ====================
# این بخش فایل رو به PDF تبدیل نمی‌کنه و لینک هم نمی‌سازه؛ «حالت واقعیِ» آرشیو رو درست می‌کنه
# و فایل خامِ درآمده رو مستقیم به‌صورت فایل (Document) برای ادمین می‌فرسته:
#   - نوع واقعی رو از روی محتوا تشخیص میده (نه پسوند): مثلاً فایلی که اسمش .zip
#     ولی در اصل RAR هست، با پسوند درست (.rar) فرستاده میشه.
#   - اگه آرشیو فقط یه «پوسته» بود و توش فقط آرشیو(های) دیگه بود (زیپ توی رار،
#     رار توی زیپ، ...)، پوسته‌ی بیرونی کنار زده میشه و آرشیوِ داخلی فرستاده میشه.
ARCHIVE_EXT = {"zip": ".zip", "rar": ".rar", "7z": ".7z"}
_JUNK_BASENAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


def _sniff_container(raw):
    """'zip' / 'rar' / '7z' / None - از روی امضای بایت‌های اول فایل."""
    kind = _sniff_archive_kind(raw)
    if kind:
        return kind
    if raw[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    return None


def _is_junk_entry(name):
    base = os.path.basename(name)
    return (
        name.endswith("/")
        or base == ""
        or base.startswith(".")
        or base.lower() in _JUNK_BASENAMES
        or name.startswith("__MACOSX/")
    )


def _unwrap_archive(raw, kind, depth=1, max_depth=NESTED_ARCHIVE_MAX_DEPTH):
    """
    اگه آرشیو «فقط پوسته» باشه (تمام محتواش خودش آرشیوه)، محتوا رو درمیاره.
    خروجی: لیستی از دیکشنری‌های {"name", "raw", "kind", "layers"} که هر کدوم
    یه آرشیوِ نهاییِ قابل‌آپلوده. اگه آرشیو پوسته نباشه (مثلاً عکس یا PDF
    داخلش باشه) یا باز نشه، خودش عیناً برمی‌گرده (layers=0).
    """
    leaf = [{"name": None, "raw": raw, "kind": kind, "layers": 0}]
    if kind not in ("zip", "rar") or depth > max_depth:
        return leaf

    inner = []
    try:
        with _open_archive(raw, kind) as af:
            names = [n for n in af.namelist() if not _is_junk_entry(n)]
            if not names or len(names) > 30:
                return leaf
            # اگه اسم حتی یه فایل عکس/PDF باشه، پوسته نیست - بدون استخراج رد شو
            if any(n.lower().endswith(IMAGE_EXTENSIONS + (".pdf",)) for n in names):
                return leaf
            for n in names:
                data = af.read(n)
                inner_kind = _sniff_container(data)
                if inner_kind is None:
                    return leaf  # یه فایل غیرآرشیو هم داخلشه -> پوسته نیست
                inner.append((os.path.basename(n), data, inner_kind))
    except Exception as e:
        print(f"⚠️ بررسی پوسته‌ی آرشیو (kind={kind}) شکست خورد، عیناً آپلود میشه: {e}")
        return leaf

    results = []
    for name, data, inner_kind in inner:
        for sub in _unwrap_archive(data, inner_kind, depth + 1, max_depth):
            sub["layers"] += 1
            if sub["name"] is None:
                sub["name"] = name
            results.append(sub)
    return results


def _archive_out_name(original_name, item, total):
    """اسم نهایی با پسوند درست. CBZ/CBR که واقعاً زیپ/رار باشن دست نمی‌خورن."""
    ext = ARCHIVE_EXT.get(item["kind"], "")
    source = original_name if total == 1 else (item["name"] or original_name)
    base, old_ext = os.path.splitext(source or "file")
    base = base or "file"
    if (old_ext.lower(), item["kind"]) in ((".cbz", "zip"), (".cbr", "rar")):
        ext = old_ext
    return f"{base}{ext}"


async def _archive_fix_and_upload(msg, context):
    doc = msg.document
    original_name = doc.file_name or "file"
    safe_orig = html.escape(original_name)

    status = await msg.reply_text(f"⬇️ در حال دریافت {original_name}...")
    try:
        raw = await download_with_progress(
            doc, status, doc.file_size, prefix=f"⬇️ در حال دریافت {original_name}..."
        )
    except TaskCancelled:
        await _safe_edit(status, f"❌ دریافت {original_name} لغو شد.")
        return
    except Exception as e:
        await _safe_edit(status, f"❌ خطا توی دریافت فایل: {e}")
        return

    kind = _sniff_container(raw)
    if kind is None:
        await _safe_edit(
            status,
            "⚠️ این فایل نه ZIP/RAR/7z تشخیص داده شد، نه شبیه آرشیوه؛ ارسال نشد.\n"
            "(برای PDF و عکس از حالت‌های خودشون استفاده کن.)",
        )
        return

    await _safe_edit(status, "🔍 در حال بررسی نوع واقعی فایل...")
    try:
        items = await _run_heavy(_unwrap_archive, raw, kind)
    except Exception as e:
        print(f"⚠️ _unwrap_archive خطا داد: {e}")
        items = [{"name": None, "raw": raw, "kind": kind, "layers": 0}]
    del raw

    # اسم‌های نهایی (یکتا)
    total = len(items)
    used = set()
    for item in items:
        out = _archive_out_name(original_name, item, total)
        stem, ext = os.path.splitext(out)
        n = 2
        while out.lower() in used:
            out = f"{stem}_{n}{ext}"
            n += 1
        used.add(out.lower())
        item["out_name"] = out

    blocks = []
    for idx, item in enumerate(items, start=1):
        out = item["out_name"]
        counter = f" ({idx} از {total})" if total > 1 else ""

        notes = []
        if out != original_name and total == 1:
            notes.append(f"🔧 نوع واقعی {item['kind'].upper()} بود؛ اسم به «{html.escape(out)}» اصلاح شد")
        if item["layers"]:
            notes.append(f"🔓 {item['layers']} لایه‌ی آرشیو بیرونی کنار زده شد")

        await _safe_edit(status, f"📤 در حال ارسال «{out}»{counter}...")
        try:
            if _in_flood():
                wait_left = _flood_remaining()
                if wait_left <= 120:
                    await asyncio.sleep(wait_left)
            await msg.reply_document(
                document=io.BytesIO(item["raw"]),
                filename=out,
                caption=out if len(out) <= 1000 else out[:1000],
                read_timeout=TELEGRAM_FILE_TIMEOUT,
                write_timeout=TELEGRAM_FILE_TIMEOUT,
                connect_timeout=60,
            )
            block = f"✅ <b>{html.escape(out)}</b> ارسال شد"
        except Exception as e:
            if isinstance(e, RetryAfter):
                _note_flood(e)
            print(f"⚠️ ارسال «{out}» به تلگرام شکست خورد: {e}")
            block = (
                f"❌ <b>{html.escape(out)}</b>\nارسال فایل ناموفق بود "
                f"({html.escape(str(e)[:150])})"
            )
        if notes:
            block += "\n" + "\n".join(notes)
        blocks.append(block)
        item["raw"] = None  # آزاد کردن رم

    text = "\n\n".join(blocks)

    try:
        await status.edit_text(text, parse_mode="HTML")
    except Exception:
        await msg.reply_text(text, parse_mode="HTML")

    # حالت فعال می‌مونه تا بشه پشت‌سرهم فایل فرستاد
    await msg.reply_text(
        "فایل بعدی رو بفرست، یا یکی از حالت‌های پایین رو بزن:",
        reply_markup=main_menu(),
    )

# ==================== پایان بخش اصلاح و آپلود آرشیو ====================
