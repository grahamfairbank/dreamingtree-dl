FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

# ffmpeg: SHN/APE/WAV -> FLAC. flac: FLAC verification/tools. ca-certificates: HTTPS to dreamingtree.org / dmbalmanac.com.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg flac ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user matching the NFS export owner (PUID/PGID 997 used across this stack).
RUN groupadd --system --gid 997 dtdl \
    && useradd --system --uid 997 --gid 997 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin dtdl

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY web/ ./web/
COPY scripts/plex-analyze.sh ./scripts/plex-analyze.sh

USER 997:997

EXPOSE 8092

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8092/healthz', timeout=4)" || exit 1

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8092"]
