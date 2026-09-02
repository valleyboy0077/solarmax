FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1     SOLARMAX_DB_PATH=/data/solarmax.db     SOLARMAX_HOST=0.0.0.0     SOLARMAX_PORT=9117

WORKDIR /app

RUN apt-get update     && apt-get install -y --no-install-recommends build-essential curl     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY solarmax ./solarmax
COPY README.md ./README.md

RUN useradd -m -u 10001 solarmax     && mkdir -p /data     && chown -R solarmax:solarmax /app /data

USER solarmax

EXPOSE 9117

CMD ["uvicorn", "solarmax.main:app", "--host", "0.0.0.0", "--port", "9117"]
