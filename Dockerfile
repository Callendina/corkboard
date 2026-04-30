# Corkboard image — single FastAPI service, sqlite-backed.
#
# Build with the cyclops package available at the repo root via the
# canonical git URL (no PyPI publish yet). Image is built on staging
# during deploy; promote-to-prod streams the bytes via docker save|load.

FROM python:3.12-slim

# Git is needed at install time for the cyclops git+URL pip dep.
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir 'cyclops @ git+https://github.com/Callendina/cyclops.git#subdirectory=packages/cyclops'

COPY . /app

# Strip git binary now that pip install is done.
RUN apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 9200

# Healthcheck — corkboard's default mount_prefix is /corkboard, but the
# bare /health endpoint is unconditional (mount-prefix-independent).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9200/health', timeout=3).status == 200 else 1)" \
        || exit 1

# Run uvicorn directly (CLI), not via run.py's programmatic uvicorn.run().
# Both work, but the CLI form is what cyclops-ui / vispay use; staying
# consistent helps when comparing logs.
CMD ["uvicorn", "corkboard.app:app", "--host", "0.0.0.0", "--port", "9200"]
