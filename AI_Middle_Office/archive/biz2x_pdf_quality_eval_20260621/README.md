# BIZ-2x PDF Quality Eval Archive

Archived: 2026-06-21

This folder contains experimental PDF quality-evaluation services, scripts, and tests moved out of the active `app/services/`, `scripts/`, and `tests/` paths.

The current MVP remains:

`PDF drawings -> GLM-4V itemization -> GB-style four-field bill preview -> Excel`

Archived scope:

- `services/`: strict three-field acceptance, gap recall, object recall, feature precision, external recall, v2 takeoff, standard bill, and related quality-eval service modules.
- `scripts/`: strict three-field gates, gap recall, object recall, feature precision, external recall, v2 takeoff, and related quality reports.
- `tests/`: matching tests for the archived experimental scripts.

Manifest:

- `MANIFEST.csv` records original paths and archive paths.

The service group was moved only after dependency audit confirmed no active MVP, legacy API/UI entrypoint, active script, or active test imported these modules.

If these archived experiments need to run again, review imports first. Some archived files still refer to their original `app.services.*` module paths.
