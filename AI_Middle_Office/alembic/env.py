from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import create_engine, engine_from_config, pool


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.models import (  # noqa: F401,E402
    account,
    account_quota,
    budget_pricing,
    budget_pricing_draft,
    budget_project,
    file_object,
    knowledge_candidate,
    material,
    model_call_log,
    cost_audit,
    enterprise_quota,
    prompt_regression,
    project_progress,
    quote_cost_evidence,
    quote_feedback,
    quote_history,
    quote_job,
    quote_requirement_row,
    rag_eval_report,
    tender_evidence,
    user,
)
from app.models import registry as model_registry  # noqa: F401,E402


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.alembic_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(settings.alembic_database_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
