"""Deterministic no-network Provider for the private MVP-1 local lab.

This is deliberately not a model simulator and must never be selected by the
production worker.  It only emits closed TaskAction envelopes so the durable
Run/Plan/Task/Tool/Checkpoint/Validation chain can be exercised offline.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.bid_assessment import BidAssessmentScope
from app.models.bid_assessment_results import BidHardGateResult, BidResolvedFact
from app.models.bid_assessment_runtime import BidAnalysisRun, BidTask
from app.models.bid_assessment_tooling import BidContextManifest, BidToolInvocation, BidToolResult
from app.services.bid_model_execution import ModelProviderResult
from app.services.bid_mvp1_authority import load_mvp1_fact_catalog
from app.services.bid_mvp1_retrieval_hints import MVP1_RETRIEVAL_HINTS


LOCAL_PROVIDER_REF = "mvp1-local-deterministic"
LOCAL_REASON = "LOCAL_MVP1_DETERMINISTIC_PROVIDER"


class DeterministicMvp1LocalProvider:
    """Closed, deterministic and strictly local test Provider."""

    def __init__(self, *, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    @staticmethod
    def _result(action: dict[str, Any], provider_request_id: str) -> ModelProviderResult:
        return ModelProviderResult(
            action=action,
            usage={"input_tokens": 0, "output_tokens": 0},
            finish_reason="local_deterministic",
            provider_receipt_id=f"local:{provider_request_id}"[:191],
            actual_cost_microunits=0,
        )

    def execute(
        self,
        *,
        request_envelope: dict[str, Any],
        provider_request_id: str,
    ) -> ModelProviderResult:
        db = self._session_factory()
        try:
            task = db.query(BidTask).filter(
                BidTask.id == str(request_envelope["task_id"])
            ).one()
            run = db.query(BidAnalysisRun).filter(
                BidAnalysisRun.id == str(request_envelope["run_id"])
            ).one()
            context = db.query(BidContextManifest).filter(
                BidContextManifest.id == str(request_envelope["context_manifest_id"]),
                BidContextManifest.manifest_hash
                == str(request_envelope["context_manifest_hash"]),
            ).one()
            manifest = dict(context.manifest_json or {})
            result_ids = [str(value) for value in manifest.get("included_tool_result_ids") or []]
            tool_rows = []
            if result_ids:
                tool_rows = (
                    db.query(BidToolResult, BidToolInvocation)
                    .join(BidToolInvocation, BidToolInvocation.id == BidToolResult.invocation_id)
                    .filter(
                        BidToolResult.id.in_(tuple(result_ids)),
                        BidToolInvocation.task_id == task.id,
                        BidToolInvocation.run_id == run.id,
                    )
                    .order_by(BidToolResult.created_at.asc(), BidToolResult.id.asc())
                    .all()
                )
            task_type = str(task.task_type)
            if task_type in MVP1_RETRIEVAL_HINTS:
                return self._fact_action(
                    db,
                    task=task,
                    run=run,
                    tool_rows=tool_rows,
                    provider_request_id=provider_request_id,
                )
            if task_type == "synthesize_assessment":
                return self._synthesis_action(
                    db,
                    run=run,
                    provider_request_id=provider_request_id,
                )
            return self._result(
                {
                    "action_type": "finish",
                    "completion_summary": "本地确定性 Provider 无需执行模型动作。",
                    "output_candidate": None,
                    "reason_codes": [LOCAL_REASON],
                },
                provider_request_id,
            )
        finally:
            db.close()

    def _fact_action(
        self,
        db: Session,
        *,
        task: BidTask,
        run: BidAnalysisRun,
        tool_rows: list[tuple[BidToolResult, BidToolInvocation]],
        provider_request_id: str,
    ) -> ModelProviderResult:
        search_rows = [row for row in tool_rows if str(row[1].tool_name) == "evidence.search"]
        read_rows = [row for row in tool_rows if str(row[1].tool_name) == "evidence.read"]
        task_type = str(task.task_type)
        if not search_rows:
            return self._result(
                {
                    "action_type": "request_tool",
                    "tool_call_id": f"local-search-{request_envelope_seq(provider_request_id)}",
                    "tool_name": "evidence.search",
                    "arguments": {
                        "query": str(
                            MVP1_RETRIEVAL_HINTS[task_type]["primary_query"]
                        ),
                        "top_k": 5,
                    },
                    "reason_codes": [LOCAL_REASON],
                },
                provider_request_id,
            )
        if not read_rows:
            hits = list((search_rows[-1][0].inline_data_json or {}).get("hits") or [])
            evidence_ids = [str(item["evidence_id"]) for item in hits[:2] if item.get("evidence_id")]
            if evidence_ids:
                return self._result(
                    {
                        "action_type": "request_tool",
                        "tool_call_id": f"local-read-{request_envelope_seq(provider_request_id)}",
                        "tool_name": "evidence.read",
                        "arguments": {
                            "evidence_ids": evidence_ids,
                            "expansion": "neighbors",
                            "radius": 1,
                        },
                        "reason_codes": [LOCAL_REASON],
                    },
                    provider_request_id,
                )
            return self._result(
                {
                    "action_type": "finish",
                    "completion_summary": "本地证据检索无结果，未生成事实候选。",
                    "output_candidate": None,
                    "reason_codes": [LOCAL_REASON],
                },
                provider_request_id,
            )
        read_payload = dict(read_rows[-1][0].inline_data_json or {})
        items = [item for item in list(read_payload.get("items") or []) if item.get("context_read")]
        if not items:
            return self._result(
                {
                    "action_type": "finish",
                    "completion_summary": "本地精读没有可引用证据，未生成事实候选。",
                    "output_candidate": None,
                    "reason_codes": [LOCAL_REASON],
                },
                provider_request_id,
            )
        slot = next(
            item
            for item in load_mvp1_fact_catalog()["slots"]
            if str(item.get("task_type")) == task_type
        )
        scope = db.query(BidAssessmentScope).filter(BidAssessmentScope.id == run.scope_id).one()
        snapshot = dict(scope.selected_lot_snapshot_json or {})
        lot_id = str(snapshot.get("lot_id") or scope.source_lot_candidate_id or "")
        evidence_ids = [str(item["evidence_id"]) for item in items[:4]]
        fact_text = "\n".join(str(item.get("text") or "") for item in items).strip()[:4000]
        evaluation_time = run.evaluation_time
        if evaluation_time.tzinfo is None:
            evaluation_time = evaluation_time.replace(tzinfo=timezone.utc)
        asserted_at = evaluation_time.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        return self._result(
            {
                "action_type": "submit_fact_candidates",
                "candidates": [
                    {
                        "fact_slot": str(slot["slot"]),
                        "value": fact_text,
                        "value_type": "text",
                        "scope": {"type": "lot", "id": lot_id},
                        "source_type": "document",
                        "evidence_ids": evidence_ids,
                        "confidence": "medium",
                        "asserted_at": asserted_at,
                    }
                ],
                "reason_codes": [LOCAL_REASON],
            },
            provider_request_id,
        )

    def _synthesis_action(
        self,
        db: Session,
        *,
        run: BidAnalysisRun,
        provider_request_id: str,
    ) -> ModelProviderResult:
        fact_rows = (
            db.query(BidResolvedFact)
            .filter(
                BidResolvedFact.run_id == run.id,
                BidResolvedFact.status == "supported",
            )
            .order_by(BidResolvedFact.fact_slot.asc(), BidResolvedFact.id.asc())
            .all()
        )
        support_fact = next(
            (
                row
                for row in fact_rows
                if str(row.fact_slot).startswith("tender.")
                and bool(row.source_assertion_ids_json)
            ),
            None,
        )
        support = str(support_fact.id) if support_fact is not None else None
        if support is None:
            support_row = (
                db.query(BidHardGateResult.id)
                .filter(BidHardGateResult.run_id == run.id)
                .order_by(BidHardGateResult.gate_code.asc())
                .first()
            )
            support = str(support_row[0]) if support_row is not None else None
        if support is None:
            return self._result(
                {
                    "action_type": "finish",
                    "completion_summary": "没有事实或门禁权威可供本地汇总。",
                    "output_candidate": None,
                    "reason_codes": [LOCAL_REASON],
                },
                provider_request_id,
            )
        return self._result(
            {
                "action_type": "submit_claim_candidates",
                "candidates": [
                    {
                        "claim_type": "recommendation",
                        "text": "本地隔离验证仅确认运行链可收敛，正式投标建议仍需人工复核。",
                        "support_ids": [support],
                        "premise_or_trigger": "本地确定性 Provider，不代表真实模型研判。",
                    }
                ],
                "reason_codes": [LOCAL_REASON],
            },
            provider_request_id,
        )


def request_envelope_seq(provider_request_id: str) -> str:
    """Return a stable tool-call suffix that satisfies the closed ID contract."""

    normalized = "".join(character for character in str(provider_request_id) if character.isalnum())
    return (normalized[-24:] or "00000000").lower()
