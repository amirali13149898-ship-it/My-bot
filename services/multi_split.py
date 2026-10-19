"""فایل‌های چندچپتری: آرشیوی (مثل 11-20.zip) که داخلش برای هر چپتر یه ZIP/RAR/PDF جدا هست"""
import os
import re

from services.chapter_detect import detect_chapter_number, to_ascii_digits
from services.converters import _classify_entry, _natural_sort_key

CHAPTER_KINDS = ("zip", "rar", "pdf")
_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_RANGE_RE = re.compile(r"(\d+)\s*[-_–—~]\s*(\d+)")
MAX_RANGE = 500


def parse_range(filename):
    """
    بازه‌ی چپترها از اسم فایل بیرونی: «11-20» ← (11, 20)، «1_10» ← (1, 10).
    اگه الگو نبود یا بازه نامعتبر بود None.
    """
    stem = os.path.splitext(to_ascii_digits(filename or ""))[0]
    m = _RANGE_RE.search(stem)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a < 0 or b < a or b - a > MAX_RANGE:
        return None
    return a, b


def _is_junk(name):
    parts = name.replace("\\", "/").split("/")
    base = parts[-1]
    return (
        not base
        or name.endswith("/")
        or "__MACOSX" in parts
        or base.startswith(".")
        or base.lower() in _JUNK_NAMES
    )


def list_inner_chapters(archive):
    """
    فایل‌های چپتر داخل آرشیو رو برمی‌گردونه: لیست (اسم داخل آرشیو، نوع، شماره‌ی چپتر یا None)
    مرتب‌شده بر اساس شماره‌ی چپتر (بدون‌شماره‌ها آخر، به ترتیب طبیعی اسم).
    archive: خروجی services.converters._open_archive
    """
    entries = []
    for name in archive.namelist():
        if _is_junk(name):
            continue
        kind = _classify_entry(name, b"")
        if kind is None:
            # پسوند ناشناخته؛ از روی محتوا حدس بزن (مثلاً آرشیوی که پسوندش پاک شده)
            kind = _classify_entry(name, archive.read(name))
        if kind not in CHAPTER_KINDS:
            continue
        label = os.path.splitext(os.path.basename(name))[0]
        entries.append((name, kind, detect_chapter_number(label)))

    entries.sort(key=lambda e: (
        e[2] is None,
        e[2] if e[2] is not None else 0,
        _natural_sort_key(e[0]),
    ))
    return entries
