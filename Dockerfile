FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.1.0 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    postgresql-client \
    tesseract-ocr \
    tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/*

# tesserocr keeps one Tesseract engine loaded per thread instead of
# spawning a new OS process per OCR call (what pytesseract does) — needs
# this to find the language data without an explicit path at call sites.
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata/

RUN pip install "poetry==$POETRY_VERSION"

COPY pyproject.toml ./
RUN poetry install --only main --no-root --no-ansi

COPY . .

EXPOSE 8000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
