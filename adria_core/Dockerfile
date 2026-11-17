ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-aiohttp

COPY app /app
WORKDIR /app

ENTRYPOINT []
CMD ["python3", "/app/main.py"]
