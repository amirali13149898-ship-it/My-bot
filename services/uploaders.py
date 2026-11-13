"""آپلودرها: Catbox / ImgBB / Imgur"""
import io
import mimetypes
import requests
import time
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder,
    MultipartEncoderMonitor,
)

from core.config import (
    CATBOX_API,
    IMGBB_API,
    IMGBB_API_KEY,
    IMGUR_API,
    IMGUR_CLIENT_ID,
    UPLOAD_RETRIES,
)
from core.tasks import TaskCancelled


def _catbox_post(filename, file_bytes, progress_cb):
    if progress_cb:
        encoder = MultipartEncoder(fields={
            "reqtype": "fileupload",
            "fileToUpload": (filename, io.BytesIO(bytes(file_bytes)), "application/octet-stream"),
        })
        total = encoder.len  # حجم کل بدنه‌ی درخواست (فایل + هدرهای multipart)

        def _on_read(monitor):
            progress_cb(monitor.bytes_read, total)

        monitor = MultipartEncoderMonitor(encoder, _on_read)
        return requests.post(
            CATBOX_API,
            data=monitor,
            headers={"Content-Type": monitor.content_type},
            timeout=300,
        )
    return requests.post(
        CATBOX_API,
        data={"reqtype": "fileupload"},
        files={"fileToUpload": (filename, bytes(file_bytes))},
        timeout=60,
    )


def upload_catbox(filename, file_bytes, progress_cb=None):
    """
    آپلود به Catbox. اگه progress_cb داده بشه، آپلود به‌صورت استریم (با
    MultipartEncoderMonitor) انجام میشه و progress_cb(bytes_sent, total_bytes)
    هر چند بار که داده واقعاً روی سوکت نوشته بشه صدا زده میشه - یعنی درصدِ
    واقعیِ آپلوده، نه یه تخمین ساختگی.

    progress_cb می‌تونه TaskCancelled پرتاب کنه تا آپلود وسط کار قطع بشه.

    اگه Catbox خطای موقتی بده (قطع اتصال، ۵xx، ۴۲۹ و ...) تا UPLOAD_RETRIES
    بار با فاصله‌ی زمانی دوباره تلاش میشه، و دلیل واقعیِ هر شکست توی لاگ
    چاپ میشه (قبلاً همه‌ی خطاها بی‌صدا بلعیده می‌شدن).
    """
    for attempt in range(1, UPLOAD_RETRIES + 1):
        wait = 3 * attempt
        try:
            r = _catbox_post(filename, file_bytes, progress_cb)
            text = (r.text or "").strip()
            if r.status_code == 200 and text.startswith("http"):
                return text
            reason = f"HTTP {r.status_code}: {text[:150]}"
            if r.status_code in (400, 401, 403, 413):
                # خطای قطعیِ سمت درخواست - تکرارش فایده ای نداره
                print(f"⚠️ Catbox رد کرد (بدون تلاش مجدد): {reason}")
                return None
            try:
                wait = min(30, max(wait, int(r.headers.get("Retry-After", 0))))
            except (TypeError, ValueError):
                pass
        except TaskCancelled:
            raise
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"

        print(f"⚠️ Catbox تلاش {attempt}/{UPLOAD_RETRIES} ناموفق بود ({filename}): {reason}")
        if attempt < UPLOAD_RETRIES:
            time.sleep(wait)
    return None


def upload_imgbb(filename, file_bytes, progress_cb=None):
    """
    آپلود عکس به ImgBB (بدون expiration => دائمی). لینک مستقیم عکس
    (data.url) رو برمی‌گردونه یا None. مثل upload_catbox، progress_cb
    می‌تونه TaskCancelled پرتاب کنه تا آپلود قطع بشه.
    """
    try:
        mime = mimetypes.guess_type(filename)[0] or "image/jpeg"
        encoder = MultipartEncoder(fields={
            "key": IMGBB_API_KEY,
            "image": (filename, io.BytesIO(bytes(file_bytes)), mime),
        })
        total = encoder.len

        def _on_read(monitor):
            if progress_cb:
                progress_cb(monitor.bytes_read, total)

        monitor = MultipartEncoderMonitor(encoder, _on_read)
        r = requests.post(
            IMGBB_API,
            data=monitor,
            headers={
                "Content-Type": monitor.content_type,
                # ImgBB درخواست‌های بدون User-Agent مرورگر رو گاهی به‌عنوان
                # بات مسدود می‌کنه (خطای کد 103). این هدر رو شبیه یه
                # مرورگر واقعی می‌فرستیم تا اون تشخیص رد بشه.
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://imgbb.com/",
                "Origin": "https://imgbb.com",
                "Accept": "application/json",
            },
            timeout=300,
        )
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200 and data.get("success"):
            return data["data"]["url"]
        # دلیل خطا (مثلاً کلید اشتباه) توی لاگ سرور چاپ میشه
        print(f"⚠️ ImgBB خطا داد: HTTP {r.status_code} - {str(data)[:200]}")
    except TaskCancelled:
        raise
    except Exception as e:
        print(f"⚠️ ImgBB آپلود نشد: {e}")
    return None


def upload_imgur(filename, file_bytes, progress_cb=None):
    """
    آپلود عکس به Imgur به‌صورت ناشناس (بدون نیاز به لاگین کاربر، فقط با
    Client ID). لینک مستقیم عکس رو برمی‌گردونه یا None. مثل upload_imgbb،
    progress_cb می‌تونه TaskCancelled پرتاب کنه تا آپلود قطع بشه.
    """
    try:
        mime = mimetypes.guess_type(filename)[0] or "image/jpeg"
        encoder = MultipartEncoder(fields={
            "image": (filename, io.BytesIO(bytes(file_bytes)), mime),
            "type": "file",
        })
        total = encoder.len

        def _on_read(monitor):
            if progress_cb:
                progress_cb(monitor.bytes_read, total)

        monitor = MultipartEncoderMonitor(encoder, _on_read)
        r = requests.post(
            IMGUR_API,
            data=monitor,
            headers={
                "Content-Type": monitor.content_type,
                "Authorization": f"Client-ID {IMGUR_CLIENT_ID}",
            },
            timeout=300,
        )
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200 and data.get("success"):
            return data["data"]["link"]
        # دلیل خطا (مثلاً Client ID اشتباه) توی لاگ سرور چاپ میشه
        print(f"⚠️ Imgur خطا داد: HTTP {r.status_code} - {str(data)[:200]}")
    except TaskCancelled:
        raise
    except Exception as e:
        print(f"⚠️ Imgur آپلود نشد: {e}")
    return None


# ===== محافظ Flood control =====
# وقتی تلگرام RetryAfter میده، تا پایان اون مدت هیچ ادیتِ پیشرفتی نمی‌فرستیم؛
