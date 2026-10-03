"""ارسال/ویرایش امن پیام، دانلود و خواندن فایل از تلگرام"""
import asyncio
import os
import time
from telegram.error import RetryAfter

from core.config import PROGRESS_UPDATE_INTERVAL, TELEGRAM_FILE_TIMEOUT
from core.formatting import _build_upload_text, _format_duration, _format_mb
from core.tasks import (
    TaskCancelled,
    _TASKS,
    _UPLOAD_SEM,
    _end_task,
    _new_task,
    cancel_keyboard,
)
from core.fun import cleanup_fun
from services.uploaders import upload_catbox


# قبلاً هر ۲ ثانیه دوباره تلاش می‌شد و همین باعث میشد بن طولانی‌تر بشه.
_FLOOD_UNTIL = 0.0
_LAST_EDIT_TEXT = {}  # (chat_id, message_id) -> آخرین متنی که فرستادیم


def _note_flood(err):
    global _FLOOD_UNTIL
    wait = getattr(err, "retry_after", 30)
    try:
        wait = float(wait.total_seconds())  # timedelta در نسخه‌های جدید
    except AttributeError:
        wait = float(wait)
    _FLOOD_UNTIL = max(_FLOOD_UNTIL, time.monotonic() + wait + 1)
    print(f"🚦 Flood control: تا {int(wait)} ثانیه‌ی دیگه ادیت/ارسال متوقف شد")


def _in_flood():
    return time.monotonic() < _FLOOD_UNTIL


def _flood_remaining():
    """چند ثانیه تا پایان Flood control مونده (۰ اگه فعال نیست)."""
    return max(0.0, _FLOOD_UNTIL - time.monotonic())


async def _safe_edit(status_msg, text, reply_markup=None):
    # نکته: ادیت بدون reply_markup دکمه‌های پیام رو پاک می‌کنه، برای همین
    # هر ادیتِ پیشرفت باید دکمه‌ی لغو رو دوباره بفرسته.
    if _in_flood():
        return
    key = (getattr(status_msg, "chat_id", None), getattr(status_msg, "message_id", None))
    if _LAST_EDIT_TEXT.get(key) == text:
        return  # متن عوض نشده، درخواست الکی نفرست
    try:
        await status_msg.edit_text(text, reply_markup=reply_markup)
        if len(_LAST_EDIT_TEXT) > 500:
            _LAST_EDIT_TEXT.clear()
        _LAST_EDIT_TEXT[key] = text
    except RetryAfter as e:
        _note_flood(e)
    except Exception:
        pass


async def upload_catbox_with_progress(filename, file_bytes, status_msg, prefix="", task_id=None, uploader=None):
    """
    upload_catbox رو توی یه ترد جدا اجرا می‌کنه (تا بلاک نکنه) و پیام status_msg
    رو حداکثر هر PROGRESS_UPDATE_INTERVAL ثانیه با درصد واقعیِ آپلود، حجم،
    سرعت و زمان باقی‌مانده آپدیت می‌کنه. زیر پیام دکمه‌ی «❌ لغو» هست.

    اگه چند نفر همزمان آپلود کنن، آپلودها توی یه صف مشترک (حداکثر
    CATBOX_MAX_CONCURRENT تا همزمان) میرن و به کاربر «در صف» نشون داده میشه.

    اگه task_id داده نشه، خودش یه عملیات جدید می‌سازه (و تهش پاکش می‌کنه).
    اگه کاربر لغو کنه TaskCancelled پرتاب میشه.
    """
    owns_task = task_id is None
    if owns_task:
        task_id = _new_task(status_msg.chat_id)
    state = _TASKS[task_id]
    kb = cancel_keyboard(task_id, fun=True)

    try:
        # ---- صف مشترک بین همه‌ی کاربرها ----
        if _UPLOAD_SEM.locked():
            await _safe_edit(
                status_msg,
                f"{prefix}\n⏳ در صف آپلود... (الان چند آپلود همزمان در جریانه، نوبتت که شد خودکار شروع میشه)",
                kb,
            )
        acquire = asyncio.ensure_future(_UPLOAD_SEM.acquire())
        state["task"] = acquire  # دکمه‌ی لغو همین رو قطع می‌کنه
        try:
            await acquire
        except asyncio.CancelledError:
            if state["cancelled"]:
                raise TaskCancelled()
            raise
        finally:
            state["task"] = None

        try:
            loop = asyncio.get_running_loop()
            total_size = len(file_bytes)
            start = time.monotonic()
            tracker = {"edit_at": 0.0, "t": start, "done": 0, "speed": 0.0}

            # همون لحظه‌ی شروع، دکمه‌ی لغو رو نشون بده (نه بعد از اولین ۲ ثانیه)
            await _safe_edit(status_msg, _build_upload_text(prefix, 0, total_size or 1, 0.0), kb)

            def progress_cb(done, total_bytes):
                if state["cancelled"]:
                    raise TaskCancelled()
                now = time.monotonic()
                if done < tracker["done"]:
                    # تلاش مجدد (retry) از اول شروع شده: آمار سرعت رو ریست کن
                    tracker["t"], tracker["done"], tracker["speed"] = now, 0, 0.0
                if now - tracker["edit_at"] < PROGRESS_UPDATE_INTERVAL and done < total_bytes:
                    return
                dt = now - tracker["t"]
                if dt >= 0.5:
                    inst = (done - tracker["done"]) / dt
                    # میانگین‌گیری نمایی تا سرعت و ETA مدام نپره
                    tracker["speed"] = inst if tracker["speed"] == 0 else 0.6 * tracker["speed"] + 0.4 * inst
                    tracker["t"], tracker["done"] = now, done
                elif tracker["speed"] == 0 and now - start >= 0.2 and done > 0:
                    # آپلودهای خیلی سریع: هنوز نمونه‌ی کافی نداریم، میانگین کل رو نشون بده
                    tracker["speed"] = done / (now - start)
                tracker["edit_at"] = now
                text = _build_upload_text(prefix, done, total_bytes, tracker["speed"])
                asyncio.run_coroutine_threadsafe(_safe_edit(status_msg, text, kb), loop)

            link = await asyncio.to_thread(uploader or upload_catbox, filename, file_bytes, progress_cb)
            if state["cancelled"] and not link:
                raise TaskCancelled()
            return link
        finally:
            _UPLOAD_SEM.release()
    finally:
        if owns_task:
            await cleanup_fun(status_msg.get_bot(), task_id)
            _end_task(task_id)


