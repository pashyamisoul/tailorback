# TailorBack production image.
# Includes LibreOffice so the server can convert generated .docx files to .pdf.
FROM python:3.12-slim

# System deps:
#   libreoffice-writer  -> docx -> pdf conversion (docx_builder.to_pdfs)
#   fonts-*             -> readable, consistent PDF rendering
#   curl                -> container health check
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-liberation \
        fonts-dejavu \
        curl \
    && rm -rf /var/lib/apt/lists/*

# LibreOffice needs a writable HOME for its user profile.
ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first so Docker can cache this layer.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code.
COPY . .

# The platform (Render/Fly/etc.) injects $PORT; default to 8000 for local runs.
ENV PORT=8000
EXPOSE 8000

# 1 worker keeps the in-memory rate limiter + SSE generation queues consistent;
# threads handle concurrent streaming requests. timeout covers long AI calls.
CMD ["sh", "-c", "gunicorn --chdir backend --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 120 app:app"]
