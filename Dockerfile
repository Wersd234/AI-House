# syntax=docker/dockerfile:1

# ─── 显卡单间（VRAM Suite）Discord Bot ───────────────────────────────
# Slim Python runtime. Runs the bot; talks to the host's local LLM
# backend over host.docker.internal (see docker-compose.yml).

FROM python:3.11-slim

# Prefer unbuffered logs so Docker captures them promptly.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1) Install dependencies first (layer-cached, only rebuilt on change)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 2) App source + character role cards + bundled avatar icons
COPY src ./src
COPY properties ./properties
COPY resource ./resource

# 3) Writable data dir (state.json / webhooks.json / server_settings.json)
#    Runs as a non-root user for container safety.
RUN mkdir -p /app/data && chown -R 1000:1000 /app
USER 1000:1000

# 4) Default: just run the bot. LLM_BASE_URL is injected via env.
CMD ["python3", "src/main.py"]
