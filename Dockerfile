# Milieu Labs / Energy Strategy web app.
# Data (projects, CIBSE weather, Milieu rates) is mounted at /data and never baked into the image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MILIEU_DATA=/data PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Chromium for HTML -> PDF (scripts/html2pdf.js)
RUN npm init -y >/dev/null && npm install playwright@1.48.2 && npx playwright install --with-deps chromium
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY library ./library
COPY scripts ./scripts
RUN pip install --no-cache-dir uv && uv pip install --system --no-cache .
RUN useradd -m milieu && mkdir -p /data && chown milieu /data
USER milieu
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "ambient_loop.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
