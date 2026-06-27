FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/weread.db

WORKDIR /app

# Install dependencies (and the package) first for better layer caching.
COPY pyproject.toml README.md ./
COPY app ./app
COPY cli.py ./
RUN pip install .

EXPOSE 8765

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8765"]
