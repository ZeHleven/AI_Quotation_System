"""Register every SQLAlchemy model for standalone workers and maintenance CLIs.

FastAPI imports these modules from ``app.main``. MCP workers and ingestion
scripts do not import the FastAPI app, so they use this lightweight registry to
resolve string relationships and foreign-key metadata without startup side
effects.
"""

from app.models import (  # noqa: F401
    account,
    account_quota,
    agent,
    bid_assessment,
    bid_assessment_config,
    bid_assessment_eventing,
    bid_assessment_runtime,
    bid_intake_runtime,
    bidding,
    budget_pricing,
    budget_pricing_draft,
    budget_project_quota,
    budget_project,
    client_inquiry,
    cost_audit,
    cost_item,
    enterprise_profile,
    enterprise_quota,
    file_object,
    knowledge_candidate,
    material,
    model_call_log,
    project_cost_import,
    project_progress,
    pricing_agent,
    prompt_regression,
    quote_cost_evidence,
    quote_feedback,
    quote_history,
    quote_job,
    quote_preview_draft,
    quote_requirement_row,
    rag_eval_report,
    tender_evidence,
    tender_evidence_index,
    tender_parse_pipeline,
    user,
)
