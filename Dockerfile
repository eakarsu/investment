FROM python:3.12-slim AS python-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app app
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .
USER app

FROM python-runtime AS api
EXPOSE 8080
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python-runtime AS migrate
CMD ["python", "-m", "backend.scripts.migrate"]

FROM node:22-slim AS web-build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM node:22-slim AS web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app app
COPY --from=web-build --chown=app:app /app ./
USER app
EXPOSE 3000
CMD ["npm", "run", "start"]
