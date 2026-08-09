FROM python:3.13-slim AS builder

ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt

FROM python:3.13-slim AS runtime

ARG APP_VERSION=unknown
ARG GIT_COMMIT=unknown
ENV APP_VERSION="$APP_VERSION" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
LABEL org.opencontainers.image.title="demo-app" \
      org.opencontainers.image.version="$APP_VERSION" \
      org.opencontainers.image.revision="$GIT_COMMIT"

RUN groupadd --system app && useradd --system --gid app --home-dir /app app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app.py .

USER app
EXPOSE 5000

CMD ["gunicorn", "--bind=0.0.0.0:5000", "--workers=2", "--threads=2", "--timeout=30", "app:app"]
