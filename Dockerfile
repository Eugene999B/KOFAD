FROM public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN python scripts/prepare_logo.py
RUN DJANGO_SECRET_KEY=build-only-placeholder-not-for-runtime-0000 DATABASE_URL=postgresql://build:build@localhost/build python manage.py collectstatic --noinput
RUN useradd --create-home app && chown -R app:app /app
USER app
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --threads ${GUNICORN_THREADS:-8} --timeout 60 --access-logfile - --access-logformat '%(h)s %(m)s %(U)s %(s)s %(L)s'"]
