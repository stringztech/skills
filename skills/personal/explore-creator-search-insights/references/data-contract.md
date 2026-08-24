# `csi.android.capture/v1` data contract

Treat raw observations as canonical. Treat CSV and reports as derived projections.

## Artifact set

Use `run-input.json`, `structure-input.json`, `observations-input.ndjson`, and source frames as replay/extractor inputs. Emit these canonical finalized artifacts:

```text
run.json
structure.json
summary.md
observations.ndjson
terms.csv
conflicts.ndjson
evidence-manifest.json
evidence.zip
evidence/
```

Include `contract_version` and `run_id` in every structured artifact or record. Use RFC 3339 timestamps with offsets and stable IDs across files. Treat `summary.md` as a non-authoritative rendering.

Keep `actions.ndjson` in a live run for the input audit. Successful finalization removes the private `.control.json` and staging inputs after canonical artifacts are written; do not treat private control state as a deliverable data source.

## Run context and status

Represent each `run-input.json.list_contexts` entry with:
`list_context_id`, `mode_label`, `category_label`, `requested_category_label`, `language_labels`, `filter_labels`, `region_label`, `capture_status`, `from_top`, `endpoint_reached`, `endpoint_text`, `scroll_steps`, `screen_count`, `evidence_ids`, `scope_note`, and optional `stop_reason`.

Use an empty string when `region_label` was not shown. Set `category_label` only when observed and retain the requested value separately. Require requested language, filter, and region values to be verified selected before capture. Never copy a term-detail `Global` or `Last 7 days` selection into a list context.

Copy validated metadata and completeness into canonical `run.json`. Include `contract_version`, `run_id`, `status`, `capture_window`, `device`, `app`, `capabilities`, `safety`, `limits`, `stop`, and `list_contexts`; replace a raw device serial with `serial_hash`.

Use only these completeness states:

- `point_in_time_endpoint_reached`
- `bounded_no_endpoint`
- `partial_interrupted`
- `blocked`
- `failed`
- `not_attempted`

Qualify an endpoint as complete only for the exact observed session and list context. Record a long list stopped at its capture limit as `bounded_no_endpoint`, never `unbounded`.

## Raw observations

Write one immutable `observations.ndjson` record per visible occurrence, including repeats in overlapping screenshots. Use `kind: list_row` or `kind: detail_field`.

Include these common fields:

`contract_version`, `run_id`, `observation_id`, `kind`, `list_context_id`, `evidence_id`, and `captured_at`.

For a list row, include:
`screen_sequence`, `scroll_index`, `visible_row_index`, `bbox`, `term_text_verbatim`, `term_text_lines`, `primary_metric_text`, `primary_metric_icon`, `blue_percentage_text`, `direction_glyph_text`, `sparkline_present`, `secondary_text_verbatim`, `secondary_kind`, `read_method`, `confidence`, `review_status`, and `original_ocr_text`.

Preserve displayed text exactly. Keep compact units and bounds such as `5.10K` and `1000%+` as strings. Use neutral metric names: the UI did not explicitly label the percentage as growth, change, or gap size. For an unlabeled list percentage, emit `metric_ui_context: list_row_unlabeled` and `metric_semantics_status: unlabeled` in the CSV.

For a detail field, add `term_observation_id`, `term_text_verbatim`, `ui_path`, exact `ui_label`, exact `value_text`, `nested_value`, `bbox`, `read_method`, `confidence`, `review_status`, and `original_ocr_text`. Emit these records when `inspect` persists a `TERM_DETAIL`, `SCOPE_SHEET`, or `RECENCY_SHEET` screen.

Link a detail only when its `term_observation_id` matches a captured list occurrence. Project recognized creator-count, scope, recency, and sampled `Search popularity` context into that term's CSV row. Preserve unrecognized visible detail text in raw observations; leave reserved related-term/video, viewer-insight, or intent-summary CSV fields empty until specialized parsing supports them. Never infer a view count from a heart icon.

## Deduplicated CSV

Deduplicate only within `list_context_id`. Build a match key using Unicode NFC, outer trimming, and line-wrap/whitespace normalization. Preserve case and punctuation; do not fuzzy-merge.

