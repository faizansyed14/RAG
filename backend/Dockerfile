FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Exact versions come from requirements.lock (a `pip freeze` of the tested image);
# requirements.txt only names what the app needs. Test tooling is not installed.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock

# Run as an unprivileged user; the tree engine's data dir must be writable by it.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/.rag-data \
    && chown -R app:app /app
COPY --chown=app:app . .
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=6s --start-period=40s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).status == 200 else 1)"

# Migrations run before the server starts (single-instance deployments; run
# `alembic upgrade head` as a separate job instead if you scale out).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header"]
