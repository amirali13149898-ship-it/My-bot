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
