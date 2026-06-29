# BIZ-2x PDF Code Slimming Plan

Generated: 2026-06-21

## Current Decision

The current MVP is:

`PDF drawings -> GLM-4V itemization -> GB-style four-field bill preview -> Excel`

The MVP does not require strict 127-row acceptance, precise quantities, or a final pricing handoff.

## Current MVP Files

These files stay in the active path:

| Layer | File | Reason |
| --- | --- | --- |
| API | `app/api/v1/dwg_quantity_trial.py` | Provides `list-items-from-pdf` upload endpoint |
| CLI | `scripts/biz2x_pdf_mvp_preview.py` | New clean CLI entry for PDF MVP preview |
| PDF itemization | `app/services/drawing_pdf_direct_itemizer.py` | Main PDF-to-four-field service |
| PDF render/tile | `app/services/drawing_pdf_evidence_pipeline.py` | Shared PDF parsing/rendering/tile helper |
| Quantity suggestion | `app/services/drawing_pdf_ai_quantity_suggester.py` | Optional rough quantity suggestion, still review-only |
| Model gateway | `app/services/model_gateway.py` | GLM-4V calls and JSON parsing |
| Standard search | `app/services/quantity_standard_index.py` | GB-style item matching |
| Standard library | `app/services/quantity_standard_library.py` | Standard library loading |
| Excel export | `app/services/quantity_list_export.py` | Shared four-field CSV/XLSX exporter used directly by PDF MVP |

`app/services/dwg_item_listing.py` still re-exports `write_quantity_list_outputs` for legacy DWG callers, but PDF direct no longer imports the DWG listing module.

## Quality Eval Files

These are useful later, but they are not the MVP gate. Service modules remain in the active tree until import relationships are checked:

- `app/services/drawing_pdf_v2_takeoff.py`
- `app/services/drawing_three_field_acceptance.py`
- `app/services/drawing_pdf_three_field_gate.py`
- `app/services/drawing_pdf_three_field_review.py`
- `app/services/drawing_pdf_three_field_defect_router.py`
- `app/services/drawing_pdf_standard_bill_export.py`
- `app/services/drawing_pdf_quantity_stage_placeholder.py`

## Experimental Files

These service modules should not be used as the main entry unless explicitly doing quality experiments:

- `app/services/drawing_pdf_gap_*`
- `app/services/drawing_pdf_object_recall_*`
- `app/services/drawing_pdf_feature_precision_*`
- `app/services/drawing_pdf_external_*`
- `app/services/drawing_pdf_structured_feature_fusion.py`
- `app/services/drawing_pdf_closed_loop_stage_report.py`

The corresponding experimental scripts/tests have been moved to:

- `AI_Middle_Office/archive/biz2x_pdf_quality_eval_20260621/`

## Do Not Move Yet

Do not move service modules in this pass. Several active files import PDF/DWG/DXF services at module import time, especially:

- `app/api/v1/dwg_quantity_trial.py`
- `app/services/dwg_item_listing.py`

Moving service files before dependency cleanup can break app startup.

## Completed Refactor: 2026-06-21

Done in the slimming pass:

1. Extracted `write_quantity_list_outputs` and `QUANTITY_LIST_HEADERS` into `app/services/quantity_list_export.py`.
2. Updated `drawing_pdf_direct_itemizer.py` to import `quantity_list_export.py` directly.
3. Kept a compatibility re-export in `dwg_item_listing.py` so existing DWG code can keep calling the same function name.
4. Added `tests/test_quantity_list_export_biz2x.py` to pin the four-field CSV/XLSX export behavior.
5. Archived 27 quality-eval scripts and 24 matching tests under `AI_Middle_Office/archive/biz2x_pdf_quality_eval_20260621/`.
6. Audited and archived the remaining 25 quality-eval service modules as one group under `AI_Middle_Office/archive/biz2x_pdf_quality_eval_20260621/services/`.
7. Moved three GB/T `.docx` source standards into `AI_Middle_Office/data/standards/source_docs/`.
8. Moved `quotation_flow_plan_with_timeline.png` into `AI_Middle_Office/docs/assets/` for route documentation.

## Next Refactor

After the new MVP CLI is verified against a real PDF run:

1. Decide whether the 6 optional DWG/PDF fusion and low-risk quantity files remain active or move to a separate optional archive.
2. Continue extracting small shared utilities only when the PDF MVP imports a large unrelated module.
