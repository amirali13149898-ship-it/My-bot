"""فایل چندچپتری: آرشیو بیرونی ← باز کردن ← هر چپتر جدا PDF و آپلود ← اضافه شدن به صف بررسی"""
import asyncio
import gc
import os
import time
import zipfile

from core.tasks import TaskCancelled, _run_heavy
from core.telegram_io import download_with_progress, upload_catbox_with_progress
from core.users import bump_upload
from handlers.site.common import edit, esc, reply_to
from services.chapter_detect import fmt_num
from services.converters import _archive_to_pdf_with_stats, _open_archive
from services.multi_split import list_inner_chapters, parse_range

MAX_LISTED = 30  # سقف تعداد خط‌ها توی گزارش
OPEN_TIMEOUT = 420     # ثانیه: سقف باز کردن آرشیو بیرونی (RAR حجیم روی CPU ضعیف کنده)
LIST_TIMEOUT = 120     # ثانیه: سقف خوندن لیست فایل‌های داخلش
HEARTBEAT = 10         # ثانیه: هر چند ثانیه یه بار پیام «هنوز زنده‌ام» ادیت بشه


async def _with_heartbeat(status, text, coro, timeout):
    """
    کار سنگین رو اجرا می‌کنه و تا تموم شدنش هر HEARTBEAT ثانیه زمان سپری‌شده رو توی
    پیام نشون میده (تا گیر کردن از «در حال کار» قابل تشخیص باشه). اگه از timeout
    بیشتر طول بکشه asyncio.TimeoutError پرتاب میشه.
    """
    task = asyncio.ensure_future(coro)
    start = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=HEARTBEAT)
            if done:
                return task.result()
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise asyncio.TimeoutError()
            await edit(status, f"{text}\n⏱ {int(elapsed)} ثانیه گذشته")
    except BaseException:
        if not task.done():
            task.cancel()
        raise


def _compact(numbers):
    return ", ".join(fmt_num(n) for n in numbers) or "—"


async def handle(msg, context, session, doc, kind):
    """یه فایل چندچپتری رو کامل پردازش می‌کنه؛ داخل قفل کاربر صدا زده میشه."""
    orig = doc.file_name or "file"
    outer_label = os.path.splitext(orig)[0] or "file"
    safe_outer = esc(outer_label)
    existing = session["existing"] or set()

    status = await reply_to(msg, f"⬇️ در حال دریافت {orig}...")
    try:
        raw = await download_with_progress(
            doc, status, doc.file_size, prefix=f"⬇️ در حال دریافت {orig}..."
        )
    except TaskCancelled:
        await edit(status, f"❌ پردازش فایل <b>{safe_outer}</b> لغو شد.")
        return
    except Exception as e:
        await edit(status, f"⚠️ دریافت فایل <b>{safe_outer}</b> ناموفق بود.\n{esc(e)}")
        return

    try:
        size_mb = len(raw) / 1e6
        print(f"📦 [multi] دانلود {orig} تموم شد ({size_mb:.0f}MB, {kind}) - شروع باز کردن")
        open_text = f"📦 در حال باز کردن <b>{safe_outer}</b> ({size_mb:.0f} مگابایت)..."
        await edit(status, open_text)
        try:
            archive = await _with_heartbeat(
                status, open_text, _run_heavy(_open_archive, raw, kind), OPEN_TIMEOUT
            )
        except asyncio.TimeoutError:
            print(f"⚠️ [multi] باز کردن {orig} بیشتر از {OPEN_TIMEOUT} ثانیه طول کشید")
            await edit(
                status,
                f"⏱ باز کردن <b>{safe_outer}</b> بیشتر از {OPEN_TIMEOUT // 60} دقیقه طول کشید و متوقف شد.\n"
                "فایل خیلی حجیمه یا سرور سرش شلوغه؛ فایل رو کوچیک‌تر (مثلاً ۵ چپتر) بفرست.",
            )
            return
        except Exception as e:
            print(f"⚠️ [multi] باز کردن {orig} شکست خورد: {e!r}")
            await edit(status, f"⚠️ فایل <b>{safe_outer}</b> باز نشد (خراب یا رمزدار؟).\n{esc(e)}")
            return

        # RAR روی دیسک استخراج شده؛ دیگه نیازی به نگه داشتن کل فایل توی رم نیست
        if not isinstance(archive, zipfile.ZipFile):
            raw = None
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
                print(f"⚠️ [multi] خوندن محتوای {orig} شکست خورد: {e!r}")
                await edit(status, f"⚠️ محتوای <b>{safe_outer}</b> خونده نشد.\n{esc(e)}")
                return
            print(f"📦 [multi] {orig}: {len(entries)} فایل چپتر پیدا شد")
            if not entries:
                await edit(
                    status,
                    f"⚠️ توی <b>{safe_outer}</b> هیچ فایل چپتری (ZIP/RAR/PDF) پیدا نشد.\n"
                    "اگه این یه چپترِ معمولیه، برگرد و «فایل جدا» رو انتخاب کن.",
                )
                return
            await _process_entries(msg, context, session, status, archive, entries,
                                   outer_label, existing)
    finally:
        raw = None
        gc.collect()


