#!/usr/bin/env python3
"""Black-box contract tests for the offline CSI artifact pipeline.

These tests intentionally use only the public ``scripts/csi.py`` process seam.
They do not import production modules and never invoke ADB.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


CONTRACT_VERSION = "csi.android.capture/v1"
SKILL_ROOT = Path(__file__).resolve().parents[2]
CLI = SKILL_ROOT / "scripts" / "csi.py"

# A known 1x1 PNG. The digest below is an independent, fixed test vector rather
# than a digest calculated from production output.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PNG_SHA256 = "431ced6916a2a21a156e38701afe55bbd7f88969fbbfc56d7fe099d47f265460"

TERMS_HEADERS = [
    "contract_version",
    "run_id",
    "list_context_id",
    "mode_label",
    "category_label",
    "language_labels_json",
    "first_observed_order",
    "term_display_safe",
    "term_verbatim_json",
    "term_match_key",
    "csv_escape_applied",
    "occurrence_count",
    "primary_metric_text",
    "blue_percentage_text",
    "direction_glyph_text",
    "metric_ui_context",
    "metric_semantics_status",
    "secondary_values_json",
    "detail_scope_text",
    "detail_recency_text",
    "detail_creators_posted_text",
    "related_terms_json",
    "related_videos_json",
    "viewer_insights_json",
    "intent_summary_text",
    "source_observation_ids_json",
    "source_evidence_ids_json",
    "conflict_fields_json",
    "capture_status",
]


def context(
    context_id: str,
    mode_label: str,
    *,
    status: str = "point_in_time_endpoint_reached",
    from_top: bool = True,
    endpoint_reached: bool = True,
    endpoint_text: str = "No more searches",
    scroll_steps: int = 1,
    screen_count: int = 2,
) -> dict[str, object]:
    """Build one canonical list-context input record."""

    return {
        "list_context_id": context_id,
        "mode_label": mode_label,
        "category_label": "Suggested",
        "language_labels": ["English"],
        "filter_labels": [],
        "region_label": "",
        "capture_status": status,
        "from_top": from_top,
        "endpoint_reached": endpoint_reached,
        "endpoint_text": endpoint_text,
        "scroll_steps": scroll_steps,
        "screen_count": screen_count,
    }


def observation(
    observation_id: str,
    context_id: str,
    evidence_id: str,
    term: str,
    *,
    term_lines: list[str] | None = None,
    metric: str = "170K",
    percentage: str = "",
    direction: str = "",
    secondary: str = "",
    secondary_kind: str = "",
    captured_at: str = "2026-08-24T12:00:00+03:00",
    screen_sequence: int = 1,
    scroll_index: int = 0,
    visible_row_index: int = 1,
    read_method: str = "ocr",
    confidence: float = 0.98,
    review_status: str = "unreviewed",
    original_ocr_text: str | None = None,
) -> dict[str, object]:
    """Build one canonical visible-row observation."""

    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": "contract-run",
        "kind": "list_row",
        "observation_id": observation_id,
        "list_context_id": context_id,
        "evidence_id": evidence_id,
        "captured_at": captured_at,
        "screen_sequence": screen_sequence,
        "scroll_index": scroll_index,
        "visible_row_index": visible_row_index,
        "bbox": [42, 1009, 700, 90],
        "term_text_verbatim": term,
        "term_text_lines": term_lines if term_lines is not None else [term],
        "primary_metric_text": metric,
        "primary_metric_icon": "flame",
        "blue_percentage_text": percentage,
        "direction_glyph_text": direction,
        "sparkline_present": True,
        "secondary_text_verbatim": secondary,
        "secondary_kind": secondary_kind,
        "read_method": read_method,
        "confidence": confidence,
        "review_status": review_status,
        "original_ocr_text": original_ocr_text if original_ocr_text is not None else term,
    }


class ContractCliTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="csi-contract-")
        self.addCleanup(self._temporary_directory.cleanup)
        self.temp = Path(self._temporary_directory.name)
        self._source_counter = 0

    def _write_source(
        self,
        observations: list[dict[str, object]],
        contexts: list[dict[str, object]],
        *,
        omitted_evidence_ids: set[str] | None = None,
        frame_order: list[str] | None = None,
    ) -> Path:
        self._source_counter += 1
        source = self.temp / f"source-{self._source_counter}"
        frames = source / "frames"
        frames.mkdir(parents=True)

        omitted = omitted_evidence_ids or set()
        evidence_ids = frame_order or sorted(
            {str(item["evidence_id"]) for item in observations}
        )
        for evidence_id in evidence_ids:
            if evidence_id not in omitted:
                (frames / f"{evidence_id}.png").write_bytes(PNG_BYTES)

        run_input = {
            "contract_version": CONTRACT_VERSION,
            "run_id": "contract-run",
            "capture_window": {
                "started_at": "2026-08-24T12:00:00+03:00",
                "ended_at": "2026-08-24T12:10:00+03:00",
            },
            "device": {
                "serial": "offline-replay",
                "model": "fixture",
                "android_version": "fixture",
            },
            "app": {
                "package": "com.zhiliaoapp.musically",
                "version_name": "fixture",
                "version_code": "fixture",
            },
            "capabilities": {
                "device_authorized": True,
                "screen_capture": True,
                "ocr": True,
                "ui_tree": True,
                "tap_navigation": True,
            },
            "safety": {"read_only": True, "device_interaction": False},
            "stop": {"reason": "offline_fixture"},
            "list_contexts": contexts,
        }
        (source / "run-input.json").write_text(
            json.dumps(run_input, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (source / "structure-input.json").write_text(
            json.dumps(
                {
                    "contract_version": CONTRACT_VERSION,
                    "run_id": "contract-run",
                    "navigation_path": [
                        "TikTok",
                        "Profile",
                        "Profile menu",
                        "Creator Search Insights",
                    ],
                    "mode_labels": [item["mode_label"] for item in contexts],
                    "controls": [],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        with (source / "observations-input.ndjson").open("w", encoding="utf-8") as handle:
            for item in observations:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        return source

    def _invoke(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(CLI), *(str(argument) for argument in arguments)],
            cwd=SKILL_ROOT,
            env=environment,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _json_stdout(self, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                "CLI stdout must be exactly one JSON document; "
                f"returncode={result.returncode}, stdout={result.stdout!r}, "
                f"stderr={result.stderr!r}, error={error}"
            )
        self.assertIsInstance(payload, dict)
        return payload

    def _invoke_success(self, *arguments: object) -> dict[str, object]:
        result = self._invoke(*arguments)
        if result.returncode != 0:
            self.fail(
                f"CLI command failed: {arguments!r}\n"
                f"returncode={result.returncode}\n"
                f"stdout={result.stdout}\n"
                f"stderr={result.stderr}"
            )
        payload = self._json_stdout(result)
        self.assertEqual(payload.get("status"), "ok")
        return payload

    def _run_pipeline(self, source: Path, output_root: Path) -> Path:
        replay = self._invoke_success(
            "replay", "--source", source, "--output-root", output_root
        )
        self.assertIn("run_dir", replay)
        run_dir = Path(str(replay["run_dir"]))
        if not run_dir.is_absolute():
            run_dir = output_root / run_dir

        self._invoke_success("extract", "--run-dir", run_dir)
        self._invoke_success("finalize", "--run-dir", run_dir)

        expected_outputs = {
            "run.json",
            "structure.json",
            "summary.md",
            "observations.ndjson",
            "terms.csv",
            "conflicts.ndjson",
            "evidence-manifest.json",
            "evidence.zip",
        }
        self.assertEqual(
            expected_outputs,
            {path.name for path in run_dir.iterdir() if path.name in expected_outputs},
        )
        return run_dir

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise AssertionError(f"expected a JSON object in {path}")
        return value

    @staticmethod
    def _read_ndjson(path: Path) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise AssertionError(
                        f"expected an object at {path}:{line_number}, got {type(value)!r}"
                    )
                records.append(value)
        return records

    @staticmethod
    def _read_terms(path: Path) -> tuple[list[str], list[dict[str, str]]]:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            return list(reader.fieldnames or []), rows

    def test_extract_preserves_unicode_multiline_text_and_read_provenance(self) -> None:
        term = "ሰላም 世界 fypシ゚viral best friend شاعری"
        lines = ["ሰላም 世界 fypシ゚viral", "best friend شاعری"]
        item = observation(
            "obs-unicode",
            "ctx-gap",
            "frame-unicode",
            term,
            term_lines=lines,
            confidence=0.873,
            review_status="reviewed",
            original_ocr_text="ሰላም 世界 fypシ゚viral\nbest friend شاعری",
        )
        source = self._write_source(
            [item], [context("ctx-gap", "Content gap")]
        )

        run_dir = self._run_pipeline(source, self.temp / "runs")

        canonical = self._read_ndjson(run_dir / "observations.ndjson")
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["term_text_verbatim"], term)
        self.assertEqual(canonical[0]["term_text_lines"], lines)
        self.assertEqual(canonical[0]["original_ocr_text"], item["original_ocr_text"])
        self.assertEqual(canonical[0]["read_method"], "ocr")
        self.assertEqual(canonical[0]["confidence"], 0.873)
        self.assertEqual(canonical[0]["review_status"], "reviewed")

        _, terms = self._read_terms(run_dir / "terms.csv")
        self.assertEqual(terms[0]["term_display_safe"], term)
        self.assertEqual(json.loads(terms[0]["term_verbatim_json"]), [term])
        self.assertEqual(terms[0]["csv_escape_applied"], "false")

    def test_unlabeled_blue_percentage_remains_semantically_neutral(self) -> None:
        item = observation(
            "obs-percentage",
            "ctx-gap",
            "frame-percentage",
            "shrimp aquascape",
            percentage="1000%+",
            direction="▲",
            secondary="High content gap",
            secondary_kind="badge",
        )
        source = self._write_source(
            [item], [context("ctx-gap", "Content gap")]
        )

        run_dir = self._run_pipeline(source, self.temp / "runs")

        _, rows = self._read_terms(run_dir / "terms.csv")
        self.assertEqual(rows[0]["blue_percentage_text"], "1000%+")
        self.assertEqual(rows[0]["direction_glyph_text"], "▲")
        self.assertEqual(rows[0]["metric_ui_context"], "list_row_unlabeled")
        self.assertEqual(rows[0]["metric_semantics_status"], "unlabeled")

    def test_high_gap_check_reflects_the_captured_filter_sheet(self) -> None:
        item = observation(
            "obs-filter",
            "ctx-gap",
            "frame-filter",
            "filter evidence term",
        )
        source = self._write_source(
            [item], [context("ctx-gap", "Content gap")]
        )
        structure_path = source / "structure-input.json"
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        structure["screens"] = [
            {
                "screen_id": "frame-filter",
                "state": "FILTER_SHEET",
                "observed_labels": ["English", "High % Gap", "Apply"],
                "claim_status": "observed",
                "scope": "captured filter sheet fixture",
                "evidence_ids": ["frame-filter"],
            }
        ]
        structure_path.write_text(
            json.dumps(structure, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        run_dir = self._run_pipeline(source, self.temp / "runs-high-gap")
        derived = self._read_json(run_dir / "structure.json")
        check = next(
            entry
            for entry in derived["requested_control_checks"]
            if entry["control"] == "High % Gap"
        )
        self.assertEqual(check["status"], "observed")
        self.assertEqual(check["claim_status"], "observed")
        self.assertEqual(check["evidence_ids"], ["frame-filter"])

    def test_finalize_omits_unsupported_detail_absence_claim(self) -> None:
        item = observation(
            "obs-no-detail",
            "ctx-gap",
            "frame-no-detail",
            "list-only term",
        )
        source = self._write_source(
            [item], [context("ctx-gap", "Content gap")]
        )

        run_dir = self._run_pipeline(source, self.temp / "runs-no-detail")
        structure = self._read_json(run_dir / "structure.json")
        self.assertNotIn("term_detail", structure)
        for claim in structure.get("row_field_presentations", []):
            self.assertTrue(claim.get("scope"))
            self.assertEqual(claim.get("evidence_ids"), ["frame-no-detail"])

    def test_deduplication_is_scoped_to_the_list_context(self) -> None:
        observations = [
            observation(
                "obs-gap-1",
                "ctx-gap",
                "frame-gap-1",
                "same topic",
                metric="170K",
            ),
            observation(
                "obs-gap-2",
                "ctx-gap",
                "frame-gap-2",
                "same topic",
                metric="170K",
                screen_sequence=2,
                scroll_index=1,
            ),
            observation(
                "obs-all-1",
                "ctx-all",
                "frame-all-1",
                "same topic",
                metric="999K",
            ),
        ]
        contexts = [
            context("ctx-gap", "Content gap"),
            context(
                "ctx-all",
                "All",
                status="bounded_no_endpoint",
                endpoint_reached=False,
                endpoint_text="",
            ),
        ]
        source = self._write_source(observations, contexts)

        run_dir = self._run_pipeline(source, self.temp / "runs")

        _, rows = self._read_terms(run_dir / "terms.csv")
        self.assertEqual(len(rows), 2)
        by_context = {row["list_context_id"]: row for row in rows}
        self.assertEqual(set(by_context), {"ctx-gap", "ctx-all"})
        self.assertEqual(by_context["ctx-gap"]["occurrence_count"], "2")
        self.assertEqual(by_context["ctx-all"]["occurrence_count"], "1")
        self.assertEqual(by_context["ctx-gap"]["primary_metric_text"], "170K")
        self.assertEqual(by_context["ctx-all"]["primary_metric_text"], "999K")
        self.assertEqual(self._read_ndjson(run_dir / "conflicts.ndjson"), [])

    def test_same_context_metric_disagreement_is_written_to_conflicts(self) -> None:
        observations = [
            observation(
                "obs-conflict-1",
                "ctx-gap",
                "frame-conflict-1",
                "changing topic",
                metric="170K",
            ),
            observation(
                "obs-conflict-2",
                "ctx-gap",
                "frame-conflict-2",
                "changing topic",
                metric="171K",
                captured_at="2026-08-24T12:01:00+03:00",
                screen_sequence=2,
                scroll_index=1,
            ),
        ]
        source = self._write_source(
            observations, [context("ctx-gap", "Content gap")]
        )

        run_dir = self._run_pipeline(source, self.temp / "runs")

        conflicts = self._read_ndjson(run_dir / "conflicts.ndjson")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["field"], "primary_metric_text")
        self.assertEqual(conflicts[0]["kind"], "temporal_change")
        self.assertEqual(conflicts[0]["observed_values"], ["170K", "171K"])
        self.assertEqual(
            conflicts[0]["source_observation_ids"],
            ["obs-conflict-1", "obs-conflict-2"],
        )

        _, rows = self._read_terms(run_dir / "terms.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            json.loads(rows[0]["conflict_fields_json"]), ["primary_metric_text"]
        )
        self.assertEqual(
            json.loads(rows[0]["source_observation_ids_json"]),
            ["obs-conflict-1", "obs-conflict-2"],
        )

    def test_nonidentical_term_variants_are_never_silently_first_wins(self) -> None:
        observations = [
            observation(
                "obs-term-1",
                "ctx-gap",
                "frame-term-1",
                "wrapped  topic",
            ),
            observation(
                "obs-term-2",
                "ctx-gap",
                "frame-term-2",
                "wrapped topic",
                screen_sequence=2,
                scroll_index=1,
            ),
        ]
        source = self._write_source(
            observations, [context("ctx-gap", "Content gap")]
        )

        run_dir = self._run_pipeline(source, self.temp / "runs-term-variant")
        conflicts = self._read_ndjson(run_dir / "conflicts.ndjson")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["field"], "term_text_verbatim")
        self.assertEqual(conflicts[0]["kind"], "unresolved_variant")
        self.assertEqual(
            conflicts[0]["observed_values"], ["wrapped  topic", "wrapped topic"]
        )

        _, rows = self._read_terms(run_dir / "terms.csv")
        self.assertEqual(rows[0]["term_display_safe"], "")
        self.assertEqual(
            json.loads(rows[0]["term_verbatim_json"]),
            ["wrapped  topic", "wrapped topic"],
        )
        self.assertEqual(
            json.loads(rows[0]["conflict_fields_json"]),
            ["term_text_verbatim"],
        )

    def test_terms_csv_neutralizes_spreadsheet_formula_prefixes(self) -> None:
        dangerous_terms = [
            "=1+1",
            "+SUM(A1:A2)",
            "-2+3",
            "@cmd",
            " \t=HYPERLINK(\"https://invalid.example\")",
        ]
        all_terms = [*dangerous_terms, "safe topic"]
        observations = [
            observation(
                f"obs-formula-{index}",
                "ctx-all",
                "frame-formulas",
                term,
                visible_row_index=index,
            )
            for index, term in enumerate(all_terms, start=1)
        ]
        source = self._write_source(
            observations,
            [
                context(
                    "ctx-all",
                    "All",
                    status="bounded_no_endpoint",
                    endpoint_reached=False,
                    endpoint_text="",
                )
            ],
        )

        run_dir = self._run_pipeline(source, self.temp / "runs")

        headers, rows = self._read_terms(run_dir / "terms.csv")
        self.assertEqual(headers, TERMS_HEADERS)
        by_verbatim = {
            json.loads(row["term_verbatim_json"])[0]: row for row in rows
        }
        for term in dangerous_terms:
            with self.subTest(term=term):
                self.assertEqual(by_verbatim[term]["term_display_safe"], "'" + term)
                self.assertEqual(by_verbatim[term]["csv_escape_applied"], "true")
        self.assertEqual(by_verbatim["safe topic"]["term_display_safe"], "safe topic")
        self.assertEqual(by_verbatim["safe topic"]["csv_escape_applied"], "false")

    def test_all_completeness_statuses_survive_finalize(self) -> None:
        contexts = [
            context("ctx-complete", "Content gap"),
            context(
                "ctx-bounded",
                "All",
                status="bounded_no_endpoint",
                endpoint_reached=False,
                endpoint_text="",
            ),
            context(
                "ctx-partial",
                "Searches by followers",
                status="partial_interrupted",
                from_top=False,
                endpoint_reached=False,
                endpoint_text="",
            ),
            context(
                "ctx-blocked",
                "Blocked fixture",
                status="blocked",
                from_top=False,
                endpoint_reached=False,
                endpoint_text="",
                scroll_steps=0,
                screen_count=0,
            ),
            context(
                "ctx-failed",
                "Failed fixture",
                status="failed",
                from_top=False,
                endpoint_reached=False,
                endpoint_text="",
                scroll_steps=0,
                screen_count=0,
            ),
            context(
                "ctx-unattempted",
                "Not attempted fixture",
                status="not_attempted",
                from_top=False,
                endpoint_reached=False,
                endpoint_text="",
                scroll_steps=0,
                screen_count=0,
            ),
        ]
        observations = [
            observation(
                "obs-complete",
                "ctx-complete",
                "frame-complete",
                "complete topic",
            ),
            observation(
                "obs-bounded",
                "ctx-bounded",
                "frame-bounded",
                "bounded topic",
            ),
        ]
        source = self._write_source(observations, contexts)

        run_dir = self._run_pipeline(source, self.temp / "runs")

        run = self._read_json(run_dir / "run.json")
        actual = {
            item["list_context_id"]: item["capture_status"]
            for item in run["list_contexts"]
        }
        self.assertEqual(
            actual,
            {
                "ctx-complete": "point_in_time_endpoint_reached",
                "ctx-bounded": "bounded_no_endpoint",
                "ctx-partial": "partial_interrupted",
                "ctx-blocked": "blocked",
                "ctx-failed": "failed",
                "ctx-unattempted": "not_attempted",
            },
        )

        _, rows = self._read_terms(run_dir / "terms.csv")
        row_statuses = {
            row["list_context_id"]: row["capture_status"] for row in rows
        }
        self.assertEqual(
            row_statuses,
            {
                "ctx-complete": "point_in_time_endpoint_reached",
                "ctx-bounded": "bounded_no_endpoint",
            },
        )

    def test_evidence_manifest_has_sha256_and_bidirectional_provenance(self) -> None:
        observations = [
            observation(
                "obs-evidence-1",
                "ctx-gap",
                "frame-evidence",
                "evidence topic one",
                visible_row_index=1,
            ),
            observation(
                "obs-evidence-2",
                "ctx-gap",
                "frame-evidence",
                "evidence topic two",
                visible_row_index=2,
            ),
        ]
        source = self._write_source(
            observations, [context("ctx-gap", "Content gap")]
        )

        run_dir = self._run_pipeline(source, self.temp / "runs")

        manifest = self._read_json(run_dir / "evidence-manifest.json")
        self.assertEqual(manifest["contract_version"], CONTRACT_VERSION)
        self.assertEqual(manifest["run_id"], "contract-run")
        self.assertEqual(len(manifest["evidence"]), 1)
        evidence = manifest["evidence"][0]
        self.assertEqual(evidence["evidence_id"], "frame-evidence")
        self.assertEqual(evidence["path"], "evidence/frame-evidence.png")
        self.assertEqual(evidence["media_type"], "image/png")
        self.assertEqual(evidence["sha256"], PNG_SHA256)
        self.assertEqual(evidence["byte_size"], 68)
        self.assertEqual(evidence["width"], 1)
        self.assertEqual(evidence["height"], 1)
        self.assertEqual(evidence["list_context_id"], "ctx-gap")
        self.assertEqual(
            evidence["observation_ids"], ["obs-evidence-1", "obs-evidence-2"]
        )
        copied = run_dir / str(evidence["path"])
        self.assertEqual(copied.read_bytes(), PNG_BYTES)

        canonical = self._read_ndjson(run_dir / "observations.ndjson")
        self.assertEqual(
            {item["evidence_id"] for item in canonical}, {"frame-evidence"}
        )
        _, rows = self._read_terms(run_dir / "terms.csv")
        self.assertEqual(
            {tuple(json.loads(row["source_evidence_ids_json"])) for row in rows},
            {("frame-evidence",)},
        )

    def test_replay_fails_when_an_observation_references_missing_evidence(self) -> None:
        item = observation(
            "obs-missing",
            "ctx-gap",
            "frame-missing",
            "missing evidence topic",
        )
        source = self._write_source(
            [item],
            [context("ctx-gap", "Content gap")],
            omitted_evidence_ids={"frame-missing"},
        )
        output_root = self.temp / "runs"

        result = self._invoke(
            "replay", "--source", source, "--output-root", output_root
        )

        self.assertNotEqual(result.returncode, 0)
        payload = self._json_stdout(result)
        self.assertEqual(payload.get("status"), "error")
        self.assertEqual(payload["error"]["code"], "missing_evidence")
        self.assertIn("frame-missing", payload["error"]["evidence_ids"])
        self.assertEqual(list(output_root.rglob("evidence.zip")), [])

    def test_evidence_zip_is_byte_deterministic(self) -> None:
        observations = [
            observation(
                "obs-zip-b",
                "ctx-gap",
                "frame-b",
                "zip topic b",
                visible_row_index=2,
            ),
            observation(
                "obs-zip-a",
                "ctx-gap",
                "frame-a",
                "zip topic a",
                visible_row_index=1,
            ),
        ]
        source = self._write_source(
            observations,
            [context("ctx-gap", "Content gap")],
            frame_order=["frame-b", "frame-a"],
        )

        first = self._run_pipeline(source, self.temp / "runs-a") / "evidence.zip"
        second = self._run_pipeline(source, self.temp / "runs-b") / "evidence.zip"

        self.assertEqual(
            hashlib.sha256(first.read_bytes()).hexdigest(),
            hashlib.sha256(second.read_bytes()).hexdigest(),
        )
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            names = archive.namelist()
            self.assertEqual(
                names,
                [
                    "evidence-manifest.json",
                    "evidence/frame-a.png",
                    "evidence/frame-b.png",
                ],
            )
            self.assertEqual(names, sorted(names))
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertFalse(info.filename.startswith("/"))
                self.assertNotIn("..", Path(info.filename).parts)
            self.assertEqual(archive.read("evidence/frame-a.png"), PNG_BYTES)
            self.assertEqual(archive.read("evidence/frame-b.png"), PNG_BYTES)


if __name__ == "__main__":
    unittest.main()
