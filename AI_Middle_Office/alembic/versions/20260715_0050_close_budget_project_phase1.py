"""close budget project phase 1 lifecycle and revision audit

Revision ID: 20260715_0050
Revises: 20260714_0049
Create Date: 2026-07-15
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260715_0050"
down_revision: Union[str, None] = "20260714_0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _longtext() -> sa.types.TypeEngine:
    return sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _strict_json(
    value: Any,
    expected_type: type,
    *,
    location: str,
) -> Any:
    """Parse legacy JSON without ever converting corruption into an empty value."""

    if value is None or (isinstance(value, str) and not value.strip()):
        raise RuntimeError(f"0050 JSON preflight failed at {location}: value is empty")
    try:
        parsed = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"0050 JSON preflight failed at {location}: invalid JSON ({exc})"
        ) from exc
    if not isinstance(parsed, expected_type):
        raise RuntimeError(
            f"0050 JSON preflight failed at {location}: expected "
            f"{expected_type.__name__}, got {type(parsed).__name__}"
        )
    return parsed


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _preflight_legacy_snapshots(bind: sa.engine.Connection) -> list[dict[str, Any]]:
    """Read and validate every legacy value used by the immutable backfill.

    This function must stay before the first DDL statement in ``upgrade``.  In
    particular, MySQL DDL may implicitly commit, so transaction rollback is not
    an adequate protection against silently freezing malformed legacy JSON.
    """

    batches = bind.execute(
        sa.text(
            "SELECT id, status, remap_revision, current_preview_json, sheet_count, "
            "total_output_row_count, standard_item_count, valid_quantity_count, "
            "invalid_quantity_count, created_by, updated_by "
            "FROM budget_project_import_batches ORDER BY id"
        )
    ).mappings().all()
    snapshots: list[dict[str, Any]] = []
    for batch in batches:
        batch_id = int(batch["id"])
        if batch["status"] != "parsed":
            raise RuntimeError(
                "0050 lifecycle preflight failed: legacy budget import "
                f"batch_id={batch_id} has status={batch['status']!r}; expected 'parsed'"
            )
        preview = _strict_json(
            batch["current_preview_json"],
            dict,
            location=f"batch_id={batch_id} field=current_preview_json",
        )
        mappings = bind.execute(
            sa.text(
                "SELECT id, sheet_name, header_row_index, detected_field_mapping_json, "
                "applied_field_mapping_json, detected_columns_json, current_columns_json, "
                "mapping_revision FROM budget_project_import_sheet_mappings "
                "WHERE batch_id=:batch_id ORDER BY id"
            ),
            {"batch_id": batch_id},
        ).mappings().all()
        mapping_snapshot: list[dict[str, Any]] = []
        for item in mappings:
            sheet_location = (
                f"batch_id={batch_id} mapping_id={item['id']} "
                f"sheet_name={item['sheet_name']!r}"
            )
            mapping_snapshot.append(
                {
                    "sheet_name": item["sheet_name"],
                    "sheet_role": "bill",
                    "header_row_index": item["header_row_index"],
                    "detected_field_mapping": _strict_json(
                        item["detected_field_mapping_json"],
                        dict,
                        location=f"{sheet_location} field=detected_field_mapping_json",
                    ),
                    "applied_field_mapping": _strict_json(
                        item["applied_field_mapping_json"],
                        dict,
                        location=f"{sheet_location} field=applied_field_mapping_json",
                    ),
                    "detected_columns": _strict_json(
                        item["detected_columns_json"],
                        list,
                        location=f"{sheet_location} field=detected_columns_json",
                    ),
                    "current_columns": _strict_json(
                        item["current_columns_json"],
                        list,
                        location=f"{sheet_location} field=current_columns_json",
                    ),
                    "mapping_revision": item["mapping_revision"],
                }
            )
        rows = bind.execute(
            sa.text(
                "SELECT id, row_key, source_sheet, raw_row_index, sort_order, "
                "mapping_revision, row_type, is_standard_item, quantity_status, "
                "standard_row_json FROM budget_project_standard_rows "
                "WHERE batch_id=:batch_id ORDER BY sort_order, id"
            ),
            {"batch_id": batch_id},
        ).mappings().all()
        row_snapshot: list[dict[str, Any]] = []
        for item in rows:
            row_location = (
                f"batch_id={batch_id} row_id={item['id']} row_key={item['row_key']!r} "
                f"sheet_name={item['source_sheet']!r}"
            )
            row_snapshot.append(
                {
                    "row_key": item["row_key"],
                    "source_sheet": item["source_sheet"],
                    "sheet_role": "bill",
                    "raw_row_index": item["raw_row_index"],
                    "sort_order": item["sort_order"],
                    "mapping_revision": item["mapping_revision"],
                    "row_type": item["row_type"],
                    "is_standard_item": bool(item["is_standard_item"]),
                    "quantity_status": item["quantity_status"],
                    "standard_row": _strict_json(
                        item["standard_row_json"],
                        dict,
                        location=f"{row_location} field=standard_row_json",
                    ),
                }
            )
        summary = {
            "sheet_count": batch["sheet_count"],
            "total_output_row_count": batch["total_output_row_count"],
            "standard_item_count": batch["standard_item_count"],
            "valid_quantity_count": batch["valid_quantity_count"],
            "invalid_quantity_count": batch["invalid_quantity_count"],
        }
        snapshot = {
            "preview": preview,
            "sheet_mappings": mapping_snapshot,
            "standard_rows": row_snapshot,
            "summary": summary,
        }
        snapshots.append(
            {
                "batch": batch,
                "preview": preview,
                "sheet_mappings": mapping_snapshot,
                "standard_rows": row_snapshot,
                "summary": summary,
                "snapshot_sha256": hashlib.sha256(
                    _dump_json(snapshot).encode("utf-8")
                ).hexdigest(),
            }
        )
    return snapshots


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    # Strictly complete every state/JSON check before any DDL is attempted.
    legacy_snapshots = _preflight_legacy_snapshots(bind)
    op.create_table(
        "budget_project_import_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision_uuid", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_kind", sa.String(length=24), server_default="remap", nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("preview_json", _longtext(), nullable=False),
        sa.Column("sheet_mappings_json", _longtext(), nullable=False),
        sa.Column("standard_rows_json", _longtext(), nullable=False),
        sa.Column("summary_json", _longtext(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["budget_project_import_batches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_uuid", name="uq_budget_import_revisions_uuid"),
        sa.UniqueConstraint("batch_id", "revision_number", name="uq_budget_import_revision_number"),
    )
    op.create_index(
        "ix_budget_project_import_revisions_batch_id",
        "budget_project_import_revisions",
        ["batch_id"],
    )
    op.create_index(
        "ix_budget_project_import_revisions_snapshot_sha256",
        "budget_project_import_revisions",
        ["snapshot_sha256"],
    )
    op.create_index(
        "ix_budget_project_import_revisions_created_by",
        "budget_project_import_revisions",
        ["created_by"],
    )
    op.create_index(
        "ix_budget_import_revision_batch_created",
        "budget_project_import_revisions",
        ["batch_id", "created_at"],
    )
    op.create_table(
        "budget_project_import_lifecycle_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("revision_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("event_json", _longtext(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["budget_project_import_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revision_id"], ["budget_project_import_revisions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_budget_project_import_lifecycle_events_project_id", ["project_id"]),
        ("ix_budget_project_import_lifecycle_events_batch_id", ["batch_id"]),
        ("ix_budget_project_import_lifecycle_events_revision_id", ["revision_id"]),
        ("ix_budget_project_import_lifecycle_events_event_type", ["event_type"]),
        ("ix_budget_project_import_lifecycle_events_actor_id", ["actor_id"]),
        ("ix_budget_import_event_project_created", ["project_id", "created_at"]),
        ("ix_budget_import_event_batch_created", ["batch_id", "created_at"]),
    ):
        op.create_index(name, "budget_project_import_lifecycle_events", columns)

    for column in (
        sa.Column("current_revision_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_revision_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", sa.Integer(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("budget_project_import_batches", column)
    op.create_index("ix_budget_project_import_batches_current_revision_id", "budget_project_import_batches", ["current_revision_id"])
    op.create_index("ix_budget_project_import_batches_confirmed_revision_id", "budget_project_import_batches", ["confirmed_revision_id"])
    op.create_index("ix_budget_import_project_status", "budget_project_import_batches", ["project_id", "status"])
    if dialect != "sqlite":
        op.create_foreign_key("fk_budget_import_current_revision", "budget_project_import_batches", "budget_project_import_revisions", ["current_revision_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key("fk_budget_import_confirmed_revision", "budget_project_import_batches", "budget_project_import_revisions", ["confirmed_revision_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key("fk_budget_import_confirmed_by", "budget_project_import_batches", "users", ["confirmed_by"], ["id"])
        op.create_foreign_key("fk_budget_import_activated_by", "budget_project_import_batches", "users", ["activated_by"], ["id"])
        op.create_foreign_key("fk_budget_import_superseded_by", "budget_project_import_batches", "users", ["superseded_by"], ["id"])

    op.add_column("budget_project_profiles", sa.Column("active_import_batch_id", sa.Integer(), nullable=True))
    op.add_column("budget_project_profiles", sa.Column("active_import_revision_id", sa.Integer(), nullable=True))
    op.create_index("ix_budget_project_profiles_active_import_batch_id", "budget_project_profiles", ["active_import_batch_id"])
    op.create_index("ix_budget_project_profiles_active_import_revision_id", "budget_project_profiles", ["active_import_revision_id"])
    if dialect != "sqlite":
        op.create_foreign_key("fk_budget_profile_active_import_batch", "budget_project_profiles", "budget_project_import_batches", ["active_import_batch_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key("fk_budget_profile_active_import_revision", "budget_project_profiles", "budget_project_import_revisions", ["active_import_revision_id"], ["id"], ondelete="SET NULL")

    op.add_column("budget_project_import_sheet_mappings", sa.Column("sheet_role", sa.String(length=32), server_default="bill", nullable=False))
    op.create_index("ix_budget_project_import_sheet_mappings_sheet_role", "budget_project_import_sheet_mappings", ["sheet_role"])
    op.add_column("budget_project_standard_rows", sa.Column("sheet_role", sa.String(length=32), server_default="bill", nullable=False))
    op.create_index("ix_budget_project_standard_rows_sheet_role", "budget_project_standard_rows", ["sheet_role"])
    for legacy in legacy_snapshots:
        batch = legacy["batch"]
        preview = legacy["preview"]
        mapping_snapshot = legacy["sheet_mappings"]
        row_snapshot = legacy["standard_rows"]
        summary = legacy["summary"]
        result = bind.execute(
            sa.text(
                "INSERT INTO budget_project_import_revisions "
                "(revision_uuid, batch_id, revision_number, revision_kind, snapshot_sha256, "
                "preview_json, sheet_mappings_json, standard_rows_json, summary_json, created_by) "
                "VALUES (:revision_uuid, :batch_id, :revision_number, 'backfill', :snapshot_sha256, "
                ":preview_json, :sheet_mappings_json, :standard_rows_json, :summary_json, :created_by)"
            ),
            {
                "revision_uuid": str(uuid.uuid4()),
                "batch_id": batch["id"],
                "revision_number": int(batch["remap_revision"] or 0),
                "snapshot_sha256": legacy["snapshot_sha256"],
                "preview_json": _dump_json(preview),
                "sheet_mappings_json": _dump_json(mapping_snapshot),
                "standard_rows_json": _dump_json(row_snapshot),
                "summary_json": _dump_json(summary),
                "created_by": batch["updated_by"] or batch["created_by"],
            },
        )
        revision_id = result.lastrowid
        if revision_id is None:
            revision_id = bind.execute(
                sa.text(
                    "SELECT id FROM budget_project_import_revisions "
                    "WHERE batch_id=:batch_id AND revision_number=:revision_number"
                ),
                {"batch_id": batch["id"], "revision_number": int(batch["remap_revision"] or 0)},
            ).scalar_one()
        bind.execute(
            sa.text(
                "UPDATE budget_project_import_batches SET current_revision_id=:revision_id WHERE id=:batch_id"
            ),
            {"revision_id": revision_id, "batch_id": batch["id"]},
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    # 0049 has no lifecycle/revision semantics.  Normalize pointers and state
    # before discarding the append-only audit tables so a later 0050 retry can
    # preflight cleanly.  Downgrade intentionally loses confirmation/activation
    # state; revision payloads remain protected until every pointer is cleared.
    bind.execute(
        sa.text(
            "UPDATE budget_project_profiles SET active_import_batch_id=NULL, "
            "active_import_revision_id=NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE budget_project_import_batches SET status='parsed', "
            "current_revision_id=NULL, confirmed_revision_id=NULL, "
            "confirmed_by=NULL, confirmed_at=NULL, activated_by=NULL, activated_at=NULL, "
            "superseded_by=NULL, superseded_at=NULL"
        )
    )
    op.drop_table("budget_project_import_lifecycle_events")
    op.drop_index("ix_budget_project_standard_rows_sheet_role", table_name="budget_project_standard_rows")
    op.drop_column("budget_project_standard_rows", "sheet_role")
    op.drop_index("ix_budget_project_import_sheet_mappings_sheet_role", table_name="budget_project_import_sheet_mappings")
    op.drop_column("budget_project_import_sheet_mappings", "sheet_role")
    if dialect != "sqlite":
        op.drop_constraint("fk_budget_profile_active_import_revision", "budget_project_profiles", type_="foreignkey")
        op.drop_constraint("fk_budget_profile_active_import_batch", "budget_project_profiles", type_="foreignkey")
    op.drop_index("ix_budget_project_profiles_active_import_revision_id", table_name="budget_project_profiles")
    op.drop_index("ix_budget_project_profiles_active_import_batch_id", table_name="budget_project_profiles")
    op.drop_column("budget_project_profiles", "active_import_revision_id")
    op.drop_column("budget_project_profiles", "active_import_batch_id")
    if dialect != "sqlite":
        for constraint in (
            "fk_budget_import_superseded_by",
            "fk_budget_import_activated_by",
            "fk_budget_import_confirmed_by",
            "fk_budget_import_confirmed_revision",
            "fk_budget_import_current_revision",
        ):
            op.drop_constraint(constraint, "budget_project_import_batches", type_="foreignkey")
    op.drop_index("ix_budget_import_project_status", table_name="budget_project_import_batches")
    op.drop_index("ix_budget_project_import_batches_confirmed_revision_id", table_name="budget_project_import_batches")
    op.drop_index("ix_budget_project_import_batches_current_revision_id", table_name="budget_project_import_batches")
    for column in (
        "superseded_at",
        "superseded_by",
        "activated_at",
        "activated_by",
        "confirmed_at",
        "confirmed_by",
        "confirmed_revision_id",
        "current_revision_id",
    ):
        op.drop_column("budget_project_import_batches", column)
    op.drop_table("budget_project_import_revisions")
