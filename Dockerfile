FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN DJANGO_SECRET_KEY=build-only-placeholder-not-for-runtime-0000 DATABASE_URL=postgresql://build:build@localhost/build python manage.py collectstatic --noinput
RUN useradd --create-home app && chown -R app:app /app
USER app
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4 --timeout 60 --access-logfile -"]
