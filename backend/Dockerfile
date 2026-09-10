FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app
COPY skills ./skills
COPY evals ./evals
RUN pip install --no-cache-dir .

RUN mkdir -p /data
ENV DATABASE_PATH=/data/evosre.db \
    APP_ENV=production

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
