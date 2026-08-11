ARG BASE_IMAGE=ai-middle-office-app:20260805_161737
FROM ${BASE_IMAGE}

ARG APP_VERSION=performance-candidate
LABEL org.opencontainers.image.version="${APP_VERSION}" \
      com.qskingship.release.kind="static-performance-overlay"

USER root
COPY --chown=root:root ai-web/dist /opt/ai-middle-office/ai-web/dist
RUN find /opt/ai-middle-office/ai-web/dist -type d -exec chmod 0755 {} + \
    && find /opt/ai-middle-office/ai-web/dist -type f -exec chmod 0644 {} +

USER 10001:10001
WORKDIR /opt/ai-middle-office/AI_Middle_Office
