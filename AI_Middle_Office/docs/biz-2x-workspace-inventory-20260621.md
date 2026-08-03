# BIZ-2x Workspace Inventory

Generated: 2026-06-21

## Purpose

This inventory reflects the git-visible workspace after local output/tool/training directories were ignored, quality-eval services/scripts/tests were archived, and source standard documents/assets were moved to explicit directories.

## Summary

- Total git-visible changed entries: 117
- Git status ` M`: 10
- Git status `??`: 107

## Category Counts

| Category | Count | Immediate handling |
| --- | ---: | --- |
| LOCAL_IGNORE_RULES | 1 | keep_gitignore_rules_for_local_artifacts |
| MVP_REQUIRED | 18 | keep_in_current_mvp_package |
| CURRENT_DOCS | 2 | keep_as_current_cleanup_evidence |
| LEGACY_COMPAT | 2 | keep_until_import_or_ui_boundary_is_slimmer |
| DWG_PDF_OPTIONAL_EXPERIMENT | 6 | keep_out_of_pdf_mvp_main_path |
| ARCHIVED_QUALITY_EVAL | 78 | archived_out_of_active_services_scripts_tests |
| STANDARD_SOURCE_DOCS | 4 | keep_as_standard_library_source_material |
| DOC_ASSET | 2 | keep_as_documentation_route_asset |
| DOC_ARCHIVE | 4 | keep_as_docs_archive_history |

## Current MVP Whitelist

- `AI_Middle_Office/.env.example`
- `AI_Middle_Office/app/api/v1/dwg_quantity_trial.py`
- `AI_Middle_Office/app/core/config.py`
- `AI_Middle_Office/app/services/model_gateway.py`
- `AI_Middle_Office/requirements.txt`
- `AI_Middle_Office/tests/test_dwg_quantity_trial_biz2x.py`
- `AI_Middle_Office/tests/test_model_gateway.py`
- `AI_Middle_Office/app/services/drawing_pdf_ai_quantity_suggester.py`
- `AI_Middle_Office/app/services/drawing_pdf_direct_itemizer.py`
- `AI_Middle_Office/app/services/drawing_pdf_evidence_pipeline.py`
- `AI_Middle_Office/app/services/quantity_list_export.py`
- `AI_Middle_Office/docs/biz-2x-pdf-code-slimming-plan.md`
- `AI_Middle_Office/docs/biz-2x-pdf-current-mvp-and-workspace-cleanup.md`
- `AI_Middle_Office/docs/biz-2x-workspace-inventory-20260621.csv`
- `AI_Middle_Office/docs/biz-2x-workspace-inventory-20260621.md`
- `AI_Middle_Office/scripts/biz2x_pdf_mvp_preview.py`
- `AI_Middle_Office/tests/test_drawing_pdf_ai_quantity_suggester_biz2x.py`
- `AI_Middle_Office/tests/test_quantity_list_export_biz2x.py`

## Source Standards And Assets

- `AI_Middle_Office/data/standards/source_docs/GBT 50500-2024 寤鸿�惧伐绋嬪伐绋嬮噺娓呭崟璁′环鏍囧噯.docx`
- `AI_Middle_Office/data/standards/source_docs/GBT 50856-2024 閫氱敤瀹夎�呭伐绋嬪伐绋嬮噺璁＄畻鏍囧噯.docx`
- `AI_Middle_Office/data/standards/source_docs/MANIFEST.md`
- `AI_Middle_Office/data/standards/source_docs/锛堟�伙級GBT50854-2024  鎴垮眿寤虹瓚涓庤�呴グ宸ョ▼宸ョ▼閲忚�＄畻鏍囧噯.docx`
- `AI_Middle_Office/docs/assets/README.md`
- `AI_Middle_Office/docs/assets/quotation_flow_plan_with_timeline.png`

## Compatibility Files To Keep For Now

- `AI_Middle_Office/app/services/dwg_item_listing.py`
- `ai-web/src/App.vue`

## Archived Quality Eval Group

- Archive root: `AI_Middle_Office/archive/biz2x_pdf_quality_eval_20260621`
- Manifest: `AI_Middle_Office/archive/biz2x_pdf_quality_eval_20260621/MANIFEST.csv`
- Scope: 25 services, 27 scripts, and 24 tests moved out of active paths.

## Gitignored Local Artifact Classes

- `AI_Middle_Office/data/exports/`
- `eval/`
- `reports/trial_readiness/`
- `tools/`
- Chinese-named local drawing practice folder; exact pattern is recorded in `.gitignore`.

## Recommended Next Cleanup Order

1. Keep MVP whitelist and compatibility files active.
2. Decide whether the 6 optional DWG/PDF fusion and low-risk quantity files remain active or move to a separate optional archive.

Full row-level inventory: `AI_Middle_Office/docs/biz-2x-workspace-inventory-20260621.csv`

## Notes

- Strict three-field acceptance and recall experiments are not blockers for the current MVP.
- The current MVP remains: `PDF drawings -> GLM-4V itemization -> GB-style four-field bill preview -> Excel`.
