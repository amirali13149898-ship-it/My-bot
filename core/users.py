"""ذخیره‌سازی و مدیریت کاربران (Supabase / فایل محلی)"""
import json
import os
import requests
import threading

from core.config import (
    ADMIN_IDS_SEED,
    ALLOWED_USER_IDS,
    SUPABASE_KEY,
    SUPABASE_ROW_KEY,
    SUPABASE_URL,
    USERS_FILE,
    USE_SUPABASE,
)


def load_users():
    if USE_SUPABASE:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/bot_kv",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                },
                params={"key": f"eq.{SUPABASE_ROW_KEY}", "select": "value"},
                timeout=15,
            )
            r.raise_for_status()
            rows = r.json()
            if rows:
                return rows[0].get("value") or {}
            return {}
        except Exception as e:
            print(f"⚠️ خواندن از Supabase با خطا مواجه شد: {e}")
            return {}

    # حالت پشتیبان: فایل محلی (روی رندر رایگان دائمی نیست!)
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


_SAVE_LOCK = threading.Lock()
_save_seq = 0          # شماره‌ی آخرین درخواست ذخیره (فقط از ترد اصلی افزایش پیدا می‌کنه)
_last_written_seq = 0  # شماره‌ی آخرین ذخیره‌ای که واقعاً نوشته شده


def _write_users(snapshot, seq):
    global _last_written_seq
    with _SAVE_LOCK:
        if seq < _last_written_seq:
            return  # یه ذخیره‌ی جدیدتر قبلاً نوشته شده؛ این نسخه‌ی قدیمی رو دوباره ننویس
        _last_written_seq = seq

        if USE_SUPABASE:
            try:
                r = requests.post(
                    f"{SUPABASE_URL}/rest/v1/bot_kv",
                    headers={
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/json",
                        # merge-duplicates یعنی اگه ردیف با همین key وجود داشت
                        # آپدیت بشه (upsert) نه اینکه خطای تکراری بده
                        "Prefer": "resolution=merge-duplicates,return=minimal",
                    },
                    json=[{"key": SUPABASE_ROW_KEY, "value": snapshot}],
                    timeout=15,
                )
                r.raise_for_status()
            except Exception as e:
                print(f"⚠️ ذخیره در Supabase با خطا مواجه شد: {e} | {getattr(getattr(e, 'response', None), 'text', '')[:300]}")
            return

        # حالت پشتیبان: فایل محلی (روی رندر رایگان دائمی نیست!)
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ ذخیره فایل کاربران با خطا مواجه شد: {e}")


def save_users(users):
    """
    قبلاً این تابع همون لحظه و توی ترد اصلیِ ربات به Supabase درخواست می‌زد
    (تا ۱۵ ثانیه)؛ یعنی هر بار که یه ادمین کاربری رو تایید/رد می‌کرد یا
    /start می‌زد، کل ربات (نوار پیشرفتِ آپلودِ بقیه‌ی ادمین‌ها هم) همین‌قدر
    یخ می‌زد. حالا از داده یه عکس‌فوری (snapshot) می‌گیریم و نوشتنش رو توی
    یه ترد جدا انجام میدیم؛ ترتیب نوشتن هم با شماره‌ی توالی حفظ میشه تا
    یه نسخه‌ی قدیمی روی نسخه‌ی جدیدتر نوشته نشه.
    """
    global _save_seq
    _save_seq += 1
    snapshot = json.loads(json.dumps(users, ensure_ascii=False))
    threading.Thread(target=_write_users, args=(snapshot, _save_seq), daemon=True).start()


# {user_id_str: {first_name, username, allowed, is_admin, uploads}}
USERS = load_users()


def register_user(user):
    uid = str(user.id)
    seed_admin = user.id in ADMIN_IDS_SEED
    if uid not in USERS:
        USERS[uid] = {
            "first_name": user.first_name or "",
            "username": user.username or "",
            "allowed": seed_admin or user.id in ALLOWED_USER_IDS,
            "is_admin": seed_admin,
        }
    else:
        USERS[uid]["first_name"] = user.first_name or ""
        USERS[uid]["username"] = user.username or ""
        USERS[uid]["is_admin"] = bool(USERS[uid].get("is_admin", False)) or seed_admin
        if seed_admin:
            USERS[uid]["allowed"] = True
    save_users(USERS)


def bump_upload(user_id):
    """یکی به شمارنده‌ی آپلودهای موفق این کاربر اضافه می‌کنه (برای بخش آمار)"""
    info = USERS.get(str(user_id))
    if info is None:
        return
    info["uploads"] = int(info.get("uploads", 0)) + 1
    save_users(USERS)


def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS_SEED:
        return True
    info = USERS.get(str(user_id))
    return bool(info and info.get("is_admin", False))


def get_admin_ids():
    ids = {int(uid) for uid, info in USERS.items() if info.get("is_admin")}
    ids |= ADMIN_IDS_SEED
    return ids


def is_allowed(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    info = USERS.get(str(user_id))
    if info is not None:
        return bool(info.get("allowed", False))
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS
