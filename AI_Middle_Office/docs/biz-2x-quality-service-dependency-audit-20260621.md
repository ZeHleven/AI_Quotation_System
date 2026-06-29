# BIZ-2x Quality Service Dependency Audit

Generated: 2026-06-21

## Scope

This audit covers the 25 remaining `QUALITY_EVAL_EXPERIMENT` service modules in `AI_Middle_Office/app/services`. Archived scripts/tests are excluded from active dependency scanning and counted separately as archive references.

## Conclusion

- Services inspected: 25
- Active external import blockers: 0
- Archive-ready as a group: 25
- Archive completed: yes, moved to `AI_Middle_Office/archive/biz2x_pdf_quality_eval_20260621/services/`

No current MVP, legacy API/UI entrypoint, active script, or active test imports these services. They can be archived as one quality-eval service group after deciding the archive layout.

## Important Caveat

Several services import each other. Moving only part of the group would break those internal imports. If preserving runnable experiments matters, archive the group together and either keep a compatibility import path or rewrite archived imports.

## Internal Import Edges

- `AI_Middle_Office/app/services/drawing_pdf_external_recall_prefill.py` -> `AI_Middle_Office/app/services/drawing_pdf_external_recall_template.py`
- `AI_Middle_Office/app/services/drawing_pdf_external_recall_prefill.py` -> `AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py`
- `AI_Middle_Office/app/services/drawing_pdf_external_recall_template_status.py` -> `AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py`
- `AI_Middle_Office/app/services/drawing_pdf_feature_precision_capture_pack.py` -> `AI_Middle_Office/app/services/drawing_three_field_acceptance.py`
- `AI_Middle_Office/app/services/drawing_pdf_feature_precision_capture_runner.py` -> `AI_Middle_Office/app/services/drawing_pdf_object_recall_capture_runner.py`
- `AI_Middle_Office/app/services/drawing_pdf_gap_recall_eval.py` -> `AI_Middle_Office/app/services/drawing_pdf_v2_takeoff.py`
- `AI_Middle_Office/app/services/drawing_pdf_gap_recall_eval.py` -> `AI_Middle_Office/app/services/drawing_three_field_acceptance.py`
- `AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py` -> `AI_Middle_Office/app/services/drawing_pdf_gap_recall_runner.py`
- `AI_Middle_Office/app/services/drawing_pdf_object_recall_capture_runner.py` -> `AI_Middle_Office/app/services/drawing_pdf_gap_recall_runner.py`
- `AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench_prefill.py` -> `AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py`
- `AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench_prefill.py` -> `AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench.py`
- `AI_Middle_Office/app/services/drawing_pdf_structured_feature_fusion.py` -> `AI_Middle_Office/app/services/drawing_pdf_gap_recall_runner.py`
- `AI_Middle_Office/app/services/drawing_pdf_v2_takeoff.py` -> `AI_Middle_Office/app/services/drawing_three_field_acceptance.py`

## Per-Service Recommendation

