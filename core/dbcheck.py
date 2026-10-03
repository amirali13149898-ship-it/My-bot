"""تست اتصال Supabase موقع استارت: نتیجه توی لاگ‌های رندر چاپ میشه"""
import requests

from core.config import SUPABASE_KEY, SUPABASE_URL, USE_SUPABASE


def supabase_selftest():
    if not USE_SUPABASE:
        print("ℹ️ تست Supabase: SUPABASE_URL / SUPABASE_SERVICE_KEY ست نشده.")
        return
    base = f"{SUPABASE_URL}/rest/v1/bot_kv"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    try:
        for n in (1, 2):  # دو بار می‌نویسیم: اگه key کلید اصلی نباشه، دومی خطا میده یا تکراری میسازه
            r = requests.post(base, headers=headers, json=[{"key": "selftest", "value": {"n": n}}], timeout=15)
            if r.status_code >= 300:
                print(f"❌ تست Supabase: نوشتن {n} ناموفق ({r.status_code}): {r.text[:300]}")
                return
        r = requests.get(base, headers=headers, params={"key": "eq.selftest", "select": "value"}, timeout=15)
        rows = r.json() if r.status_code < 300 else None
        if rows is None:
            print(f"❌ تست Supabase: خوندن ناموفق ({r.status_code}): {r.text[:300]}")
        elif len(rows) != 1:
            print(f"❌ تست Supabase: {len(rows)} ردیف با یک key ساخته شد؛ ستون key باید PRIMARY KEY باشه (SQL توی راهنما).")
        elif rows[0].get("value") != {"n": 2}:
            print(f"❌ تست Supabase: مقدار آپدیت نشد: {rows[0]}")
        else:
            print("✅ تست Supabase: نوشتن و خوندن درسته.")
        requests.delete(base, headers=headers, params={"key": "eq.selftest"}, timeout=15)
    except Exception as e:
        print(f"❌ تست Supabase: اتصال ناموفق (آدرس SUPABASE_URL رو چک کن): {e}")