async def _process_entries(msg, context, session, status, archive, entries, outer_label, existing):
    safe_outer = esc(outer_label)
    total = len(entries)
    done, failed, skipped, unnumbered = [], [], [], []
    found_numbers = set()
    cancelled = False

    for i, (name, ikind, number) in enumerate(entries, 1):
        if session["cancelled"]:
            cancelled = True
            break
        label = os.path.splitext(os.path.basename(name))[0] or f"part{i}"
        if number is not None:
            found_numbers.add(number)

        if number is not None and number in existing:
            skipped.append(number)
            continue

        try:
            inner = await asyncio.to_thread(archive.read, name)
            if ikind == "pdf":
                data = inner
            else:
                data, _stats = await _run_heavy(_archive_to_pdf_with_stats, inner, ikind)
            inner = None
            if data is None:
                raise RuntimeError("هیچ عکس یا PDF معتبری داخلش نبود")
            link = await upload_catbox_with_progress(
                f"{label}.pdf", data, status,
                prefix=f"⬆️ ({i}/{total}) در حال آپلود «{label}»",
            )
            data = None
            if not link:
                raise RuntimeError("آپلود روی Catbox ناموفق بود")
        except TaskCancelled:
            cancelled = True
            break
        except Exception as e:
            print(f"⚠️ چپتر {label} از {outer_label} پردازش نشد: {e!r}")
            failed.append(label)
            continue
        finally:
            gc.collect()

        if session["cancelled"]:
            cancelled = True
            break
        session["items"].append({"name": label, "number": number, "link": link})
        bump_upload(msg.chat_id)
        done.append((label, number))
        if number is None:
            unnumbered.append(label)

    # ---- گزارش
    lines = [f"📦 فایل <b>{safe_outer}</b> " + ("متوقف شد." if cancelled else "پردازش شد.")]
    if done:
        lines.append(f"✅ {len(done)} چپتر آپلود شد، لینک‌ها دریافت شد:")
        for label, number in done[:MAX_LISTED]:
            lines.append(f"• {esc(label)}")
        if len(done) > MAX_LISTED:
            lines.append(f"… و {len(done) - MAX_LISTED} مورد دیگه")
    if skipped:
        lines.append("⛔ قبلاً روی سایت بود (دستی آپلود کن): چپتر " + _compact(sorted(skipped)))
    if failed:
        lines.append("❌ آپلودشون ناموفق بود: " + ", ".join(esc(n) for n in failed))
        session["failed"].extend(failed)
    if unnumbered:
        lines.append("❓ شماره‌ی چپتر این‌ها از اسمشون معلوم نشد؛ بعداً ازت می‌پرسم.")

    rng = parse_range(outer_label)
    if rng:
        expected = set(range(rng[0], rng[1] + 1))
        missing = sorted(expected - found_numbers)
        extra = sorted(n for n in found_numbers if n not in expected)
        if missing and not cancelled:
            hint = " (شاید یکی از فایل‌های بدون شماره باشه)" if unnumbered else ""
            lines.append(
                f"⚠️ از بازه‌ی {rng[0]} تا {rng[1]} این چپترها داخل فایل نبود: {_compact(missing)}{hint}"
            )
        if extra:
            lines.append(f"⚠️ چپترهای خارج از بازه‌ی اسم فایل: {_compact(extra)}")

    await edit(status, "\n".join(lines)[:4000])
