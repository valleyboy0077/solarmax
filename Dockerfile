FROM node:22.13.1-bookworm-slim AS web-build

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
COPY requirements.txt /build/requirements.txt
COPY solarmax /build/solarmax
COPY scripts /build/scripts
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && python3 -m pip install --break-system-packages --no-cache-dir -r /build/requirements.txt \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/build
RUN npm run typecheck && npm run lint && npm test \
    && cp src/api/generated.ts /tmp/generated.ts \
    && npm run api:generate \
    && cmp -s src/api/generated.ts /tmp/generated.ts \
    && npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SOLARMAX_DB_PATH=/data/solarmax.db \
    SOLARMAX_HOST=0.0.0.0 \
    SOLARMAX_PORT=9117 \
    SOLARMAX_WEBUI_MODE=react

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY solarmax ./solarmax
COPY README.md ./README.md
RUN rm -rf ./solarmax/static/webui
COPY --from=web-build /build/web/dist ./solarmax/static/webui

RUN useradd -m -u 10001 solarmax \
    && mkdir -p /data \
    && chown -R solarmax:solarmax /app /data

# hadolint ignore=DL3066
USER solarmax

EXPOSE 9117

CMD ["uvicorn", "solarmax.main:app", "--host", "0.0.0.0", "--port", "9117"]
