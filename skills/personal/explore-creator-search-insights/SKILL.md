---
name: explore-creator-search-insights
description: Safely capture TikTok Creator Search Insights from an authorized Android device with read-only navigation, evidence-backed extraction, and no content engagement.
disable-model-invocation: true
---

# Explore Creator Search Insights

Capture Creator Search Insights as a read-only, evidence-backed observation. Treat TikTok as live UI: verify the current screen instead of assuming the dated map still applies.

## Load the guidance

Read these references before acting:

1. Read [safety.md](references/safety.md) before any device command.
2. Read [platform-runtime.md](references/platform-runtime.md) before preflight or CLI use.
3. Read [data-contract.md](references/data-contract.md) before capture, extraction, replay, or finalization.
4. Read [ui-map-2026-08-24.md](references/ui-map-2026-08-24.md) for navigation hints and known labels. Treat it as a dated observation, not current truth.

## Run the workflow

1. Run `preflight` only with an explicit ADB executable path and chosen OCR policy. Confirm one authorized device, screen capture, a text-reading path, and tap navigation.
2. Disclose the complete capability report to the user before running `begin` or navigating. Stop if any required capability is missing.
3. Start a run with `begin` only from that capability report. Require its exact ADB path and acquire the device lease; stop if another run owns the device.
4. Inspect the current screen before each action. Route every proposal through `policy`, then use `act` only with the bound ADB path, current screen hash, and a target label/bounds that match a fresh safe anchor. Never guess an icon or reuse stale geometry.
5. Navigate to Creator Search Insights. Try the verified direct profile-menu route first, a verified Creator Tools/TikTok Studio intermediary second, and the exact search fallback only when both are unavailable.
6. Record exact mode, category, filter, scope, recency, and option labels before changing view state. Enumerate the visible filter sheet and explicitly check whether `High % Gap` and a region selector exist. Apply only explicitly requested, non-persistent view filters allowed by [safety.md](references/safety.md).
7. Capture a requested list mode only after verifying the top-of-list state, requested mode, and requested language/filter/region selections. Unless the invocation narrows scope, capture `Content gap` first, then `All`, then `Searches by followers`. Preserve every visible occurrence and screenshot before deduplication.
8. Unless the invocation opts out, open one safe Content Gap term whose row visibly includes a blue percentage, then inspect each relevant detail, scope, and recency screen so evidence-linked `detail_field` observations are recorded. Keep detail-only values attached to that term.
9. Stop immediately on login, CAPTCHA, anti-automation, authorization loss, or an unknown state. Do not work around the blocker.
10. Run `extract`, review conflicts and derived structure, then run `finalize`. Record the returned `evidence_zip_sha256` and device-lease release result. Use copying-only `replay` for offline reprocessing without a device.
11. Report exact artifacts, archive hash, scoped completeness, unresolved conflicts, and every limitation. Never describe a bounded capture as an unbounded feed or a point-in-time endpoint as permanent completeness.

## Preserve evidence integrity

- Keep raw observations immutable and separate from the deduplicated CSV.
- Preserve displayed spelling, capitalization, symbols, and apparent typos.
- Trace every extracted value and structure claim to observation and evidence IDs.
- Label interpretations as inferred. Do not call an unlabeled percentage growth, change, or gap size.
- Keep numeric order distinct from first-observed order; do not claim ranking unless TikTok labels it.
- Finish partial or blocked runs with manifests and stop details instead of discarding evidence.
