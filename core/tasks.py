"""صف‌ها، سمافورها و مدیریت عملیات‌های در حال اجرا (لغو)"""
import asyncio
import uuid
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import CATBOX_MAX_CONCURRENT, CONVERT_MAX_CONCURRENT


_UPLOAD_SEM = asyncio.Semaphore(CATBOX_MAX_CONCURRENT)
_CONVERT_SEM = asyncio.Semaphore(CONVERT_MAX_CONCURRENT)


async def _run_heavy(func, *args):
    """کارهای سنگین (ساخت PDF) رو توی صف مشترک و توی ترد جدا اجرا می‌کنه."""
    async with _CONVERT_SEM:
        return await asyncio.to_thread(func, *args)


# ===== مدیریت عملیات‌های در حال اجرا (برای دکمه‌ی «❌ لغو») =====
# هر دانلود/آپلود یه task_id کوتاه می‌گیره. دکمه‌ی لغو همین id رو توی
# callback_data می‌بره و button_handler با اون، عملیات درست رو متوقف می‌کنه.
_TASKS = {}


class TaskCancelled(Exception):
    """وقتی کاربر دکمه‌ی «❌ لغو» رو بزنه پرتاب میشه."""


def _new_task(chat_id):
    task_id = uuid.uuid4().hex[:8]
    _TASKS[task_id] = {"chat_id": chat_id, "cancelled": False, "task": None}
    return task_id


def _end_task(task_id):
    _TASKS.pop(task_id, None)


def cancel_keyboard(task_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ لغو", callback_data=f"cancel_task_{task_id}")]
    ])


# قفل جداگانه برای هر کاربر: چون آپدیت‌ها همزمان پردازش میشن (تا دکمه‌ی لغو
# وسط آپلود کار کنه)، فایل‌های یه کاربر باید همچنان یکی‌یکی و به‌ترتیب پردازش بشن.
_USER_LOCKS = {}


def _user_lock(user_id):
    lock = _USER_LOCKS.get(user_id)
    if lock is None:
        lock = _USER_LOCKS[user_id] = asyncio.Lock()
    return lock
