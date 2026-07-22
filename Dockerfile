FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir -e .

EXPOSE 8000
# DATABASE_URL, WRITE_KEYS and CORS_ORIGINS are supplied at run time (compose or
# the platform's secrets) — never baked into a layer. The app runs its schema
# migration itself on startup (Alembic on Postgres, create_all on SQLite via the
# lifespan), so there is no separate migrate step to remember. With no
# DATABASE_URL it boots on a local SQLite file — enough to run, not to persist.
CMD ["sh", "-c", "uvicorn evalhistory.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
