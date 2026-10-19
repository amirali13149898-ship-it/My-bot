"""تشخیص شماره‌ی چپتر از اسم فایل + ابزارهای نرمال‌سازی متن/عدد"""
import difflib
import re

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_TO_ASCII = {ord(c): str(i) for i, c in enumerate(_PERSIAN_DIGITS)}
_TO_ASCII.update({ord(c): str(i) for i, c in enumerate(_ARABIC_DIGITS)})
_TO_PERSIAN = {ord(str(i)): c for i, c in enumerate(_PERSIAN_DIGITS)}

# کلمه‌هایی که بعدشون شماره‌ی چپتر میاد (به ترتیب اولویت اهمیتی ندارن؛ اولین تطابق در اسم فایل برنده‌ست)
_KEYWORD_RE = re.compile(
    r"(?:(?<![A-Za-z])(?:chapter|chap|ch|episode|ep)|چپتر|فصل|قسمت|#)"
    r"\s*[\.\-_:]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_ANY_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def to_ascii_digits(text):
    return (text or "").translate(_TO_ASCII)


def to_persian_digits(text):
    return str(text).translate(_TO_PERSIAN)


def _to_number(raw):
    value = float(raw)
    return int(value) if value.is_integer() else value


def fmt_num(n):
    """۱۲ -> '12'، ۱۲٫۵ -> '12.5'"""
    if n is None:
        return "؟"
    return str(int(n)) if float(n).is_integer() else str(n)


def parse_number(text):
    """
    متنِ کاربر رو به عدد تبدیل می‌کنه (ارقام فارسی/عربی هم قبوله).
    خروجی: عدد مثبت (int/float) یا None اگه معتبر نبود.
    """
    t = to_ascii_digits(text).strip().replace("٫", ".").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    n = _to_number(t)
    return n if n > 0 else None


def detect_chapter_number(filename):
    """
    شماره‌ی چپتر رو از اسم فایل درمیاره:
    ۱) عددی که بعد از ch / chapter / ep / چپتر / فصل / قسمت / # میاد (اولین تطابق)
    ۲) اگه کلمه‌ی کلیدی نبود و فقط یه عدد توی اسم بود، همون
    ۳) در غیر این صورت None (ربات از کاربر می‌پرسه)
    """
    name = to_ascii_digits(filename or "")
    name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)  # پسوند فایل
    name = name.replace("_", " ")

    m = _KEYWORD_RE.search(name)
    if m:
        n = _to_number(m.group(1))
        return n if n > 0 else None

    numbers = _ANY_NUMBER_RE.findall(name)
    if len(numbers) == 1:
        n = _to_number(numbers[0])
        return n if n > 0 else None
    return None


# ---------------------------------------------------------------- جست‌وجوی مانهوا
def normalize_text(text):
    t = to_ascii_digits(text or "").lower()
    t = t.replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("ة", "ه")
    t = re.sub(r"[\u200c\u200d\u200e\u200f\u064b-\u065f]", "", t)  # نیم‌فاصله و اعراب
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def search_manhwas(manhwas, query, limit=10):
    """
    لیست ایندکس مانهواهایی که با query می‌خونن (اول تطابق مستقیم، بعد حدس نزدیک).
    هر آیتم manhwas یه dict با کلیدهای title/slug (و اختیاری title_en) است.
    """
    q = normalize_text(query)
    if not q:
        return []

    def haystack(m):
        return normalize_text(f"{m.get('title', '')} {m.get('title_en', '')} {m.get('slug', '')}")

    direct = [i for i, m in enumerate(manhwas) if q in haystack(m)]
    if direct:
        return direct[:limit]

    scored = []
    for i, m in enumerate(manhwas):
        title = normalize_text(m.get("title", ""))
        ratio = difflib.SequenceMatcher(None, q, title).ratio()
        if ratio >= 0.55:
            scored.append((ratio, i))
    scored.sort(reverse=True)
    return [i for _, i in scored[:limit]]
