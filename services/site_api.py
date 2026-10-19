"""
کلاینت API سایت (ادمین محتوا).

همه‌ی درخواست‌ها هدر X-Bot-Secret-Key دارن. اسم فیلدها و مسیرها فقط اینجا
تعریف شدن؛ اگه قرارداد API سایت یه چیزی‌ش فرق داشت، فقط همین فایل عوض میشه.

توابع sync هستن (با requests) و از handlerها با asyncio.to_thread صدا زده میشن؛
نسخه‌ی async هر کدوم با پیشوند a_ پایین فایل هست.
"""
import asyncio
import time

import requests

from core.config import BASE_URL, BOT_SECRET_KEY, SITE_GET_RETRIES, SITE_TIMEOUT

# ---------------------------------------------------------------- مسیرها
PATH_MANHWAS = "/api/public/bot/manhwas"
PATH_GENRES = "/api/public/bot/genres"
PATH_CHAPTERS = "/api/public/bot/chapters"


def path_manhwa_chapters(manhwa_id):
    return f"/api/public/bot/manhwas/{manhwa_id}/chapters"


# ---------------------------------------------------------------- خطا
class SiteAPIError(Exception):
    """خطای ارتباط با سایت؛ پیامش برای نمایش به کاربر آماده‌ست."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _headers():
    return {
        "X-Bot-Secret-Key": BOT_SECRET_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _error_text(resp):
    """پیام خطای سایت (اگه JSON با message/error بود) یا بخشی از متن."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            for key in ("message", "error", "detail"):
                if data.get(key):
                    return str(data[key])[:300]
    except ValueError:
        pass
    return (resp.text or "").strip()[:200]


def _raise_for_status(resp):
    code = resp.status_code
    if 200 <= code < 300:
        return
    detail = _error_text(resp)
    if code in (401, 403):
        raise SiteAPIError("کلید امنیتی ربات (BOT_SECRET_KEY) از طرف سایت قبول نشد.", code)
    if code == 404:
        raise SiteAPIError(f"آدرس یا آیتم موردنظر روی سایت پیدا نشد. {detail}".strip(), code)
    if code == 422 or code == 400:
        raise SiteAPIError(f"سایت اطلاعات ارسالی رو نپذیرفت: {detail}", code)
    if code == 429:
        raise SiteAPIError("سایت درخواست‌ها رو محدود کرده؛ چند لحظه بعد دوباره امتحان کن.", code)
    if code >= 500:
        raise SiteAPIError(f"خطای سمت سرور سایت (کد {code}). {detail}".strip(), code)
    raise SiteAPIError(f"پاسخ نامنتظر از سایت (کد {code}). {detail}".strip(), code)


def _request(method, path, *, json_body=None, retries=1):
    if not (BASE_URL and BOT_SECRET_KEY):
        raise SiteAPIError("اتصال به سایت تنظیم نشده (BASE_URL یا BOT_SECRET_KEY ست نیست).")

    url = f"{BASE_URL}{path}"
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(
                method, url, headers=_headers(), json=json_body, timeout=SITE_TIMEOUT
            )
        except requests.exceptions.Timeout:
            last_error = SiteAPIError("سایت دیر جواب داد (Timeout).")
        except requests.exceptions.ConnectionError as e:
            last_error = SiteAPIError(f"اتصال به سایت برقرار نشد: {type(e).__name__}")
        except requests.exceptions.RequestException as e:
            last_error = SiteAPIError(f"خطای شبکه موقع ارتباط با سایت: {e}")
        else:
            try:
                _raise_for_status(resp)
            except SiteAPIError as e:
                # فقط خطای سرور (5xx) و محدودیت (429) ارزش تلاش مجدد دارن
                if e.status and (e.status >= 500 or e.status == 429) and attempt < retries:
                    last_error = e
                else:
                    print(f"⚠️ سایت خطا داد [{method} {path}]: {e}")
                    raise
            else:
                try:
                    return resp.json()
                except ValueError:
                    raise SiteAPIError("پاسخ سایت JSON معتبر نبود.", resp.status_code)

        print(f"⚠️ تلاش {attempt}/{retries} برای {method} {path} ناموفق بود: {last_error}")
        if attempt < retries:
            time.sleep(2 * attempt)
    raise last_error