| Service | Recommendation | Internal importers | Imports internal services | Archive refs |
| --- | --- | --- | --- | ---: |
| `AI_Middle_Office/app/services/drawing_pdf_closed_loop_stage_report.py` | ARCHIVE_READY_AS_GROUP | - | - | 4 |
| `AI_Middle_Office/app/services/drawing_pdf_external_evidence_quality.py` | ARCHIVE_READY_AS_GROUP | - | - | 3 |
| `AI_Middle_Office/app/services/drawing_pdf_external_recall_prefill.py` | ARCHIVE_READY_AS_GROUP | - | AI_Middle_Office/app/services/drawing_pdf_external_recall_template.py; AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_external_recall_template.py` | ARCHIVE_READY_AS_GROUP | AI_Middle_Office/app/services/drawing_pdf_external_recall_prefill.py | - | 11 |
| `AI_Middle_Office/app/services/drawing_pdf_external_recall_template_status.py` | ARCHIVE_READY_AS_GROUP | - | AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py | 7 |
| `AI_Middle_Office/app/services/drawing_pdf_feature_precision_capture_pack.py` | ARCHIVE_READY_AS_GROUP | - | AI_Middle_Office/app/services/drawing_three_field_acceptance.py | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_feature_precision_capture_runner.py` | ARCHIVE_READY_AS_GROUP | - | AI_Middle_Office/app/services/drawing_pdf_object_recall_capture_runner.py | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_gap_recall_eval.py` | ARCHIVE_READY_AS_GROUP | - | AI_Middle_Office/app/services/drawing_pdf_v2_takeoff.py; AI_Middle_Office/app/services/drawing_three_field_acceptance.py | 4 |
| `AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py` | ARCHIVE_READY_AS_GROUP | AI_Middle_Office/app/services/drawing_pdf_external_recall_prefill.py; AI_Middle_Office/app/services/drawing_pdf_external_recall_template_status.py; AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench_prefill.py | AI_Middle_Office/app/services/drawing_pdf_gap_recall_runner.py | 11 |
| `AI_Middle_Office/app/services/drawing_pdf_gap_recall_plan.py` | ARCHIVE_READY_AS_GROUP | - | - | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_gap_recall_runner.py` | ARCHIVE_READY_AS_GROUP | AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py; AI_Middle_Office/app/services/drawing_pdf_object_recall_capture_runner.py; AI_Middle_Office/app/services/drawing_pdf_structured_feature_fusion.py | - | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_gap_review_pack.py` | ARCHIVE_READY_AS_GROUP | - | - | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_object_recall_capture_pack.py` | ARCHIVE_READY_AS_GROUP | - | - | 4 |
| `AI_Middle_Office/app/services/drawing_pdf_object_recall_capture_runner.py` | ARCHIVE_READY_AS_GROUP | AI_Middle_Office/app/services/drawing_pdf_feature_precision_capture_runner.py | AI_Middle_Office/app/services/drawing_pdf_gap_recall_runner.py | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_object_recall_pack.py` | ARCHIVE_READY_AS_GROUP | - | - | 5 |
| `AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench.py` | ARCHIVE_READY_AS_GROUP | AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench_prefill.py | - | 7 |
| `AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench_prefill.py` | ARCHIVE_READY_AS_GROUP | - | AI_Middle_Office/app/services/drawing_pdf_gap_recall_importer.py; AI_Middle_Office/app/services/drawing_pdf_object_recall_workbench.py | 3 |
| `AI_Middle_Office/app/services/drawing_pdf_quantity_stage_placeholder.py` | ARCHIVE_READY_AS_GROUP | - | - | 4 |
| `AI_Middle_Office/app/services/drawing_pdf_standard_bill_export.py` | ARCHIVE_READY_AS_GROUP | - | - | 4 |
| `AI_Middle_Office/app/services/drawing_pdf_structured_feature_fusion.py` | ARCHIVE_READY_AS_GROUP | - | AI_Middle_Office/app/services/drawing_pdf_gap_recall_runner.py | 2 |
| `AI_Middle_Office/app/services/drawing_pdf_three_field_defect_router.py` | ARCHIVE_READY_AS_GROUP | - | - | 3 |
| `AI_Middle_Office/app/services/drawing_pdf_three_field_gate.py` | ARCHIVE_READY_AS_GROUP | - | - | 4 |
| `AI_Middle_Office/app/services/drawing_pdf_three_field_review.py` | ARCHIVE_READY_AS_GROUP | - | - | 4 |
| `AI_Middle_Office/app/services/drawing_pdf_v2_takeoff.py` | ARCHIVE_READY_AS_GROUP | AI_Middle_Office/app/services/drawing_pdf_gap_recall_eval.py | AI_Middle_Office/app/services/drawing_three_field_acceptance.py | 3 |
| `AI_Middle_Office/app/services/drawing_three_field_acceptance.py` | ARCHIVE_READY_AS_GROUP | AI_Middle_Office/app/services/drawing_pdf_feature_precision_capture_pack.py; AI_Middle_Office/app/services/drawing_pdf_gap_recall_eval.py; AI_Middle_Office/app/services/drawing_pdf_v2_takeoff.py | - | 5 |

Full CSV: `AI_Middle_Office/docs/biz-2x-quality-service-dependency-audit-20260621.csv`

## Recommended Next Step

The 25 service modules have been archived as a single quality-eval service group. For the current PDF MVP, they are not active dependencies.
