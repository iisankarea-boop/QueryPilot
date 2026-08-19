FROM node:22.14-alpine AS web-builder

WORKDIR /web

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web ./
RUN npm run build

FROM python:3.12.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system querypilot && useradd --system --gid querypilot querypilot

COPY pyproject.toml README.md ./
COPY apps/api/src/querypilot/__init__.py ./apps/api/src/querypilot/__init__.py
RUN python -m pip install ".[postgres]"

COPY apps/api/src ./apps/api/src
RUN python -m pip install --no-deps .

COPY catalog ./catalog
COPY infra/scripts ./infra/scripts
COPY --from=web-builder /web/dist ./apps/web/dist

USER querypilot

EXPOSE 8000

CMD ["uvicorn", "querypilot.transport.http:app", "--host", "0.0.0.0", "--port", "8000"]
