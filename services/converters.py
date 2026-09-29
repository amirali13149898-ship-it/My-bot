"""تبدیل عکس و آرشیو به PDF"""
import gc
import img2pdf
import io
import os
import pypdf
import re
import zipfile


import rarfile
from PIL import Image

# rarfile برای استخراج واقعیِ محتوای RAR به یه ابزار خارجی نیاز داره
# (خودش فقط هدرها رو می‌فهمه، دیکد نمی‌کنه). روی ایمیج آلپاینی ما
# ابزار bsdtar (از پکیج libarchive-tools توی Dockerfile) نصبه؛ اینجا
# صریحاً بهش می‌گیم ازش استفاده کنه تا به‌جای auto-detect (که همیشه
# قابل‌اعتماد نیست) مطمئن باشیم درست پیدا میشه.
import shutil
if shutil.which("bsdtar"):
    rarfile.UNRAR_TOOL = "bsdtar"
else:
    print("⚠️ ابزار bsdtar پیدا نشد؛ فایل‌های RAR ممکنه باز نشن (پکیج libarchive-tools رو چک کن).")

# ===== پشتیبانی از فرمت‌های اضافه‌ی عکس =====
# HEIC/HEIF (فرمت پیش‌فرض آیفون) و AVIF از طریق پلاگین به پیلو اضافه
# میشن - بعد از این import‌ها، Image.open خودش این فرمت‌ها رو هم می‌فهمه.
import pillow_heif
pillow_heif.register_heif_opener()
import pillow_avif  # noqa: F401  (صرفاً import شدنش کافیه، پلاگین AVIF رو ثبت می‌کنه)

# RAW دوربین (CR2/CR3/NEF/ARW/DNG) - پیلو این‌ها رو نمی‌فهمه، باید جدا دیکد بشن
import rawpy

# SVG (وکتور) - قبل از هرکاری باید به PNG رندر بشه
import cairosvg
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.error import RetryAfter

IMAGE_EXTENSIONS = (
    # راستری معمولی
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif",
    # فرمت گوشی/مرورگر
    ".heic", ".heif", ".avif",
    # وکتور
    ".svg", ".eps", ".ai",
    # خام دوربین (RAW)
    ".cr2", ".cr3", ".nef", ".arw", ".dng",
)
# پسوندهایی که واقعاً "خام دوربین" هستن و پیلو مستقیم بازشون نمی‌کنه؛
# این‌ها با rawpy دیکد میشن (نه فقط به‌عنوان fallback روی محتوای نامعتبر).
RAW_EXTENSIONS = (".cr2", ".cr3", ".nef", ".arw", ".dng")
NESTED_ARCHIVE_MAX_DEPTH = 3


def _natural_sort_key(name):
    # مرتب‌سازی طبیعی: img2 قبل از img10 بیاد، نه بعدش
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


def _looks_like_svg(raw):
    # فایل SVG واقعاً یه متن XML‌ـه، نه یه فرمت باینری با امضای مشخص؛
    # برای همین دنبال تگ <svg توی چند صد بایت اول می‌گردیم.
    head = raw[:1000].lower()
    return b"<svg" in head


