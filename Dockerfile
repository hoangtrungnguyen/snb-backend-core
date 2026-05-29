# --- Build stage ---
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies needed to compile some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt

# --- Runtime stage ---
FROM python:3.11-slim AS runtime

WORKDIR /app

# Only runtime system library needed for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from the builder stage
COPY --from=builder /install /usr/local

# Copy the project source
COPY . .

# Collect static files (may be a no-op without storage backend configured)
# ENV DJANGO_SETTINGS_MODULE is expected from the runtime environment (.env)
# Fallback default so the image works standalone; override at runtime for prod
# (e.g. DJANGO_SETTINGS_MODULE=spb_core.settings.prod once the settings split lands).
ENV DJANGO_SETTINGS_MODULE=spb_core.settings.prod \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Collect static for WhiteNoise. SECRET_KEY/ALLOWED_HOSTS/DATABASE_URL aren't
# needed at collectstatic time, but prod settings require them — use dummies.
RUN SECRET_KEY=build-only ALLOWED_HOSTS=* DATABASE_URL=sqlite:///tmp/build.db \
    python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "spb_core.wsgi:application"]
