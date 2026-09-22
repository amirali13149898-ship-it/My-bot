#!/bin/sh
set -e

mkdir -p /var/lib/telegram-bot-api

echo "🚀 در حال اجرای Local Telegram Bot API Server..."
telegram-bot-api \
    --api-id="${TELEGRAM_API_ID}" \
    --api-hash="${TELEGRAM_API_HASH}" \
    --local \
    --http-port=8081 \
    --dir=/var/lib/telegram-bot-api \
    &

# ===== پاکسازی خودکار فایل‌های کش‌شده‌ی Local Bot API =====
# خود telegram-bot-api هیچوقت فایل‌هایی که آپلود/دانلود کرده رو پاک نمی‌کنه،
# برای همین دیسک بی‌نهایت پر می‌شه. اینجا هر ۵ دقیقه یه‌بار چک می‌کنیم و هر
# فایلی که بیشتر از ۳۰ دقیقه (CLEANUP_MAX_AGE_MIN) قدیمی باشه رو پاک می‌کنیم.
# فایل‌های .binlog/.sqlite* رو دست نمی‌زنیم چون حالت داخلی/سشن سرور توشونه؛
# پاک کردنشون می‌تونه سرور رو خراب کنه.
CLEANUP_MAX_AGE_MIN="${CLEANUP_MAX_AGE_MIN:-30}"
CLEANUP_INTERVAL_SEC="${CLEANUP_INTERVAL_SEC:-300}"

(
    while true; do
        sleep "$CLEANUP_INTERVAL_SEC"
        find /var/lib/telegram-bot-api -type f \
            -mmin "+${CLEANUP_MAX_AGE_MIN}" \
            ! -name "*.binlog" \
            ! -name "*.sqlite*" \
            -delete 2>/dev/null
    done
) &

echo "🧹 پاکسازی خودکار فایل‌های قدیمی‌تر از ${CLEANUP_MAX_AGE_MIN} دقیقه فعال شد."

echo "⏳ صبر برای بالا اومدن سرور..."
i=0
while [ "$i" -lt 30 ]; do
    if nc -z localhost 8081 2>/dev/null; then
        echo "✅ Local Bot API Server آماده‌ست."
        break
    fi
    i=$((i + 1))
    sleep 1
done

echo "🚀 در حال اجرای بات پایتون..."
exec python3 bot.py
