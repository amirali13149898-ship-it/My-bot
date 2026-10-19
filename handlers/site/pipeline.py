"""دریافت فایل از تلگرام ← (تبدیل به PDF) ← آپلود روی Catbox؛ بدون هیچ پیام نهایی"""
import gc
import os

from core.tasks import TaskCancelled, _run_heavy
from core.telegram_io import (
    _safe_edit,
    download_with_progress,
    upload_catbox_with_progress,
)
from services.converters import _archive_to_pdf_with_stats, images_bytes_to_pdf


class PipelineError(Exception):
    """خطای قابل‌نمایش به کاربر (دریافت/تبدیل/آپلود)."""


async def file_to_pdf_link(doc, kind, status):
    """
    doc: Document تلگرام، kind: 'pdf' / 'zip' / 'rar' / 'image'
    status: پیامی که پیشرفت روش نشون داده میشه.
    خروجی: لینک مستقیم PDF آپلودشده. خطا ← PipelineError، لغو ← TaskCancelled.
    """
    orig = doc.file_name or "file"
    label = os.path.splitext(orig)[0] or "file"

    try:
        raw = await download_with_progress(
            doc, status, doc.file_size, prefix=f"⬇️ در حال دریافت {orig}..."
        )
    except TaskCancelled:
        raise
    except Exception as e:
        raise PipelineError(f"خطا در دریافت فایل از تلگرام: {e}")

    try:
        if kind == "pdf":
            data = raw
            upload_name = orig if orig.lower().endswith(".pdf") else f"{label}.pdf"
        elif kind in ("zip", "rar"):
            await _safe_edit(status, f"🛠 در حال تبدیل «{label}» به PDF...")
            data, _stats = await _run_heavy(_archive_to_pdf_with_stats, raw, kind)
            if data is None:
                raise PipelineError("هیچ عکس یا PDF معتبری توی آرشیو نبود، یا آرشیو خراب/رمزدار بود.")
            upload_name = f"{label}.pdf"
        else:  # image
            await _safe_edit(status, f"🛠 در حال تبدیل «{label}» به PDF...")
            data = await _run_heavy(images_bytes_to_pdf, [(orig, raw)])
            upload_name = f"{label}.pdf"
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(f"خطا در تبدیل به PDF: {e}")
    finally:
        raw = None
        gc.collect()

    try:
        link = await upload_catbox_with_progress(
            upload_name, data, status, prefix=f"⬆️ در حال آپلود «{upload_name}»"
        )
    finally:
        data = None
        gc.collect()

    if not link:
        raise PipelineError("آپلود روی Catbox ناموفق بود (بعد از چند بار تلاش).")
    return link
