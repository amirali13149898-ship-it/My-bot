FROM aiogram/telegram-bot-api:latest AS botapi

FROM alpine:3.20

COPY --from=botapi /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

COPY requirements.txt /tmp/requirements.txt

RUN apk add --no-cache \
    python3 py3-pip \
    jpeg zlib libjpeg-turbo \
    libarchive \
    libarchive-tools \
    findutils \
    libheif \
    libavif \
    cairo pango gdk-pixbuf fontconfig ttf-dejavu \
    ghostscript \
    libraw \
    ca-certificates \
    && apk add --no-cache --virtual .build-deps \
    gcc g++ musl-dev python3-dev make pkgconfig libraw-dev \
    && pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && apk del .build-deps \
    && rm -rf /root/.cache /tmp/*

# برای باز کردن RAR رمزدار (اگه پکیج پیدا نشه بیلد خراب نمیشه)
RUN apk add --no-cache 7zip || echo "⚠️ 7zip نصب نشد؛ RAR رمزدار باز نمیشه"

WORKDIR /app

COPY bot.py start.sh ./
COPY core ./core
COPY services ./services
COPY handlers ./handlers
COPY data ./data
RUN chmod +x start.sh

ENV TELEGRAM_API_ID=17349
ENV TELEGRAM_API_HASH=344583e45741c457fe1862106095a5eb

EXPOSE 8080

CMD ["./start.sh"]
