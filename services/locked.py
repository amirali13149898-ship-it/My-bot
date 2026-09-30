"""فایل‌های رمزدار: تشخیص رمز، رمزبرداری PDF / ZIP / RAR"""
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
import zlib

import pypdf
import rarfile

try:  # پشتیبانی از ZIP با رمز AES (WinRAR/7-Zip)؛ بدونش فقط ZipCrypto قدیمی باز میشه
    import pyzipper
    _ZipClass = pyzipper.AESZipFile
except ImportError:  # pragma: no cover
    _ZipClass = zipfile.ZipFile

from services.converters import NESTED_ARCHIVE_MAX_DEPTH, _sniff_archive_kind


class WrongPassword(Exception):
    """رمز اشتباهه."""


class ToolMissing(Exception):
    """ابزار خارجی لازم برای باز کردن RAR رمزدار نصب نیست."""


def sniff_locked_kind(raw):
    """'pdf' / 'zip' / 'rar' / None - از روی محتوا، نه پسوند."""
    if b"%PDF-" in raw[:1024]:
        return "pdf"
    return _sniff_archive_kind(raw)


# ==================== تشخیص اینکه رمز داره یا نه ====================

def needs_password(raw, kind):
    if kind == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(raw))
        if not reader.is_encrypted:
            return False
        try:
            len(reader.pages)  # با رمز خالی باز میشه؟ (فقط محدودیت مالک داره)
            return False
        except Exception:
            return True

    if kind == "zip":
        with _ZipClass(io.BytesIO(raw)) as zf:
            return any(i.flag_bits & 0x1 for i in zf.infolist())

    if kind == "rar":
        try:
            with rarfile.RarFile(io.BytesIO(raw)) as rf:
                return rf.needs_password()
        except rarfile.PasswordRequired:
            return True  # هدرها هم رمزگذاری شدن

    return False


# ==================== PDF ====================

def unlock_pdf(raw, password):
    reader = pypdf.PdfReader(io.BytesIO(raw))
    if not reader.is_encrypted:
        return raw
    if int(reader.decrypt(password)) == 0:
        raise WrongPassword()
    writer = pypdf.PdfWriter(clone_from=reader)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ==================== ZIP ====================

def _iter_zip(raw, password):
    with _ZipClass(io.BytesIO(raw)) as zf:
        zf.setpassword(password.encode("utf-8"))
        infos = [i for i in zf.infolist() if not i.is_dir()]

        # قبل از هر کاری رمز رو روی کوچک‌ترین فایلِ رمزدار امتحان کن
        # (خوندن کامل، یعنی CRC هم چک میشه و حدس اشتباهِ ZipCrypto رد نمیشه)
        enc = sorted((i for i in infos if i.flag_bits & 0x1), key=lambda i: i.file_size or 0)
        probe = next((i for i in enc if i.file_size), enc[0] if enc else None)
        if probe is not None:
            try:
                zf.read(probe)
            except (RuntimeError, zipfile.BadZipFile, zlib.error, EOFError):
                raise WrongPassword()

        for info in infos:
            try:
                data = zf.read(info)
            except (RuntimeError, zipfile.BadZipFile, zlib.error, EOFError):
                if info.flag_bits & 0x1:
                    raise WrongPassword()
                continue  # فایل خرابِ بدون رمز، فقط ردش کن
            yield info.filename, data


# ==================== RAR ====================

def _find_rar_tool():
    exe = shutil.which("unrar")
    if exe:
        return "unrar", exe
    for cand in ("7zz", "7z", "7za"):
        exe = shutil.which(cand)
        if exe:
            return "7z", exe
    raise ToolMissing("ابزار unrar روی سرور نصب نیست")


def _iter_rar(raw, password):
    tool, exe = _find_rar_tool()
    with tempfile.TemporaryDirectory(prefix="bot_locked_") as tmp:
        src = os.path.join(tmp, "in.rar")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir)
        with open(src, "wb") as f:
            f.write(raw)

        if tool == "unrar":
            cmd = [exe, "x", "-y", "-idq", f"-p{password}", src, out_dir + os.sep]
        else:
            cmd = [exe, "x", "-y", f"-p{password}", f"-o{out_dir}", src]

        try:
            res = subprocess.run(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("باز کردن RAR بیش از حد طول کشید")
        if res.returncode not in (0, 1):  # ۱ = فقط هشدار
            raise WrongPassword()

        paths = []
        for root, _dirs, files in os.walk(out_dir):
            for fn in files:
                paths.append(os.path.join(root, fn))
        paths.sort()
        for p in paths:
            rel = os.path.relpath(p, out_dir).replace(os.sep, "/")
            with open(p, "rb") as f:
                yield rel, f.read()


# ==================== تبدیل آرشیو رمزدار به ZIP ساده ====================

def decrypt_to_plain_zip(raw, kind, password, depth=1):
    """
    آرشیو رمزدار رو باز می‌کنه و محتواش رو توی یه ZIP بدون رمز (بدون فشرده‌سازی)
    می‌ریزه؛ بعد همون ZIP به تابع آماده‌ی «آرشیو -> PDF» داده میشه.
    آرشیوهای تودرتوی رمزدار هم با همون رمز باز میشن.
    """
    it = _iter_zip(raw, password) if kind == "zip" else _iter_rar(raw, password)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as dst:
        for name, data in it:
            if depth < NESTED_ARCHIVE_MAX_DEPTH:
                inner = _sniff_archive_kind(data)
                if inner:
                    try:
                        if needs_password(data, inner):
                            data = decrypt_to_plain_zip(data, inner, password, depth + 1)
                    except (WrongPassword, ToolMissing):
                        pass  # همون‌طور می‌مونه؛ موقع تبدیل «خراب/رمزدار» حساب میشه
                    except Exception:
                        pass
            dst.writestr(name, data)
    return out.getvalue()
