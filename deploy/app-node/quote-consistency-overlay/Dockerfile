ARG BASE_IMAGE=ai-middle-office-app:20260805_161737
FROM ${BASE_IMAGE}

ARG APP_VERSION=20260808_consistency1
LABEL org.opencontainers.image.version="${APP_VERSION}" \
      com.qskingship.release.kind="quote-consistency-phase1-overlay"

USER root

COPY apply-overlay.py /tmp/quote-consistency-overlay/apply-overlay.py
COPY phase1.patch /tmp/quote-consistency-overlay/phase1.patch
COPY new-files /tmp/quote-consistency-overlay/new-files

RUN python /tmp/quote-consistency-overlay/apply-overlay.py \
      --root /opt/ai-middle-office/AI_Middle_Office \
      --patch /tmp/quote-consistency-overlay/phase1.patch \
      --new-root /tmp/quote-consistency-overlay/new-files \
    && cd /opt/ai-middle-office/AI_Middle_Office \
    && python -m compileall -q app alembic/versions/20260808_0082_add_quote_consistency_phase1.py \
    && python -c "from app.main import app; from app.models.quote_job import QuotePushAttempt, QuoteQuotaReservation; from app.models.user import User; assert hasattr(User, 'quota_reserved'); paths={route.path for route in app.routes}; assert '/api/v1/internal/n8n/quote-push/claim' in paths" \
    && python -m alembic heads | grep -F "20260808_0082 (head)" \
    && rm -rf /tmp/quote-consistency-overlay \
    && find /opt/ai-middle-office/AI_Middle_Office -type d -exec chmod 0755 {} + \
    && find /opt/ai-middle-office/AI_Middle_Office -type f -exec chmod 0644 {} +

USER 10001:10001
WORKDIR /opt/ai-middle-office/AI_Middle_Office
