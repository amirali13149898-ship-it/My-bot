"""تنظیمات و متغیرهای محیطی"""
import os


BOT_TOKEN = os.environ.get("BOT_TOKEN")

# ===== تنظیمات Local Bot API Server =====
# اگه USE_LOCAL_BOT_API روشن باشه (پیش‌فرض: روشن)، بات به‌جای
# api.telegram.org به سرور محلی telegram-bot-api که توی همین کانتینر
# اجرا میشه وصل میشه - همینه که محدودیت حجم فایل رو از ۲۰/۵۰ مگابایت
# به ۲ گیگابایت می‌بره بالا.
USE_LOCAL_BOT_API = os.environ.get("USE_LOCAL_BOT_API", "true").lower() == "true"
LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL", "http://localhost:8081")
# ================================================================

# آیدی عددی کاربرهایی که از قبل اجازه استفاده دارن (فقط برای اولین بار / seed)
ALLOWED_USER_IDS = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

# آیدی عددی ادمین‌ها (فقط برای اولین بار / seed - بعدش از فایل خونده میشه)
ADMIN_IDS_SEED = {
    int(uid.strip())
    for uid in os.environ.get("ADMIN_IDS", "").split(",")
    if uid.strip().isdigit()
}
# ================================================================

USERS_FILE = "users_data.json"
LIST_PAGE_SIZE = 6

# ===== ذخیره‌سازی دائمی با Supabase =====
# روی پلن رایگان Render دیسک موقتیه و هر ری‌استارت/دیپلوی فایل رو پاک
# می‌کنه. برای همین اگه این دو متغیر ست شده باشن، به‌جای فایل روی دیسک،
# از جدول bot_kv توی Supabase (رایگان و دائمی) استفاده می‌کنیم.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)
SUPABASE_ROW_KEY = "users_data"


CATBOX_API = "https://catbox.moe/user/api.php"

# ===== ImgBB (فقط برای «آپلود کاور») =====
# کلید API رو از https://api.imgbb.com بگیر و توی Environment Variables سرویس
# (مثلاً روی Render) با اسم IMGBB_API_KEY ست کن. بدون تاریخ انقضا آپلود
# می‌کنیم، پس عکس‌ها دائمی می‌مونن. اگه کلید ست نباشه، کاورها مثل قبل
# روی Catbox آپلود میشن.
IMGBB_API = "https://api.imgbb.com/1/upload"
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "").strip()

# ===== Imgur (فقط برای «آپلود کاور») =====
# چون توی ایران بدون فیلترشکن باز میشه (برخلاف Catbox/ImgBB که مسدودن)،
# این رو جایگزین ImgBB کردیم. یه اپ رایگان از
# https://api.imgur.com/oauth2/addclient بساز (نوع: "Anonymous usage
# without user authorization")، بعد Client ID رو توی Environment Variables
# سرویس با اسم IMGUR_CLIENT_ID ست کن. اگه ست نشه، کاورها مثل قبل روی
# Catbox آپلود میشن.
IMGUR_API = "https://api.imgur.com/3/image"
IMGUR_CLIENT_ID = os.environ.get("IMGUR_CLIENT_ID", "").strip()

PROGRESS_UPDATE_INTERVAL = 5.0  # ثانیه - هر چند وقت یه‌بار نوار پیشرفت آپدیت بشه (۲ ثانیه باعث Flood control میشد)


# ===== کنترل همزمانی (مهم وقتی چند ادمین با هم کار می‌کنن) =====
# Catbox اگه از یه IP چندتا آپلود سنگین همزمان بگیره (اینجا: IP سرور Render)
# اتصال‌ها رو قطع/محدود می‌کنه، و رم ۵۱۲ مگی هم با چندتا تبدیل PDF همزمان
# پر میشه. برای همین آپلودها و تبدیل‌ها یه صف مشترک دارن. با Environment
# Variable قابل تنظیمه (CATBOX_MAX_CONCURRENT / CONVERT_MAX_CONCURRENT).
def _env_int(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


CATBOX_MAX_CONCURRENT = _env_int("CATBOX_MAX_CONCURRENT", 2)
CONVERT_MAX_CONCURRENT = _env_int("CONVERT_MAX_CONCURRENT", 1)
UPLOAD_RETRIES = 3  # هر آپلود تا ۳ بار تلاش میشه
TELEGRAM_FILE_TIMEOUT = 900  # ثانیه - سقف انتظار برای get_file فایل‌های حجیم