Emit these columns in order:

```text
contract_version,run_id,list_context_id,mode_label,category_label,language_labels_json,first_observed_order,term_display_safe,term_verbatim_json,term_match_key,csv_escape_applied,occurrence_count,primary_metric_text,blue_percentage_text,direction_glyph_text,metric_ui_context,metric_semantics_status,secondary_values_json,detail_scope_text,detail_recency_text,detail_creators_posted_text,related_terms_json,related_videos_json,viewer_insights_json,intent_summary_text,source_observation_ids_json,source_evidence_ids_json,conflict_fields_json,capture_status
```

Use valid JSON arrays/objects in `_json` cells instead of custom delimiters. Use `term_display_safe` for spreadsheet display and preserve every exact source spelling in `term_verbatim_json`. Prefix `term_display_safe` with an apostrophe when the verbatim value, including any leading ASCII whitespace, could be interpreted as a spreadsheet formula beginning with `=`, `+`, `-`, or `@`; set `csv_escape_applied` to `true`. Treat `first_observed_order` as capture sequence, not TikTok rank.

## Conflicts

Merge identical duplicates by adding provenance and incrementing `occurrence_count`. When nonempty values disagree, preserve every raw observation, leave the ambiguous scalar blank, add the field to `conflict_fields_json`, and emit a `conflicts.ndjson` record with:

`conflict_id`, `entity_key`, `field`, `kind`, `observed_values`, and `source_observation_ids`. Add `resolution` only when review resolves the disagreement.

Use conflict kinds `ocr_disagreement`, `temporal_change`, `context_mismatch`, or `unresolved_variant`. A visual review may resolve OCR, but keep `original_ocr_text`. Treat a live metric change as temporal evidence, not an extraction error.

## Structure and claims

Build `structure-input.json.screens` during inspection from exact allowlisted structural labels, screen state/hash, capture time, and evidence IDs. Record navigated controls in `navigation.observed_path` with `order`, `label`, `role`, `interacted`, source state, target text, screen hash, claim status, and privacy-storage status.

During finalization, derive `panel.list_modes`, `content_categories`, `filter_surfaces`, `requested_control_checks`, `term_detail`, and `ranking`. Also add evidence-backed `row_field_presentations` and `secondary_label_vocabulary`. Preserve compatible replay-supplied structure fields rather than inventing missing observations.

Give every structure, absence, and interpretation claim `claim_status: observed|not_observed|inferred`, a scoped context, and `evidence_ids`. Use `not_observed` rather than global `absent`; finalization must fail when a structure claim cites missing evidence.

Represent `Creation & business tools` as a section heading, not a tapped navigation step. Scope `High % Gap` or region absence to the filter sheets examined. Record the blue percentage as being visually grouped inside a sampled `Search popularity` block, with `comparison_basis: null` and inferred semantics.

## Evidence manifest

Give `evidence-manifest.json` the top-level fields `contract_version`, `run_id`, and `evidence`. For every evidence item, record `evidence_id`, relative `path`, `media_type`, `sha256`, `byte_size`, capture timestamp, sequence, screen kind, context/scroll IDs, linked observation IDs, and any UI-tree or OCR artifact path. Add width and height for images.

Never overwrite raw evidence. Give crops or redactions new IDs with `derivative_of` and `transform`. Record excluded captures with `excluded` and `exclusion_reason`. Link hashed XML and OCR JSON as `supporting_artifacts` on their screenshot entry.

Do not persist profile/menu screenshots or unrestricted UI trees. Represent outer navigation only with hashed, sanitized JSON evidence containing the verified safe label, state, action, and screen hash; exclude account text and device serials. List these records in the same manifest as `application/json` evidence.

Build `evidence.zip` atomically and deterministically with sorted relative paths, fixed ZIP timestamps, and no absolute or parent-traversal paths. Return its exact SHA-256 as `evidence_zip_sha256` in successful `finalize` JSON, alongside `device_lease_released`; do not place the archive hash inside its own manifest.

Maintain this invariant: every CSV value points to raw observation IDs, every observation points to hashed evidence, and every structure, absence, or completeness claim points to scoped evidence.
