FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/weread.db

WORKDIR /app

# Build backend, installed once into a cached layer so the package builds below
# can skip pip's build isolation (which would re-download it every time).
RUN pip install "setuptools>=68" wheel

# 1) Install third-party dependencies only, using a stub package so this layer
#    is cached and re-runs only when pyproject.toml changes.
COPY pyproject.toml README.md ./
RUN mkdir -p app && touch app/__init__.py cli.py \
    && pip install --no-build-isolation . \
    && rm -rf app cli.py

# 2) Install the real package without re-resolving deps. Re-runs only on source
#    changes and finishes in a second or two.
COPY app ./app
COPY cli.py ./
RUN pip install --no-deps --no-build-isolation --force-reinstall .

EXPOSE 8765

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8765"]
