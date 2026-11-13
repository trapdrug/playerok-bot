FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DATA_DIR=/data \
    DATABASE_URL=sqlite+aiosqlite:////data/playerok.db \
    SELECTORS_FILE=/app/selectors.yaml \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC

WORKDIR /app

# dumb-init becomes PID 1 and reaps the orphaned browser processes, otherwise
# they pile up as zombies and the container runs out of process slots.
RUN apt-get update \
    && apt-get install -y --no-install-recommends dumb-init \
    && rm -rf /var/lib/apt/lists/*

# The time zone database (Europe/Moscow and friends) comes from the `tzdata`
# Python package in requirements.txt, so no interactive apt wizard is needed.

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY selectors.yaml ./selectors.yaml

RUN mkdir -p /app/data /data

ENTRYPOINT ["/usr/bin/dumb-init", "--"]

# The official Playwright image already contains the matching Chromium build.
CMD ["python", "-m", "app.main"]