def _to_img2pdf_bytes(raw):
    """
    اگه بایت خام عکس مستقیم توسط img2pdf قابل embed باشه (بدون دیکد
    شدن به بیت‌مپ)، همون بایت اصلی و دست‌نخورده برگردونده میشه —
    یعنی صفر افت کیفیت و کمترین مصرف رم.
    اگه فرمت مشکل‌دار باشه (PNG با کانال آلفا، حالت پالت، یا فرمتی
    غیر از JPEG/PNG مثل webp/bmp/gif/tiff/heic/avif)، یا وکتور (SVG/AI/EPS)
    یا خام دوربین (CR2/CR3/NEF/ARW/DNG) باشه، یه بار دیکد و با کیفیت
    ۹۵٪ به JPEG تبدیل میشه تا img2pdf بتونه قبولش کنه.
    اگه عکس اصلاً خراب/نامعتبر باشه None برمی‌گردونه.
    """
    try:
        img2pdf.convert([raw])
        return raw
    except Exception:
        pass

    img = None

    # SVG (وکتور) - اول باید به PNG رندر بشه، بعد مثل یه عکس معمولی ادامه پیدا کنه
    if _looks_like_svg(raw):
        try:
            png_bytes = cairosvg.svg2png(bytestring=raw, output_width=2000)
            img = Image.open(io.BytesIO(png_bytes))
            img.load()
        except Exception:
            img = None

    # فرمت‌های راستری معمولی + HEIC/HEIF/AVIF (با پلاگین‌های بالای فایل) +
    # EPS و AI قدیمی (پیلو خودش از طریق Ghostscript بازشون می‌کنه)
    if img is None:
        try:
            candidate = Image.open(io.BytesIO(raw))
            candidate.load()
            img = candidate
        except Exception:
            img = None

    # خام دوربین (RAW): CR2/CR3/NEF/ARW/DNG - پیلو این‌ها رو نمی‌فهمه،
    # با rawpy (لایبراری libraw) دیکد میشن
    if img is None:
        try:
            with rawpy.imread(io.BytesIO(raw)) as raw_img:
                rgb = raw_img.postprocess()
            img = Image.fromarray(rgb)
        except Exception:
            img = None

    if img is None:
        return None

    try:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except Exception:
        return None


def images_bytes_to_pdf(entries):
    """
    entries: لیستی از (filename, raw_bytes) که از قبل به ترتیب دلخواه
    مرتب شده. با img2pdf (بدون دیکد کامل به بیت‌مپ خام) توی یه PDF
    چندصفحه‌ای می‌چسبونه تا هم رم کمتر مصرف بشه هم کیفیت کامل حفظ بشه.
    اگه هیچ عکس معتبری توی entries نباشه، None برمی‌گردونه.
    """
    prepared = [
        data for data in (_to_img2pdf_bytes(raw) for _name, raw in entries)
        if data is not None
    ]

    if not prepared:
        return None

    try:
        return img2pdf.convert(prepared)
    except Exception:
        return None


def _sniff_archive_kind(raw):
    """
    نوع واقعیِ آرشیو رو از روی امضای بایت‌های اولش تشخیص میده - نه از
    روی پسوند اسم فایل یا mime_type ای که تلگرام گزارش کرده. لازمه چون
    خیلی از کاربرها (خصوصاً ربات‌های دیگه) یه فایل RAR رو با پسوند
    .zip می‌فرستن (یا برعکس)، و قبلاً توی این حالت zipfile روی بایت‌های
    RAR شکست می‌خورد و کلش «هیچی پیدا نشد» جواب می‌داد.
    خروجی: 'zip' / 'rar' / None (اگه امضا شناخته‌شده نبود).
    """
    if raw[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if raw[:7] == b"Rar!\x1a\x07\x00" or raw[:8] == b"Rar!\x1a\x07\x01\x00":
        return "rar"
    return None


def _open_archive(archive_bytes, kind):
    # اول بر اساس محتوای واقعی تشخیص بده؛ فقط اگه امضا ناشناخته بود از
    # kind ای که از پسوند/mime حدس زده شده به‌عنوان fallback استفاده کن.
    actual_kind = _sniff_archive_kind(archive_bytes) or kind
    if actual_kind == "zip":
        return zipfile.ZipFile(io.BytesIO(archive_bytes))
    return rarfile.RarFile(io.BytesIO(archive_bytes))


def _classify_entry(name, raw):
    """
    نوع یه فایل داخل آرشیو رو تشخیص میده: 'image' / 'pdf' / 'zip' / 'rar' / None.
    اول بر اساس پسوند اسم فایل. اگه پسوند شناخته‌شده نبود (یا اصلاً غلط
    بود - مثلاً یه فایل عکس که به‌اشتباه .pdf یا بدون پسوند سیوشده)،
    از روی محتوای واقعی بایت‌ها (امضای PDF یا اعتبارسنجی PIL) حدس می‌زنه.
    این یعنی هر ترکیبی از عکس/PDF داخل زیپ یا رار، هر جوری که نام‌گذاری
    شده باشه، شناسایی و تبدیل میشه.
    """
    lower = name.lower()
    if lower.endswith(IMAGE_EXTENSIONS):
        return "image"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".zip", ".cbz")):
        return "zip"
    if lower.endswith((".rar", ".cbr")):
        return "rar"

    # پسوند ناشناخته یا گمراه‌کننده -> از روی محتوای واقعی تشخیص بده
    if raw[:4] == b"%PDF":
        return "pdf"
    # آرشیوی که پسوندش پاک شده یا اسمش چیز دیگه‌ایه (مثلاً «part1» یا «data.bin»)
    archive_kind = _sniff_archive_kind(raw)
    if archive_kind:
        return archive_kind
    if _looks_like_svg(raw):
        return "image"
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        return "image"
    except Exception:
        pass
    # شاید خام دوربین (CR2/CR3/NEF/ARW/DNG) باشه که پیلو نمی‌فهمتش
    try:
        with rawpy.imread(io.BytesIO(raw)):
            pass
        return "image"
    except Exception:
        return None


