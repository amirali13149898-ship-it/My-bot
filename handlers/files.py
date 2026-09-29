"""دریافت فایل‌ها"""
import gc
import html
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.keyboards import main_menu
from core.tasks import TaskCancelled, _run_heavy, _user_lock
from core.telegram_io import (
    _safe_edit,
    download_with_progress,
    read_and_free,
    upload_catbox_with_progress,
)
from core.users import is_allowed
from handlers.bulk import _label_html
from handlers.convert_flow import _convert_to_pdf_and_upload, _detect_convert_kind
from handlers.reply import process_and_reply
from services.archive_fix import _archive_fix_and_upload
from services.converters import (
    IMAGE_EXTENSIONS,
    _archive_to_pdf_with_stats,
    _describe_stats,
)


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فایل‌های هر کاربر یکی‌یکی (به‌ترتیب) پردازش میشن، ولی دکمه‌ی لغو آزاده
    async with _user_lock(update.effective_user.id):
        await _handle_file_impl(update, context)


async def _handle_file_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id

    if not is_allowed(user_id):
        await msg.reply_text("⛔ شما اجازه استفاده از این ربات رو ندارید.")
        return

    mode = context.user_data.get("mode")

    if mode is None:
        await msg.reply_text(
            "اول یکی از حالت‌ها رو انتخاب کن:",
            reply_markup=main_menu()
        )
        return

    if mode == "cover":
        # فقط عکس قبول میشه
        if msg.photo:
            file_src = msg.photo[-1]
            filename = "cover.jpg"
        else:
            await msg.reply_text("⚠️ تو حالت «آپلود کاور» فقط عکس قبول میشه. فایل دیگه‌ای نفرست.")
            return

    elif mode == "pdf":
        # فقط PDF قبول میشه
        is_pdf = (
            msg.document
            and (
                msg.document.mime_type == "application/pdf"
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".pdf"))
            )
        )
        if is_pdf:
            file_src = msg.document
            filename = msg.document.file_name or "file.pdf"
        else:
            await msg.reply_text("⚠️ تو حالت «آپلود PDF» فقط فایل PDF قبول میشه. فایل دیگه‌ای نفرست.")
            return

    elif mode == "to_pdf":
        kind = _detect_convert_kind(msg)
        if kind is None:
            await msg.reply_text(
                "⚠️ تو حالت «تغییر فرمت به PDF» فقط PDF، ZIP، RAR (یا CBZ/CBR) یا عکس قبول میشه."
            )
            return

        if kind == "image":
            file_src = msg.document if msg.document else msg.photo[-1]
            filename = getattr(file_src, "file_name", None) or "image.jpg"
        else:
            file_src = msg.document
            filename = msg.document.file_name or f"file.{kind}"

        await _convert_to_pdf_and_upload(msg, context, kind, file_src, filename)
        return

    elif mode == "connect":
        # این حالت عکس (به‌صورت فایل/Document ترجیحاً، یا عکس فشرده) قبول می‌کنه
        images_list = context.user_data.setdefault("connect_images", [])

        is_image_doc = msg.document and (
            (msg.document.mime_type and msg.document.mime_type.startswith("image/"))
            or (msg.document.file_name and msg.document.file_name.lower().endswith(IMAGE_EXTENSIONS))
        )

        if is_image_doc:
            file_obj = await msg.document.get_file()
            filename = msg.document.file_name or f"image_{len(images_list) + 1:03d}.jpg"
        elif msg.photo:
            # عکس فشرده - اسم اصلی نداره، پس بر اساس ترتیب دریافت اسم می‌ذاریم
            file_obj = await msg.photo[-1].get_file()
            filename = f"photo_{len(images_list) + 1:03d}.jpg"
        else:
            await msg.reply_text(
                "⚠️ تو حالت «اتصال عکس‌ها» فقط عکس (فایل یا عکس فشرده) قبول میشه."
            )
            return

        try:
            file_bytes = await read_and_free(file_obj)
        except Exception as e:
            await msg.reply_text(f"❌ خطا توی دریافت عکس: {e}")
            return

        images_list.append((filename, file_bytes))

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تمام", callback_data="connect_done"),
                InlineKeyboardButton("❌ انصراف", callback_data="connect_cancel"),
            ]
        ])
        status_text = f"📥 {len(images_list)} عکس دریافت شد. وقتی تموم شد «تمام» رو بزن."

        last_status_id = context.user_data.get("connect_status_msg_id")
        edited = False
        if last_status_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=msg.chat_id,
                    message_id=last_status_id,
                    text=status_text,
                    reply_markup=keyboard,
                )
                edited = True
            except Exception:
                edited = False

        if not edited:
            sent = await msg.reply_text(status_text, reply_markup=keyboard)
            context.user_data["connect_status_msg_id"] = sent.message_id

        return

    elif mode == "archive_fix":
        if not msg.document:
            await msg.reply_text(
                "⚠️ تو حالت «اصلاح و آپلود آرشیو» فایل رو به‌صورت Document بفرست (نه عکس)."
            )
            return
        await _archive_fix_and_upload(msg, context)
        return

    elif mode == "bulk_upload":
        # هر فایل (PDF یا ZIP) همون لحظه‌ای که میاد پردازش میشه:
        # دریافت -> (اگه ZIP بود) تبدیل به PDF -> آپلود -> فرستادن لینک -> فایل بعدی.
        # هیچ فایلی توی رم یا دیسک جمع نمیشه، پس دکمه‌ی «تمام» لازم نیست.
        is_pdf = (
            msg.document
            and (
                msg.document.mime_type == "application/pdf"
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".pdf"))
            )
        )
        is_zip = (
            msg.document
            and (
                msg.document.mime_type in ("application/zip", "application/x-zip-compressed")
                or (msg.document.file_name and msg.document.file_name.lower().endswith(".zip"))
            )
        )

        if not (is_pdf or is_zip):
            await msg.reply_text(
                "⚠️ تو حالت «آپلود گروهی» فقط فایل PDF یا ZIP قبول میشه."
            )
            return

        original_name = msg.document.file_name or "file"
        label = os.path.splitext(original_name)[0] or "file"
        safe_label = _label_html(label)
        dl_label = original_name

        status = await msg.reply_text(f"⬇️ در حال دریافت {dl_label}...")
        try:
            file_bytes = await download_with_progress(
                msg.document, status, msg.document.file_size, prefix=f"⬇️ در حال دریافت {dl_label}..."
            )
        except TaskCancelled:
            await _safe_edit(status, f"❌ دریافت {dl_label} لغو شد.")
            return
        except Exception as e:
            try:
                await status.edit_text(f"❌ خطا توی دریافت {dl_label}: {e}")
            except Exception:
                pass
            return

        note = ""
        if is_zip:
            await _safe_edit(status, f"🛠 در حال تبدیل «{label}» به PDF...")
            try:
                upload_bytes, stats = await _run_heavy(_archive_to_pdf_with_stats, file_bytes, "zip")
            except Exception as e:
                file_bytes = None
                try:
                    await status.edit_text(f"📄 {safe_label}\n⚠️ خطا در تبدیل زیپ: {html.escape(str(e))}", parse_mode="HTML")
                except Exception:
                    pass
                return
            file_bytes = None
            if upload_bytes is None:
                try:
                    await status.edit_text(
                        f"📄 {safe_label}\n⚠️ هیچ عکس یا PDF معتبری توی زیپ پیدا نشد یا زیپ خراب بود",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return
            upload_filename = f"{label}.pdf"
            note = _describe_stats(stats)
        else:
            upload_bytes = file_bytes
            file_bytes = None
            upload_filename = original_name if original_name.lower().endswith(".pdf") else f"{label}.pdf"

        try:
            link = await upload_catbox_with_progress(
                upload_filename, upload_bytes, status,
                prefix=f"⬆️ در حال آپلود «{upload_filename}»",
            )
        except TaskCancelled:
            upload_bytes = None
            await _safe_edit(status, f"❌ آپلود {label} لغو شد.")
            return
        upload_bytes = None
        gc.collect()

        if link:
            text = f"📄 {safe_label}\n<code>{link}</code>"
            if note:
                text += f"\n\n{html.escape(note)}"
        else:
            text = f"📄 {safe_label}\n⚠️ آپلود به Catbox ناموفق بود"
        try:
            await status.edit_text(text, parse_mode="HTML")
        except Exception:
            try:
                await msg.reply_text(text, parse_mode="HTML")
            except Exception:
                pass
        # حالت گروهی فعال می‌مونه تا فایل بعدی رو بفرستی
        return

    else:
        return

    status = None
    try:
        status = await msg.reply_text(f"⬇️ در حال دریافت {filename}...")
        file_bytes = await download_with_progress(
            file_src, status, getattr(file_src, "file_size", None),
            prefix=f"⬇️ در حال دریافت {filename}..."
        )
        await process_and_reply(
            msg, filename, file_bytes, context, status_msg=status,
            host="catbox"
        )
    except TaskCancelled:
        if status:
            await _safe_edit(status, "❌ دریافت فایل لغو شد.")
        context.user_data["mode"] = None
        await msg.reply_text(
            "یکی از حالت‌ها رو انتخاب کن:",
            reply_markup=main_menu()
        )
    except Exception as e:
        # هر خطایی که پیش بیاد، ربات کرش نمی‌کنه و به کاربر اطلاع میده
        await msg.reply_text(f"❌ خطایی پیش اومد: {e}")
        context.user_data["mode"] = None
        await msg.reply_text(
            "یکی از حالت‌ها رو انتخاب کن:",
            reply_markup=main_menu()
          )
