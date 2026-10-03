"""بخش سرگرمی: ذخیره‌ی محتوا، انتخاب رندوم بدون تکرار و نمایش حین آپلود"""
import html
import json
import os
import random
import threading
import time
import uuid

import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
)

from core.config import SUPABASE_KEY, SUPABASE_URL, USE_SUPABASE
from core.tasks import _TASKS

FUN_ROW_KEY = "fun_items"
FUN_FILE = "fun_data.json"
SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "fun_seed.json")
COOLDOWN = 5.0  # ثانیه بین دو بار زدن دکمه برای هر کاربر

CAT_TITLES = {"joke": "😂 جوک", "riddle": "🧩 چیستان", "fact": "💡 دانستنی"}


# ---------------------------------------------------------------- ذخیره‌سازی
def _read_seed():
    try:
        with open(SEED_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    items = []
    for r in raw:
        r.setdefault("file_id", None)
        r.setdefault("buttons", [])
        r.setdefault("answer", None)
        r["id"] = uuid.uuid4().hex[:8]
        items.append(r)
    return items


def _load():
    data = None
    read_ok = True
    if USE_SUPABASE:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/bot_kv",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
                params={"key": f"eq.{FUN_ROW_KEY}", "select": "value"},
                timeout=15,
            )
            r.raise_for_status()
            rows = r.json()
            if rows:
                data = rows[0].get("value")
        except Exception as e:
            read_ok = False
            print(f"⚠️ خواندن سرگرمی از Supabase ناموفق بود: {e}")
    elif os.path.exists(FUN_FILE):
        try:
            with open(FUN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    # اولین اجرا: محتوای اولیه (اگه خوندن خطا داده بود ذخیره نمی‌کنیم تا داده‌ی واقعی رو بازنویسی نکنه)
    items = _read_seed()
    if read_ok:
        _save(items)
    return items


_SAVE_LOCK = threading.Lock()
_save_seq = 0
_written_seq = 0


def _write(snapshot, seq):
    global _written_seq
    with _SAVE_LOCK:
        if seq < _written_seq:
            return
        _written_seq = seq
        payload = {"items": snapshot}
        if USE_SUPABASE:
            try:
                r = requests.post(
                    f"{SUPABASE_URL}/rest/v1/bot_kv",
                    headers={
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "resolution=merge-duplicates,return=minimal",
                    },
                    json=[{"key": FUN_ROW_KEY, "value": payload}],
                    timeout=15,
                )
                r.raise_for_status()
            except Exception as e:
                print(f"⚠️ ذخیره‌ی سرگرمی در Supabase ناموفق بود: {e} | {getattr(getattr(e, 'response', None), 'text', '')[:300]}")
            return
        try:
            with open(FUN_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ ذخیره‌ی فایل سرگرمی ناموفق بود: {e}")


def _save(items):
    """ذخیره توی ترد جدا تا ربات (و نوار پیشرفت آپلودها) یخ نزنه."""
    global _save_seq
    _save_seq += 1
    snap = json.loads(json.dumps(items, ensure_ascii=False))
    threading.Thread(target=_write, args=(snap, _save_seq), daemon=True).start()


ITEMS = _load()


def add_item(item):
    item["id"] = uuid.uuid4().hex[:8]
    ITEMS.append(item)
    _save(ITEMS)
    return item["id"]


def delete_item(item_id):
    for i, it in enumerate(ITEMS):
        if it["id"] == item_id:
            ITEMS.pop(i)
            _save(ITEMS)
            return True
    return False


def get_item(item_id):
    return next((it for it in ITEMS if it["id"] == item_id), None)


def counts():
    c = {k: 0 for k in CAT_TITLES}
    for it in ITEMS:
        c[it["cat"]] = c.get(it["cat"], 0) + 1
    return c


# ------------------------------------------------------- انتخاب رندوم بدون تکرار
_DECKS = {}  # user_id -> [item_id,...] (مواردی که هنوز نشون داده نشده)
_LAST_SEEN = {}  # user_id -> آخرین آیدی نمایش‌داده‌شده
_LAST_CLICK = {}  # user_id -> زمان آخرین کلیک


def pick_item(user_id):
    if not ITEMS:
        return None
    ids = {it["id"] for it in ITEMS}
    deck = [i for i in _DECKS.get(user_id, []) if i in ids]
    if not deck:
        deck = list(ids)
        random.shuffle(deck)
        # اولین مورد دسته‌ی جدید با آخرین مورد دسته‌ی قبلی یکی نباشه
        if len(deck) > 1 and deck[-1] == _LAST_SEEN.get(user_id):
            deck[0], deck[-1] = deck[-1], deck[0]
    item_id = deck.pop()
    _DECKS[user_id] = deck
    _LAST_SEEN[user_id] = item_id
    if len(_DECKS) > 2000:
        _DECKS.clear()
    return get_item(item_id)


def cooldown_left(user_id):
    """چند ثانیه‌ی دیگه باید صبر کنه (۰ یعنی مجازه) و اگه مجازه زمان رو ثبت می‌کنه."""
    now = time.monotonic()
    left = COOLDOWN - (now - _LAST_CLICK.get(user_id, -1e9))
    if left > 0:
        return left
    _LAST_CLICK[user_id] = now
    if len(_LAST_CLICK) > 5000:
        _LAST_CLICK.clear()
    return 0.0


# ---------------------------------------------------------------- نمایش
def _keyboard(item, task_id):
    rows = []
    for row in item.get("buttons") or []:
        r = [InlineKeyboardButton(b["text"], url=b["url"]) for b in row if b.get("text") and b.get("url")]
        if r:
            rows.append(r)
    ctrl = []
    if item["cat"] == "riddle" and item.get("answer"):
        ctrl.append(InlineKeyboardButton("💡 جواب", callback_data=f"fun_ans_{task_id}"))
    ctrl.append(InlineKeyboardButton("🎲 بعدی", callback_data=f"fun_next_{task_id}"))
    rows.append(ctrl)
    return InlineKeyboardMarkup(rows)


def _caption(item):
    body = item.get("text") or ""
    if item["type"] == "text":
        return f"<b>{CAT_TITLES.get(item['cat'], '🎭 سرگرمی')}</b>\n\n{body}"
    return body or None


async def _send(bot, chat_id, item, kb):
    t, cap, fid = item["type"], _caption(item), item.get("file_id")
    if t == "text":
        return await bot.send_message(chat_id, cap, parse_mode="HTML", reply_markup=kb,
                                      disable_web_page_preview=True)
    send = getattr(bot, {"photo": "send_photo", "video": "send_video", "animation": "send_animation"}[t])
    return await send(chat_id, fid, caption=cap, parse_mode="HTML", reply_markup=kb)


def _input_media(item):
    cls = {"photo": InputMediaPhoto, "video": InputMediaVideo, "animation": InputMediaAnimation}[item["type"]]
    return cls(item["file_id"], caption=_caption(item), parse_mode="HTML")


async def show_item(bot, task_id, item):
    """
    یه پیام سرگرمی برای هر آپلود: اگه از قبل هست و نوعش (متن/رسانه) یکیه همون
    ادیت میشه؛ در غیر این صورت پاک و پیام جدید فرستاده میشه.
    """
    state = _TASKS.get(task_id)
    if state is None:
        return
    chat_id = state["chat_id"]
    fun = state.setdefault("fun", {})
    kb = _keyboard(item, task_id)
    fun["answer"] = item.get("answer")
    mid, prev = fun.get("message_id"), fun.get("kind")
    new = item["type"]

    if mid:
        try:
            if prev == "text" and new == "text":
                await bot.edit_message_text(
                    _caption(item), chat_id=chat_id, message_id=mid, parse_mode="HTML",
                    reply_markup=kb, disable_web_page_preview=True,
                )
                return
            if prev != "text" and new != "text":
                await bot.edit_message_media(
                    _input_media(item), chat_id=chat_id, message_id=mid, reply_markup=kb
                )
                fun["kind"] = new
                return
        except Exception:
            pass
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass
        fun["message_id"] = None

    msg = await _send(bot, chat_id, item, kb)
    fun["message_id"], fun["kind"] = msg.message_id, new


async def cleanup_fun(bot, task_id):
    """آخر آپلود پیام سرگرمی پاک میشه تا چت شلوغ نمونه."""
    state = _TASKS.get(task_id)
    mid = ((state or {}).get("fun") or {}).get("message_id")
    if mid:
        try:
            await bot.delete_message(state["chat_id"], mid)
        except Exception:
            pass


def preview(item, limit=28):
    import re
    plain = re.sub(r"<[^>]+>", "", item.get("text") or "")
    plain = html.unescape(plain).replace("\n", " ").strip()
    icon = {"text": "", "photo": "🖼 ", "video": "🎬 ", "animation": "🎞 "}.get(item["type"], "")
    return icon + (plain[:limit] + ("…" if len(plain) > limit else "") if plain else "(بدون متن)")