def _new_stats():
    return {"images": 0, "pdfs": 0, "pages": 0, "nested": 0,
            "ignored": 0, "bad_images": 0, "bad_archives": 0}


def _iter_pdf_sources(archive_bytes, kind, depth=1, max_depth=NESTED_ARCHIVE_MAX_DEPTH,
                      prefix="", stats=None):
    """
    نسخه‌ی «جریانی» (generator): آیتم‌ها رو یکی‌یکی و به ترتیب اسم می‌ده
    (name, raw_bytes, etype) بدون اینکه همه‌ی عکس‌ها هم‌زمان توی رم باشن.
    آرشیو تودرتو (زیپ/رار) هم همون‌جا که رسیدیم باز میشه و ادامه پیدا می‌کنه.
    فایل غیرعکس/غیرPDF یا خراب فقط نادیده گرفته میشه و کل کار متوقف نمیشه.
    """
    if stats is None:
        stats = _new_stats()
    try:
        with _open_archive(archive_bytes, kind) as af:
            names = [
                n for n in af.namelist()
                if not n.endswith("/")
                and not os.path.basename(n).startswith(".")
                and os.path.basename(n) != ""
            ]
            names.sort(key=_natural_sort_key)

            for name in names:
                try:
                    raw = af.read(name)
                except Exception as e:
                    print(f"⚠️ خوندن «{name}» از آرشیو شکست خورد: {e}")
                    stats["bad_archives"] += 1
                    continue

                full_name = f"{prefix}{name}"
                etype = _classify_entry(name, raw)
                if etype in ("image", "pdf"):
                    yield (full_name, raw, etype)
                elif etype in ("zip", "rar") and depth < max_depth:
                    stats["nested"] += 1
                    yield from _iter_pdf_sources(
                        raw, etype, depth + 1, max_depth,
                        prefix=f"{full_name}/", stats=stats,
                    )
                else:
                    stats["ignored"] += 1
                raw = None
    except Exception as e:
        print(f"⚠️ باز کردن آرشیو (kind={kind}) شکست خورد: {e}")
        stats["bad_archives"] += 1


def _collect_pdf_source_entries(archive_bytes, kind, depth=1, max_depth=NESTED_ARCHIVE_MAX_DEPTH,
                                prefix="", stats=None):
    """نسخه‌ی لیستی (فقط برای سازگاری؛ رم زیادی می‌گیره) - ترجیحاً _iter_pdf_sources."""
    return list(_iter_pdf_sources(archive_bytes, kind, depth, max_depth, prefix, stats))