async def read_and_free(file_obj):
    """
    فایل رو از دیسکِ Local Bot API می‌خونه و بلافاصله همون نسخه رو پاک می‌کنه.

    توی local_mode مسیر file_path یه فایل واقعی روی دیسکه و سرور هیچوقت
    خودش پاکش نمی‌کنه؛ قبلاً تا ۳۰ دقیقه (تا اجرای پاکسازی دوره‌ای) می‌موند و
    چند فایل حجیم پشت هم دیسک رندر رو پر می‌کرد. الان بایت‌ها میرن توی رم و
    نسخه‌ی دیسک همون لحظه آزاد میشه.
    """
    path = getattr(file_obj, "file_path", None)
    try:
        return bytes(await file_obj.download_as_bytearray())
    finally:
        if path and os.path.isabs(str(path)):
            try:
                os.remove(path)
            except OSError:
                pass


async def download_with_progress(file_src, status_msg, total_size, prefix="⬇️ در حال دریافت..."):
    """
    file_src یه Document/PhotoSize تلگرامه (چیزی که .get_file() داره).

    توجه: توی حالت Local Bot API Server، دانلود واقعی از سرورهای تلگرام
    داخل خودِ درخواست get_file() انجام میشه و سرور محلی درصد لحظه‌ای بهمون
    نمی‌ده. برای همین اینجا درصد جعلی نمی‌سازیم؛ زمان سپری‌شده و حجم فایل رو
    نشون می‌دیم. (قبلاً get_file بیرون از این تابع صدا زده می‌شد و تیکر فقط
    روی خوندن فایل از دیسک بود - الان کل مرحله‌ی دریافت زیر تیکر و دکمه‌ی لغو هست.)

    اگه کاربر لغو کنه TaskCancelled پرتاب میشه.
    """
    task_id = _new_task(status_msg.chat_id)
    state = _TASKS[task_id]
    kb = cancel_keyboard(task_id)
    size_txt = _format_mb(total_size) if total_size else "نامشخص"

    async def _work():
        # get_file روی Local Bot API تا تموم شدن دانلود از تلگرام منتظر می‌مونه؛
        # timeout پیش‌فرض (۱۲۰ ثانیه) برای فایل‌های حجیم یا وقتی چند نفر همزمان
        # دانلود می‌کنن کمه.
        file_obj = await file_src.get_file(
            read_timeout=TELEGRAM_FILE_TIMEOUT,
            pool_timeout=TELEGRAM_FILE_TIMEOUT,
        )
        return await read_and_free(file_obj)

    work = asyncio.ensure_future(_work())
    state["task"] = work
    start = time.monotonic()

    try:
        await _safe_edit(status_msg, f"{prefix}\n📦 حجم فایل: {size_txt}", kb)
        while not work.done():
            await asyncio.wait({work}, timeout=PROGRESS_UPDATE_INTERVAL)
            if work.done():
                break
            elapsed = _format_duration(time.monotonic() - start)
            await _safe_edit(
                status_msg,
                f"{prefix}\n⏱ {elapsed} گذشته\n📦 حجم فایل: {size_txt}",
                kb,
            )
        try:
            return await work
        except asyncio.CancelledError:
            if state["cancelled"]:
                raise TaskCancelled()
            raise
    except BaseException:
        if not work.done():
            work.cancel()
        raise
    finally:
        _end_task(task_id)
