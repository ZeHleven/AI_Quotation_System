ARG BASE_IMAGE=ai-middle-office-app:20260806_072623_perf1
FROM ${BASE_IMAGE}

ARG APP_VERSION=staff-scope-candidate
LABEL org.opencontainers.image.version="${APP_VERSION}" \
      com.qskingship.release.kind="staff-scope-security-overlay"

USER root
COPY --chown=root:root AI_Middle_Office/app/services/rbac.py /opt/ai-middle-office/AI_Middle_Office/app/services/rbac.py
COPY --chown=root:root AI_Middle_Office/app/services/cost_items.py /opt/ai-middle-office/AI_Middle_Office/app/services/cost_items.py
COPY --chown=root:root AI_Middle_Office/app/dependencies.py /opt/ai-middle-office/AI_Middle_Office/app/dependencies.py
COPY --chown=root:root AI_Middle_Office/app/api/v1/account_quotas.py /opt/ai-middle-office/AI_Middle_Office/app/api/v1/account_quotas.py
COPY --chown=root:root AI_Middle_Office/app/services/project_progress.py /opt/ai-middle-office/AI_Middle_Office/app/services/project_progress.py
COPY --chown=root:root AI_Middle_Office/app/api/v1/agents.py /opt/ai-middle-office/AI_Middle_Office/app/api/v1/agents.py
COPY --chown=root:root AI_Middle_Office/app/api/v1/codex_worker.py /opt/ai-middle-office/AI_Middle_Office/app/api/v1/codex_worker.py
COPY --chown=root:root AI_Middle_Office/app/api/v1/dwg_quantity_trial.py /opt/ai-middle-office/AI_Middle_Office/app/api/v1/dwg_quantity_trial.py
COPY --chown=root:root AI_Middle_Office/app/api/v1/pricing_agent.py /opt/ai-middle-office/AI_Middle_Office/app/api/v1/pricing_agent.py
COPY --chown=root:root AI_Middle_Office/app/api/v1/requirement_standardization.py /opt/ai-middle-office/AI_Middle_Office/app/api/v1/requirement_standardization.py
COPY --chown=root:root ai-web/dist /opt/ai-middle-office/ai-web/dist
RUN chmod 0644 \
        /opt/ai-middle-office/AI_Middle_Office/app/services/rbac.py \
        /opt/ai-middle-office/AI_Middle_Office/app/services/cost_items.py \
        /opt/ai-middle-office/AI_Middle_Office/app/dependencies.py \
        /opt/ai-middle-office/AI_Middle_Office/app/api/v1/account_quotas.py \
        /opt/ai-middle-office/AI_Middle_Office/app/services/project_progress.py \
        /opt/ai-middle-office/AI_Middle_Office/app/api/v1/agents.py \
        /opt/ai-middle-office/AI_Middle_Office/app/api/v1/codex_worker.py \
        /opt/ai-middle-office/AI_Middle_Office/app/api/v1/dwg_quantity_trial.py \
        /opt/ai-middle-office/AI_Middle_Office/app/api/v1/pricing_agent.py \
        /opt/ai-middle-office/AI_Middle_Office/app/api/v1/requirement_standardization.py \
    && find /opt/ai-middle-office/ai-web/dist -type d -exec chmod 0755 {} + \
    && find /opt/ai-middle-office/ai-web/dist -type f -exec chmod 0644 {} +

USER 10001:10001
WORKDIR /opt/ai-middle-office/AI_Middle_Office
RUN python -c "from app.services.rbac import get_available_modules; from app.services.cost_items import can_access_cost_db; from app.dependencies import require_account_quota_user; from app.api.v1.agents import require_agent_module_access; from app.api.v1.pricing_agent import require_pricing_agent_access; assert get_available_modules and can_access_cost_db and require_account_quota_user and require_agent_module_access and require_pricing_agent_access"
