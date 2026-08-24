#!/usr/bin/env python3
"""Black-box capture lifecycle tests using only a temporary fake ADB.

The production package is never imported. All behavior is observed through
``scripts/csi.py`` and declared run artifacts. The fake ADB is placed first on
``PATH`` as a second guard against accidentally invoking a host ADB binary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


CONTRACT_VERSION = "csi.android.capture/v1"
SKILL_ROOT = Path(__file__).resolve().parents[2]
CLI = SKILL_ROOT / "scripts" / "csi.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "capture"

PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PNG_BYTES = base64.b64decode(PNG_B64)
# Fake page zero is the known PNG with one deterministic page byte appended.
PAGE_ZERO_SHA256 = "f0eac4ed40ab268abd3730a522c83cbdb930caaf7a25bbf36c116e4c8182d41f"


class CaptureCliContractTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="csi-capture-")
        self.addCleanup(self._temporary_directory.cleanup)
        self.temp = Path(self._temporary_directory.name)
        serial_suffix = hashlib.sha256(str(self.temp).encode("utf-8")).hexdigest()[:10]
        self.serial = "CAP" + serial_suffix.upper()
        self.adb_log = self.temp / "fake-adb-log.ndjson"
        self.adb_state = self.temp / "fake-adb-state.json"
        self.fake_adb = self.temp / "adb"
        self.preflight_json = self.temp / "preflight.json"
        self._write_fake_adb()
        self._reset_fake_page()
        self._write_preflight()

        self.base_env = os.environ.copy()
        self.base_env.update(
            {
                "PATH": str(self.temp) + os.pathsep + self.base_env.get("PATH", ""),
                "CSI_FAKE_ADB_LOG": str(self.adb_log),
                "CSI_FAKE_ADB_STATE": str(self.adb_state),
                "CSI_FAKE_ADB_PAGES": str(FIXTURES / "pages.json"),
                "CSI_FAKE_ADB_SCENARIO": "endpoint",
                "CSI_TEST_MODE": "1",
                "CSI_TEST_NOW": "1000.0",
            }
        )

    def _write_fake_adb(self) -> None:
        source = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import base64
            import html
            import json
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            log_path = Path(os.environ["CSI_FAKE_ADB_LOG"])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(args) + "\\n")

            state_path = Path(os.environ["CSI_FAKE_ADB_STATE"])
            pages_path = Path(os.environ["CSI_FAKE_ADB_PAGES"])
            scenario = os.environ.get("CSI_FAKE_ADB_SCENARIO", "endpoint")
            pages_by_scenario = json.loads(pages_path.read_text(encoding="utf-8"))
            pages = pages_by_scenario[scenario]
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
            else:
                state = {"page": 0}
            page_number = int(state.get("page", 0))
            page = pages[min(page_number, len(pages) - 1)]

            if args == ["devices", "-l"]:
                print("List of devices attached")
                print(os.environ.get("CSI_FAKE_SERIAL", "CAPTURE") + "\\tdevice model:Fixture")
                raise SystemExit(0)

            if len(args) < 3 or args[0] != "-s":
                print("unsupported unscoped fake adb call", file=sys.stderr)
                raise SystemExit(64)
            command = args[2:]

            if command == ["get-state"]:
                print("device")
                raise SystemExit(0)
            if command == ["shell", "wm", "size"]:
                print("Physical size: 1080x2410")
                raise SystemExit(0)
            if command == ["exec-out", "screencap", "-p"]:
                image = base64.b64decode("__PNG_B64__") + bytes([page_number % 256])
                sys.stdout.buffer.write(image)
                raise SystemExit(0)
            if "uiautomator" in command:
                package = "com.zhiliaoapp.musically"
                nodes = [
                    '<node package="%s" text="Creator Search Insights" '
                    'content-desc="" bounds="[0,100][1080,220]"/>' % package,
                    '<node package="%s" text="%s" content-desc="" '
                    'bounds="[0,770][500,896]"/>'
                    % (package, html.escape(str(page.get("mode", "")), quote=True)),
                ]
                for index, row in enumerate(page.get("rows", [])):
                    top = 920 + index * 190
                    term = html.escape(str(row.get("term", "")), quote=True)
                    metric = html.escape(str(row.get("metric", "")), quote=True)
                    percentage = html.escape(str(row.get("percentage", "")), quote=True)
                    nodes.append(
                        '<node package="%s" text="%s" content-desc="" '
                        'bounds="[42,%d][720,%d]"/>'
                        % (package, term, top, top + 60)
                    )
                    nodes.append(
                        '<node package="%s" text="%s" content-desc="" '
                        'bounds="[42,%d][240,%d]"/>'
                        % (package, metric, top + 70, top + 115)
                    )
                    if percentage:
                        nodes.append(
                            '<node package="%s" text="▲ %s" content-desc="" '
                            'bounds="[300,%d][500,%d]"/>'
                            % (package, percentage, top + 70, top + 115)
                        )
                for field in ("endpoint_text", "stop_text"):
                    if page.get(field):
                        nodes.append(
                            '<node package="%s" text="%s" content-desc="" '
                            'bounds="[100,2200][980,2280]"/>'
                            % (
                                package,
                                html.escape(str(page[field]), quote=True),
                            )
                        )
                print('<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>")
                raise SystemExit(0)
            if command[:3] == ["shell", "input", "swipe"]:
                state["page"] = page_number + 1
                state_path.write_text(json.dumps(state), encoding="utf-8")
                raise SystemExit(0)
            if command[:3] == ["shell", "input", "tap"]:
                raise SystemExit(0)

            print("unsupported fake adb call: %r" % (args,), file=sys.stderr)
            raise SystemExit(65)
            """
        ).replace("__PNG_B64__", PNG_B64)
        self.fake_adb.write_text(source, encoding="utf-8")
        self.fake_adb.chmod(
            self.fake_adb.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    def _reset_fake_page(self) -> None:
        self.adb_state.write_text('{"page": 0}\n', encoding="utf-8")

    def _write_preflight(self) -> None:
        report = {
            "contract_version": CONTRACT_VERSION,
            "_test_fixture": True,
            "ready": True,
            "reason": "ready",
            "adb_path": str(self.fake_adb.resolve()),
            "device": {
                "serial": self.serial,
                "connected": True,
                "authorized": True,
            },
            "app": {
                "package": "com.zhiliaoapp.musically",
                "version_name": "fixture",
                "version_code": "1",
                "installed_verified": True,
            },
            "capabilities": {
                "device_authorized": True,
                "device_unlocked": True,
                "screen_capture": True,
                "screen_reading": True,
                "ocr": True,
                "ui_tree": True,
                "input_binary": True,
                "tap_navigation": True,
            },
            "screen": {
                "width": 1080,
                "height": 2410,
                "hash": PAGE_ZERO_SHA256,
                "state": "CSI_LIST",
                "package": "com.zhiliaoapp.musically",
                "title": "Creator Search Insights",
                "at_top": True,
            },
        }
        self.preflight_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def run_cli(
        self,
        *arguments: object,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process_env = self.base_env.copy()
        process_env["CSI_FAKE_SERIAL"] = self.serial
        if env:
            process_env.update(env)
        return subprocess.run(
            [sys.executable, str(CLI), *(str(argument) for argument in arguments)],
            cwd=SKILL_ROOT,
            env=process_env,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )

    def json_stdout(
        self, result: subprocess.CompletedProcess[str]
    ) -> dict[str, object]:
        self.assertTrue(
            result.stdout.strip(),
            msg=f"CLI emitted no JSON; stderr={result.stderr!r}",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                f"CLI stdout must be one JSON object: {error}; "
                f"stdout={result.stdout!r}; stderr={result.stderr!r}"
            )
        self.assertIsInstance(payload, dict)
        return payload

    def assert_success(
        self, result: subprocess.CompletedProcess[str]
    ) -> dict[str, object]:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r}; stderr={result.stderr!r}",
        )
        payload = self.json_stdout(result)
        self.assertEqual(payload.get("status"), "ok")
        return payload

    def begin_run(self, output_root: Path | None = None) -> tuple[Path, dict[str, object]]:
        output_root = output_root or self.temp / "runs"
        payload = self.assert_success(
            self.run_cli(
                "begin",
                "--output-root",
                output_root,
                "--preflight-json",
                self.preflight_json,
            )
        )
        self.assertIn("run_dir", payload)
        run_dir = Path(str(payload["run_dir"]))
        if not run_dir.is_absolute():
            run_dir = output_root / run_dir
        self.assertTrue(run_dir.is_dir())
        return run_dir.resolve(), payload

    def capture_tab(
        self,
        run_dir: Path,
        *,
        mode: str = "Content gap",
        max_scrolls: int = 5,
        adb_path: Path | None = None,
        scenario: str = "endpoint",
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "capture-tab",
            "--run-dir",
            run_dir,
            "--mode",
            mode,
            "--max-scrolls",
            max_scrolls,
            "--adb-path",
            adb_path or self.fake_adb,
            env={"CSI_FAKE_ADB_SCENARIO": scenario},
        )

    def adb_calls(self) -> list[list[str]]:
        if not self.adb_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.adb_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def input_calls(self) -> list[list[str]]:
        return [
            call
            for call in self.adb_calls()
            if len(call) >= 5 and call[0] == "-s" and call[2:4] == ["shell", "input"]
        ]

    def swipe_calls(self) -> list[list[str]]:
        return [call for call in self.input_calls() if call[4] == "swipe"]

    def screenshot_calls(self) -> list[list[str]]:
        return [
            call
            for call in self.adb_calls()
            if len(call) >= 5 and call[0] == "-s" and "screencap" in call
        ]

    @staticmethod
    def read_json(path: Path) -> dict[str, object]:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise AssertionError(f"expected JSON object in {path}")
        return value

    @staticmethod
    def read_ndjson(path: Path) -> list[dict[str, object]]:
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def list_context(self, run_dir: Path, mode: str) -> dict[str, object]:
        run_input = self.read_json(run_dir / "run-input.json")
        matches = [
            item
            for item in run_input.get("list_contexts", [])
            if item.get("mode_label") == mode
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_inspect_reports_scoped_state_and_persists_hashed_evidence(self) -> None:
        run_dir, _ = self.begin_run()

        payload = self.assert_success(
            self.run_cli("inspect", "--run-dir", run_dir)
        )

        self.assertEqual(payload.get("state"), "CSI_LIST")
        self.assertEqual(payload.get("screen_hash"), PAGE_ZERO_SHA256)
        self.assertEqual(
            payload.get("foreground_package"), "com.zhiliaoapp.musically"
        )
        self.assertIn("Creator Search Insights", json.dumps(payload["safe_anchors"]))
        stop = payload.get("stop")
        self.assertFalse(stop.get("stop") if isinstance(stop, dict) else stop)

        evidence = run_dir / "evidence"
        png = evidence / "frame-000001.png"
        xml = evidence / "frame-000001.xml"
        ocr = evidence / "frame-000001.ocr.json"
        self.assertTrue(png.is_file())
        self.assertTrue(xml.is_file())
        self.assertTrue(ocr.is_file())
        self.assertEqual(hashlib.sha256(png.read_bytes()).hexdigest(), PAGE_ZERO_SHA256)
        self.assertIn("Creator Search Insights", xml.read_text(encoding="utf-8"))
        ocr_payload = json.loads(ocr.read_text(encoding="utf-8"))
        self.assertIn(
            "Creator Search Insights",
            json.dumps(ocr_payload, ensure_ascii=False),
        )
        self.assertEqual(self.input_calls(), [])

    def test_capture_tab_captures_initial_viewport_then_stops_at_endpoint(self) -> None:
        run_dir, _ = self.begin_run()

        payload = self.assert_success(self.capture_tab(run_dir))

        self.assertEqual(payload.get("mode"), "Content gap")
        self.assertEqual(
            payload.get("capture_status"), "point_in_time_endpoint_reached"
        )
        self.assertEqual(payload.get("screen_count"), 2)
        self.assertEqual(payload.get("scroll_steps"), 1)
        self.assertIs(payload.get("endpoint_reached"), True)
        self.assertEqual(payload.get("endpoint_text"), "No more searches")

        context = self.list_context(run_dir, "Content gap")
        self.assertIs(context["from_top"], True)
        self.assertIs(context["endpoint_reached"], True)
        self.assertEqual(context["endpoint_text"], "No more searches")
        self.assertEqual(context["capture_status"], "point_in_time_endpoint_reached")
        self.assertEqual(context["screen_count"], 2)
        self.assertEqual(context["scroll_steps"], 1)

        terms = {
            item.get("term_text_verbatim")
            for item in self.read_ndjson(run_dir / "observations-input.ndjson")
            if item.get("kind") == "list_row"
        }
        self.assertTrue({"alpha topic", "beta topic"}.issubset(terms))
        self.assertNotIn("No more searches", terms)

        calls = self.adb_calls()
        first_screen = min(index for index, call in enumerate(calls) if "screencap" in call)
        first_swipe = min(index for index, call in enumerate(calls) if call in self.swipe_calls())
        self.assertLess(first_screen, first_swipe)
        self.assertEqual(len(self.swipe_calls()), 1)
        self.assertTrue((run_dir / "evidence" / "frame-000001.png").is_file())
        self.assertTrue((run_dir / "evidence" / "frame-000002.png").is_file())

    def test_capture_tab_reports_bounded_snapshot_at_scroll_limit(self) -> None:
        run_dir, _ = self.begin_run()

        payload = self.assert_success(
            self.capture_tab(run_dir, mode="All", max_scrolls=2, scenario="bounded")
        )

        self.assertEqual(payload.get("mode"), "All")
        self.assertEqual(payload.get("capture_status"), "bounded_no_endpoint")
        self.assertEqual(payload.get("screen_count"), 3)
        self.assertEqual(payload.get("scroll_steps"), 2)
        self.assertIs(payload.get("endpoint_reached"), False)
        self.assertEqual(payload.get("endpoint_text"), "")
        self.assertEqual(len(self.swipe_calls()), 2)

        context = self.list_context(run_dir, "All")
        self.assertEqual(context["capture_status"], "bounded_no_endpoint")
        self.assertEqual(context["screen_count"], 3)
        self.assertEqual(context["scroll_steps"], 2)
        self.assertIs(context["endpoint_reached"], False)
        terms = {
            item.get("term_text_verbatim")
            for item in self.read_ndjson(run_dir / "observations-input.ndjson")
            if item.get("kind") == "list_row"
        }
        self.assertTrue(
            {
                "bounded topic zero",
                "bounded topic one",
                "bounded topic two",
            }.issubset(terms)
        )

    def test_capture_tab_stops_on_challenge_and_preserves_partial_evidence(self) -> None:
        run_dir, _ = self.begin_run()

        result = self.capture_tab(run_dir, scenario="blocked")

        self.assertNotEqual(result.returncode, 0)
        payload = self.json_stdout(result)
        self.assertIn("blocked", json.dumps(payload).lower())
        self.assertRegex(
            json.dumps(payload).lower(), r"captcha|verify|challenge|puzzle"
        )
        self.assertEqual(len(self.swipe_calls()), 1)
        self.assertGreaterEqual(
            len(list((run_dir / "evidence").glob("frame-*.png"))), 2
        )
        context = self.list_context(run_dir, "Content gap")
        self.assertEqual(context["capture_status"], "blocked")
        self.assertIs(context["endpoint_reached"], False)
        terms = {
            item.get("term_text_verbatim")
            for item in self.read_ndjson(run_dir / "observations-input.ndjson")
        }
        self.assertIn("safe topic before stop", terms)

    def test_capture_tab_rejects_adb_path_that_differs_from_begin(self) -> None:
        run_dir, _ = self.begin_run()
        other_adb = self.temp / "other-adb"
        shutil.copyfile(self.fake_adb, other_adb)
        other_adb.chmod(self.fake_adb.stat().st_mode)

        result = self.capture_tab(run_dir, adb_path=other_adb)

        self.assertNotEqual(result.returncode, 0)
        payload = self.json_stdout(result)
        self.assertIn("adb", json.dumps(payload).lower())
        self.assertRegex(json.dumps(payload).lower(), r"match|persisted|different")
        self.assertEqual(self.adb_calls(), [])

    def test_finalize_releases_device_serial_lease_for_next_begin(self) -> None:
        first_run, _ = self.begin_run(self.temp / "runs-first")

        contended = self.run_cli(
            "begin",
            "--output-root",
            self.temp / "runs-second",
            "--preflight-json",
            self.preflight_json,
        )
        self.assertNotEqual(contended.returncode, 0)
        self.assertRegex(
            json.dumps(self.json_stdout(contended)).lower(), r"lock|lease|active"
        )

        self.assert_success(self.capture_tab(first_run))
        self.assert_success(self.run_cli("extract", "--run-dir", first_run))
        self.assert_success(self.run_cli("finalize", "--run-dir", first_run))

        self._reset_fake_page()
        second_run, _ = self.begin_run(self.temp / "runs-second")
        self.assertNotEqual(first_run, second_run)

        # Leave no active lease behind, even when this test is run repeatedly.
        self.assert_success(self.capture_tab(second_run))
        self.assert_success(self.run_cli("extract", "--run-dir", second_run))
        self.assert_success(self.run_cli("finalize", "--run-dir", second_run))


if __name__ == "__main__":
    unittest.main()
