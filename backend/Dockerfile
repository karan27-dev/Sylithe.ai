FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY sylithe ./sylithe
RUN pip install --no-cache-dir .

# Non-root runtime user; workspace volume mounted at /workspace
RUN useradd -m sylithe && mkdir -p /workspace /data && chown -R sylithe /workspace /data
USER sylithe

ENV WORKSPACE_ROOT=/workspace \
    DATABASE_URL=sqlite:////data/sylithe.db \
    AUDIT_LOG_PATH=/data/audit.log.jsonl

EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

CMD ["uvicorn", "--factory", "sylithe.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
