ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-aiohttp tzdata poppler-utils \
    && (apk add --no-cache py3-pypdf py3-reportlab \
        || (apk add --no-cache py3-pip && pip install --no-cache-dir --break-system-packages pypdf reportlab)) \
    && (apk add --no-cache py3-numpy || true)

COPY app /app
WORKDIR /app

ENTRYPOINT []
CMD ["python3", "/app/main.py"]