def _as_list(data, *keys):
    """پاسخ ممکنه خودِ لیست باشه یا داخل یه کلید ({"data": [...]} و ...)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys + ("data", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    raise SiteAPIError("ساختار پاسخ سایت قابل‌فهم نبود.")


# ---------------------------------------------------------------- endpointها
def get_manhwas():
    """لیست مانهواها: [{id, title, slug, ...}]"""
    items = _as_list(_request("GET", PATH_MANHWAS, retries=SITE_GET_RETRIES), "manhwas")
    out = []
    for m in items:
        if not isinstance(m, dict) or m.get("id") is None:
            continue
        out.append({
            "id": m["id"],
            "title": str(m.get("title") or m.get("title_fa") or m.get("slug") or m["id"]),
            "title_en": str(m.get("title_en") or ""),
            "slug": str(m.get("slug") or ""),
        })
    return out


def get_genres():
    """لیست ژانرها: [{id, name}]"""
    items = _as_list(_request("GET", PATH_GENRES, retries=SITE_GET_RETRIES), "genres")
    out = []
    for g in items:
        if not isinstance(g, dict) or g.get("id") is None:
            continue
        out.append({"id": g["id"], "name": str(g.get("name") or g.get("title") or g["id"])})
    return out


def get_existing_chapter_numbers(manhwa_id):
    """مجموعه‌ی شماره‌ی چپترهایی که از قبل روی سایت ثبت شدن."""
    items = _as_list(
        _request("GET", path_manhwa_chapters(manhwa_id), retries=SITE_GET_RETRIES), "chapters"
    )
    numbers = set()
    for it in items:
        raw = it.get("chapter_number", it.get("number")) if isinstance(it, dict) else it
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        numbers.add(int(value) if value.is_integer() else value)
    return numbers


def create_manhwa(data):
    """
    data: title_fa, title_en, translators, author, summary, status
    ('ongoing'/'completed')، rating، cover_url، genre_ids. فیلدهای خالی ارسال نمیشن.
    خروجی: dict پاسخ سایت (معمولاً id و slug).
    """
    body = {k: v for k, v in data.items() if v not in (None, "", [])}
    # سایت اسم فارسی رو توی فیلد title هم می‌خواد (طبق قرارداد اولیه)
    if "title_fa" in body:
        body.setdefault("title", body["title_fa"])
    result = _request("POST", PATH_MANHWAS, json_body=body)
    return result if isinstance(result, dict) else {}


MAX_CHAPTERS_PER_REQUEST = 100  # سقف سایت برای هر درخواست


def _post_chapters_chunk(manhwa_id, chunk):
    data = _request(
        "POST", PATH_CHAPTERS, json_body={"manhwa_id": manhwa_id, "chapters": chunk}
    )

    raw_results = None
    if isinstance(data, dict):
        raw_results = data.get("results")
    elif isinstance(data, list):
        raw_results = data

    if not isinstance(raw_results, list):
        return [
            {"chapter_number": c["chapter_number"], "status": "created", "message": ""}
            for c in chunk
        ]

    results = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        status = str(r.get("status") or "").lower()
        if status in ("created", "ok", "success", "added", "inserted"):
            status = "created"
        elif status in ("duplicate", "exists", "already_exists", "skipped"):
            status = "duplicate"
        else:
            status = "error"
        results.append({
            "chapter_number": r.get("chapter_number"),
            "status": status,
            "message": str(r.get("message") or r.get("error") or ""),
        })
    return results


def create_chapters(manhwa_id, chapters):
    """
    chapters: [{chapter_number, title, download_url}]
    خروجی: لیست نتیجه‌ی هر چپتر: [{chapter_number, status, message}]
    status یکی از created / duplicate / error.
    سایت توی هر درخواست حداکثر ۱۰۰ چپتر می‌گیره؛ بیشتر از اون خودکار تیکه‌تیکه میشه.
    اگه تیکه‌ی اول خطا بده، خطا پرتاب میشه (هیچی ثبت نشده)؛ اگه تیکه‌های بعدی
    خطا بدن، چپترهاشون با وضعیت error گزارش میشن و تیکه‌های قبلی حفظ میشن.
    """
    results = []
    for start in range(0, len(chapters), MAX_CHAPTERS_PER_REQUEST):
        chunk = chapters[start:start + MAX_CHAPTERS_PER_REQUEST]
        try:
            results += _post_chapters_chunk(manhwa_id, chunk)
        except SiteAPIError as e:
            if start == 0:
                raise
            results += [
                {"chapter_number": c["chapter_number"], "status": "error", "message": str(e)}
                for c in chunk
            ]
    return results


# ---------------------------------------------------------------- نسخه‌ی async
async def a_get_manhwas():
    return await asyncio.to_thread(get_manhwas)


async def a_get_genres():
    return await asyncio.to_thread(get_genres)


async def a_get_existing_chapter_numbers(manhwa_id):
    return await asyncio.to_thread(get_existing_chapter_numbers, manhwa_id)


async def a_create_manhwa(data):
    return await asyncio.to_thread(create_manhwa, data)


async def a_create_chapters(manhwa_id, chapters):
    return await asyncio.to_thread(create_chapters, manhwa_id, chapters)
