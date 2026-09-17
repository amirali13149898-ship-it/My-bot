# ===== مرحله ۱: گرفتن باینری آماده‌ی Local Bot API Server =====
# این ایمیج فقط برای کپی کردن باینری استفاده میشه، خودش اجرا نمیشه
FROM aiogram/telegram-bot-api:latest AS botapi

# ===== مرحله ۲: ایمیج نهایی - همون بیس آلپاینی که باینری روش تست شده =====
# روی همون بیس (Alpine) می‌مونیم که باینری telegram-bot-api باهاش ساخته
# شده تا مشکل ناسازگاری کتابخونه‌ای (glibc/musl) پیش نیاد.
FROM alpine:3.20

# باینری Local Bot API Server
COPY --from=botapi /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

# پایتون + ابزارهای لازم برای نصب Pillow/img2pdf
RUN apk add --no-cache \
    python3 py3-pip \
    jpeg zlib libjpeg-turbo \
    ca-certificates

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY bot.py .
COPY start.sh .
RUN chmod +x start.sh

# مقادیر پیش‌فرض تستی (عمومی، منتشرشده توسط خود تلگرام برای تست).
# اگه بعداً api_id/api_hash اختصاصی گرفتی، این دو تا رو توی
# Environment Variables خود سرویس روی Render override کن (نیازی
# به تغییر این فایل و ری‌بیلد نیست).
ENV TELEGRAM_API_ID=17349
ENV TELEGRAM_API_HASH=344583e45741c457fe1862106095a5eb

EXPOSE 8080

CMD ["./start.sh"]
