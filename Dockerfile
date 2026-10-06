FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

# Dependencies first so this layer is cached until requirements.txt changes.
# --require-hashes: every wheel is verified against requirements.txt; --only-binary: no compiler needed.
COPY requirements.txt .
RUN pip install --require-hashes --only-binary=:all: -r requirements.txt

COPY app ./app
RUN useradd --system --no-create-home app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz')"
# One worker on purpose: the cache is in memory, so more workers would only repeat upstream calls.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