def _archive_to_pdf(archive_bytes, kind, stats=None):
    """
    همه‌ی عکس‌ها و PDFهای داخل یه آرشیو (و آرشیوهای تودرتوش) رو به
    ترتیب اسم، توی یه PDF واحد می‌چسبونه. هر فایل یکی‌یکی خونده و به PDF
    اضافه میشه و بعدش از رم آزاد میشه (قبلاً همه‌ی عکس‌ها هم‌زمان توی رم
    جمع می‌شدن و روی رندر ۵۱۲ مگی رم پر می‌شد).
    اگه هیچ عکس/PDF معتبری پیدا نشه، None برمی‌گردونه.
    """
    if stats is None:
        stats = _new_stats()

    writer = pypdf.PdfWriter()
    any_added = False
    count = 0

    for _name, raw, etype in _iter_pdf_sources(archive_bytes, kind, stats=stats):
        try:
            if etype == "image":
                img_ready = _to_img2pdf_bytes(raw)
                if img_ready is None:
                    stats["bad_images"] += 1
                    continue
                single_page_pdf = img2pdf.convert([img_ready])
                reader = pypdf.PdfReader(io.BytesIO(single_page_pdf))
                img_ready = single_page_pdf = None
            else:  # pdf
                reader = pypdf.PdfReader(io.BytesIO(raw))

            for page in reader.pages:
                writer.add_page(page)
            stats["images" if etype == "image" else "pdfs"] += 1
            any_added = True
        except Exception:
            # یه صفحه/فایل خراب کل عملیات رو متوقف نمی‌کنه، فقط ردش می‌کنیم
            stats["bad_images"] += 1
        finally:
            raw = None
            count += 1
            if count % 10 == 0:
                gc.collect()

    if not any_added:
        return None

    stats["pages"] = len(writer.pages)
    out = io.BytesIO()
    try:
        writer.write(out)
    except Exception:
        return None
    writer = None
    gc.collect()
    return out.getvalue()


def _archive_to_pdf_with_stats(archive_bytes, kind):
    """(pdf_bytes یا None, stats) - برای اینکه به کاربر بگیم داخل فایل چی بود."""
    stats = _new_stats()
    return _archive_to_pdf(archive_bytes, kind, stats), stats


def _describe_stats(stats):
    """یه توضیح کوتاه از اینکه داخل آرشیو چی پیدا و تبدیل شد."""
    lines = []
    if stats["nested"]:
        lines.append(f"🔓 داخل فایل {stats['nested']} آرشیو دیگه (زیپ/رار) بود؛ بازشون کردم")
    content = []
    if stats["images"]:
        content.append(f"{stats['images']} عکس")
    if stats["pdfs"]:
        content.append(f"{stats['pdfs']} PDF")
    if content:
        lines.append(f"🖼 {' + '.join(content)} → یه PDF با {stats['pages']} صفحه")
    skipped = stats["bad_images"] + stats["bad_archives"]
    if skipped:
        lines.append(f"⚠️ {skipped} مورد خراب/رمزدار بود و ردش کردم")
    if stats["ignored"]:
        lines.append(f"ℹ️ {stats['ignored']} فایل غیرعکس/غیرPDF نادیده گرفته شد")
    return "\n".join(lines)


def convert_zip_images_to_pdf(zip_bytes):
    """
    عکس‌ها و PDFهای داخل یه فایل زیپ (و زیپ/رارهای تودرتوی داخلش، تا ۳
    سطح) رو پیدا می‌کنه، به ترتیب اسم مرتب می‌کنه و توی یه PDF واحد
    می‌چسبونه. اگه هیچی پیدا نشه یا زیپ خراب باشه، None برمی‌گردونه.
    """
    return _archive_to_pdf(zip_bytes, "zip")


def convert_rar_images_to_pdf(rar_bytes):
    """
    مثل convert_zip_images_to_pdf ولی برای فایل RAR (و زیپ/رارهای
    تودرتوی داخلش، تا ۳ سطح).
    """
    return _archive_to_pdf(rar_bytes, "rar")
