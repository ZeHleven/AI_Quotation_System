# syntax=docker/dockerfile:1.7

FROM public.ecr.aws/docker/library/node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS web-build

WORKDIR /build/ai-web
COPY ai-web/package.json ai-web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ai-web/index.html ai-web/vite.config.js ./
COPY ai-web/public ./public
COPY ai-web/src ./src
RUN npm run build


FROM public.ecr.aws/docker/library/python:3.11-alpine3.23@sha256:f73754c398b259dfbbe482361dca8b464dea57da74efe5214966ca2ee767ee12 AS runtime

ARG APP_VERSION=candidate
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
LABEL org.opencontainers.image.title="AI Middle Office App Node" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AUTO_RUN_DB_MIGRATIONS=false \
    PYTHONPATH=/opt/ai-middle-office/AI_Middle_Office

RUN addgroup -S -g 10001 aiapp \
    && adduser -S -D -H -u 10001 -G aiapp -s /sbin/nologin aiapp

WORKDIR /opt/ai-middle-office

COPY AI_Middle_Office/requirements-production.lock /tmp/requirements.lock
RUN python -m pip install \
        --index-url "${PIP_INDEX_URL}" \
        --require-hashes \
        --no-deps \
        --only-binary=:all: \
        --timeout 60 \
        --retries 5 \
        --progress-bar off \
        --no-cache-dir \
        -r /tmp/requirements.lock \
    && python -m pip check \
    && python -m pip uninstall --yes jaraco.context setuptools wheel \
    && python -m pip uninstall --yes pip \
    && python -c "import fastapi, uvicorn, sqlalchemy, jwt, PIL, pypdf, multipart, urllib3" \
    && rm -f /tmp/requirements.lock

COPY --chown=root:root AI_Middle_Office/app ./AI_Middle_Office/app
COPY --chown=root:root AI_Middle_Office/mcp_servers ./AI_Middle_Office/mcp_servers
COPY --chown=root:root AI_Middle_Office/alembic ./AI_Middle_Office/alembic
COPY --chown=root:root AI_Middle_Office/alembic.ini ./AI_Middle_Office/alembic.ini
COPY --chown=root:root app.html index.html admin.html ./
COPY --chown=root:root static ./static
COPY --from=web-build --chown=root:root /build/ai-web/dist ./ai-web/dist

RUN find /opt/ai-middle-office -type d -exec chmod 0755 {} + \
    && find /opt/ai-middle-office -type f -exec chmod 0644 {} + \
    && python -c "from app.main import app; import mcp_servers.tender_evidence.hybrid_client; assert app"

USER 10001:10001
WORKDIR /opt/ai-middle-office/AI_Middle_Office

EXPOSE 9000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--proxy-headers", "--forwarded-allow-ips", "*"]
