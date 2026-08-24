#!/usr/bin/env python3
"""Black-box hardening regressions for the Creator Search Insights CLI.

The production package is never imported. Every assertion crosses the public
``scripts/csi.py`` process seam, and every Android command targets a temporary
fake ADB executable. No real ADB binary or device is used.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


CONTRACT_VERSION = "csi.android.capture/v1"
TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
SKILL_ROOT = Path(__file__).resolve().parents[2]
CLI = SKILL_ROOT / "scripts" / "csi.py"
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PNG_BYTES = base64.b64decode(PNG_B64)
SCREEN_HASH = hashlib.sha256(PNG_BYTES).hexdigest()
CONTENT_GAP_BOUNDS = "20,300,320,80"


class HardeningCliTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="csi-hardening-")
        self.temp = Path(self._temporary_directory.name)
        self.fake_adb = self.temp / "fake-adb"
        self.adb_log = self.temp / "fake-adb.ndjson"
        self._report_number = 0
        self._run_dirs: list[Path] = []
        self._write_fake_adb()

        self.base_env = os.environ.copy()
        for name in (
            "CSI_TEST_MODE",
            "CSI_TEST_NOW",
            "CSI_FAKE_SCREEN",
            "CSI_FAKE_ADB_DELAY_SECONDS",
            "CSI_FAKE_ADB_STARTED",
        ):
            self.base_env.pop(name, None)
        self.base_env.update(
            {
                "CSI_FAKE_ADB_LOG": str(self.adb_log),
                "PYTHONUTF8": "1",
            }
        )

    def tearDown(self) -> None:
        # A failed or deliberately incomplete live run retains its serial lease.
        # Remove only the exact leases created by this temporary test run.
        for run_dir in self._run_dirs:
            control_path = run_dir / ".control.json"
            if not control_path.is_file():
                continue
            try:
                control = json.loads(control_path.read_text(encoding="utf-8"))
                lease_path = Path(str(control.get("lease_path") or ""))
                if lease_path.is_file():
                    lease_path.unlink()
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        self._temporary_directory.cleanup()

    def _write_fake_adb(self) -> None:
        source = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import base64
            import json
            import os
            from pathlib import Path
            import sys
            import time

            args = sys.argv[1:]
            log_path = Path(os.environ["CSI_FAKE_ADB_LOG"])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(args) + "\\n")

            if len(args) < 3 or args[0] != "-s":
                print("unsupported unscoped fake adb call", file=sys.stderr)
                raise SystemExit(64)

            command = args[2:]
            screen = os.environ.get("CSI_FAKE_SCREEN", "csi")

            if command == ["exec-out", "screencap", "-p"]:
                marker = os.environ.get("CSI_FAKE_ADB_STARTED")
                if marker:
                    Path(marker).write_text("started\\n", encoding="utf-8")
                delay = float(os.environ.get("CSI_FAKE_ADB_DELAY_SECONDS", "0"))
                if delay:
                    time.sleep(delay)
                sys.stdout.buffer.write(base64.b64decode("__PNG_B64__"))
                raise SystemExit(0)

            if command == ["shell", "uiautomator", "dump", "/dev/tty"]:
                package = "com.zhiliaoapp.musically"
                if screen == "profile":
                    nodes = [
                        '<node package="%s" text="Edit profile" bounds="[20,200][500,280]"/>' % package,
                        '<node package="%s" text="Followers" bounds="[20,320][250,390]"/>' % package,
                        '<node package="%s" text="Following" bounds="[280,320][520,390]"/>' % package,
                    ]
                else:
                    nodes = [
                        '<node package="%s" text="Creator Search Insights" bounds="[0,100][1080,220]"/>' % package,
                        '<node package="%s" text="All" bounds="[360,300][520,380]"/>' % package,
                        '<node package="%s" text="Content gap" bounds="[20,300][340,380]"/>' % package,
                        '<node package="%s" text="Searches by followers" bounds="[540,300][1040,380]"/>' % package,
                        '<node package="%s" text="Suggested" checked="true" bounds="[20,420][300,500]"/>' % package,
                        '<node package="%s" text="hardening topic" bounds="[40,620][700,680]"/>' % package,
                        '<node package="%s" text="5.10K" bounds="[40,700][220,750]"/>' % package,
                        '<node package="%s" text="▲ 250%%" bounds="[280,700][500,750]"/>' % package,
                    ]
                print('<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>")
                print("UI hierarchy dumped to: /dev/tty")
                raise SystemExit(0)

            if command == ["shell", "dumpsys", "window", "windows"]:
                print(
                    "mCurrentFocus=Window{42 u0 "
                    "com.zhiliaoapp.musically/com.zhiliaoapp.musically.MainActivity}"
                )
                raise SystemExit(0)

            if command[:2] == ["shell", "input"]:
                raise SystemExit(0)
            if command[:3] == ["shell", "am", "start"]:
                raise SystemExit(0)

            print("unsupported fake adb call: %r" % (args,), file=sys.stderr)
            raise SystemExit(65)
            """
        ).replace("__PNG_B64__", PNG_B64)
        self.fake_adb.write_text(source, encoding="utf-8")
        self.fake_adb.chmod(
            self.fake_adb.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )

    def run_cli(
        self,
        *arguments: object,
        env: dict[str, str] | None = None,
        timeout: float = 20,
    ) -> subprocess.CompletedProcess[str]:
        process_env = self.base_env.copy()
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
            timeout=timeout,
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

    def assert_error(
        self, result: subprocess.CompletedProcess[str], expected_code: str
    ) -> dict[str, object]:
        self.assertNotEqual(
            result.returncode,
            0,
            msg=f"command unexpectedly succeeded: stdout={result.stdout!r}",
        )
        payload = self.json_stdout(result)
        self.assertEqual(payload.get("status"), "error")
        error = payload.get("error")
        self.assertIsInstance(error, dict)
        self.assertEqual(error.get("code"), expected_code)
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
        self.assertIn(payload.get("status", "ok"), {"ok", None})
        return payload

    def adb_calls(self) -> list[list[str]]:
        if not self.adb_log.is_file():
            return []
        return [
            json.loads(line)
            for line in self.adb_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def input_calls(self) -> list[list[str]]:
        calls = []
        for argv in self.adb_calls():
            command = argv[2:] if len(argv) >= 3 and argv[0] == "-s" else argv
            if command[:2] == ["shell", "input"] or command[:3] == [
                "shell",
                "am",
                "start",
            ]:
                calls.append(argv)
        return calls

    def write_preflight(
        self,
        *,
        include_adb_path: bool = True,
        package: str = TIKTOK_PACKAGE,
        state: str = "CSI_LIST",
        test_fixture: bool = False,
        at_top: bool = True,
    ) -> Path:
        self._report_number += 1
        serial_seed = f"{self.temp}:{self._report_number}".encode("utf-8")
        serial = "HARD" + hashlib.sha256(serial_seed).hexdigest()[:12].upper()
        report: dict[str, object] = {
            "contract_version": CONTRACT_VERSION,
            "ready": True,
            "reason": "ready",
            "device": {
                "serial": serial,
                "connected": True,
                "authorized": True,
                "model": "synthetic-fixture",
            },
            "app": {
                "package": package,
                "version_name": "fixture",
                "version_code": "1",
                "installed_verified": True,
            },
            "capabilities": {
                "device_authorized": True,
                "device_unlocked": True,
                "screen_capture": True,
                "screen_reading": True,
                "ocr": False,
                "ui_tree": True,
                "input_binary": True,
                "tap_navigation": True,
            },
            "screen": {
                "width": 1080,
                "height": 2410,
                "hash": SCREEN_HASH,
                "state": state,
                "package": package,
                "title": "Creator Search Insights",
                "at_top": at_top,
            },
            # A dictionary prevents discovery or invocation of a host OCR binary.
            "ocr_backend": {"name": "disabled"},
        }
        if include_adb_path:
            report["adb_path"] = str(self.fake_adb.resolve())
        if test_fixture:
            report["_test_fixture"] = True
        path = self.temp / f"preflight-{self._report_number}.json"
        path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return path

    def begin_run(self, report: Path) -> Path:
        output_root = self.temp / f"runs-{len(self._run_dirs) + 1}"
        payload = self.assert_success(
            self.run_cli(
                "begin",
                "--output-root",
                output_root,
                "--preflight-json",
                report,
            )
        )
        run_dir = Path(str(payload["run_dir"])).resolve()
        self.assertTrue(run_dir.is_relative_to(output_root.resolve()))
        self._run_dirs.append(run_dir)
        return run_dir

    def act(
        self,
        run_dir: Path,
        intent: str,
        *,
        target: str | None = None,
        bounds: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments: list[object] = [
            "act",
            "--run-dir",
            run_dir,
            "--intent",
            intent,
            "--screen-hash",
            SCREEN_HASH,
            "--adb-path",
            self.fake_adb,
        ]
        if target is not None:
            arguments.extend(["--target", target])
        if bounds is not None:
            arguments.extend(["--bounds", bounds])
        return self.run_cli(*arguments, env=env)

    def write_replay_source(self, run_id: str) -> Path:
        source = self.temp / ("source-" + hashlib.sha256(run_id.encode()).hexdigest()[:8])
        frames = source / "frames"
        frames.mkdir(parents=True)
        (source / "run-input.json").write_text(
            json.dumps(
                {
                    "contract_version": CONTRACT_VERSION,
                    "run_id": run_id,
                    "list_contexts": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (source / "observations-input.ndjson").write_text("", encoding="utf-8")
        (source / "structure-input.json").write_text(
            json.dumps({"contract_version": CONTRACT_VERSION, "run_id": run_id})
            + "\n",
            encoding="utf-8",
        )
        return source

    def test_begin_requires_bound_adb_and_allowlisted_tiktok_package(self) -> None:
        missing_adb = self.write_preflight(include_adb_path=False)
        missing_result = self.run_cli(
            "begin",
            "--output-root",
            self.temp / "missing-adb-runs",
            "--preflight-json",
            missing_adb,
        )
        self.assert_error(missing_result, "preflight_incomplete")

        unsafe_package = self.write_preflight(package="com.example.not.tiktok")
        package_result = self.run_cli(
            "begin",
            "--output-root",
            self.temp / "unsafe-package-runs",
            "--preflight-json",
            unsafe_package,
        )
        self.assert_error(package_result, "invalid_tiktok_package")
        self.assertEqual(self.input_calls(), [])

    def test_generic_tap_requires_matching_fresh_anchor(self) -> None:
        run_dir = self.begin_run(self.write_preflight())
        before = len(self.input_calls())

        mismatched = self.act(
            run_dir,
            "select_tab",
            target="Content gap",
            bounds="700,300,300,80",
        )
        self.assert_error(mismatched, "action_denied")
        self.assertEqual(len(self.input_calls()), before)

        matched = self.act(
            run_dir,
            "select_tab",
            target="Content gap",
            bounds=CONTENT_GAP_BOUNDS,
        )
        self.assert_success(matched)
        self.assertEqual(len(self.input_calls()), before + 1)

    def test_capture_rejects_unverified_and_fresh_non_csi_state(self) -> None:
        unverified = self.begin_run(
            self.write_preflight(state="UNVERIFIED", at_top=True)
        )
        unverified_result = self.run_cli(
            "capture-tab",
            "--run-dir",
            unverified,
            "--mode",
            "Content gap",
            "--max-scrolls",
            "0",
            "--adb-path",
            self.fake_adb,
        )
        self.assert_error(unverified_result, "wrong_state")

        csi_run = self.begin_run(self.write_preflight(at_top=True))
        changed_result = self.run_cli(
            "capture-tab",
            "--run-dir",
            csi_run,
            "--mode",
            "Content gap",
            "--max-scrolls",
            "1",
            "--adb-path",
            self.fake_adb,
            env={"CSI_FAKE_SCREEN": "profile"},
        )
        self.assert_error(changed_result, "list_state_lost")
        self.assertEqual(self.input_calls(), [])

    def test_replay_rejects_traversal_run_id_and_symlink_frames(self) -> None:
        traversal = self.write_replay_source("../escaped-run")
        traversal_output = self.temp / "traversal-output"
        traversal_result = self.run_cli(
            "replay",
            "--source",
            traversal,
            "--output-root",
            traversal_output,
        )
        self.assert_error(traversal_result, "invalid_run_id")
        self.assertFalse((self.temp / "escaped-run").exists())

        symlink_source = self.write_replay_source("safe-symlink-run")
        outside = self.temp / "private-outside-frame.png"
        outside.write_bytes(PNG_BYTES)
        link = symlink_source / "frames" / "frame-000001.png"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"symlinks are unavailable on this host: {error}")
        symlink_output = self.temp / "symlink-output"
        symlink_result = self.run_cli(
            "replay",
            "--source",
            symlink_source,
            "--output-root",
            symlink_output,
        )
        self.assert_error(symlink_result, "invalid_evidence_file")
        self.assertEqual(list(symlink_output.rglob("*.png")), [])

    def test_finalized_live_run_is_immutable(self) -> None:
        run_dir = self.begin_run(self.write_preflight(at_top=True))
        self.assert_success(
            self.run_cli(
                "capture-tab",
                "--run-dir",
                run_dir,
                "--mode",
                "Content gap",
                "--max-scrolls",
                "0",
                "--adb-path",
                self.fake_adb,
            )
        )
        self.assert_success(self.run_cli("extract", "--run-dir", run_dir))
        self.assert_success(self.run_cli("finalize", "--run-dir", run_dir))

        canonical = (
            "observations.ndjson",
            "terms.csv",
            "conflicts.ndjson",
            "run.json",
            "structure.json",
            "evidence-manifest.json",
            "evidence.zip",
        )
        before = {name: (run_dir / name).read_bytes() for name in canonical}

        self.assert_error(
            self.run_cli("extract", "--run-dir", run_dir), "run_finalized"
        )
        self.assert_error(
            self.run_cli("finalize", "--run-dir", run_dir), "run_finalized"
        )
        after = {name: (run_dir / name).read_bytes() for name in canonical}
        self.assertEqual(after, before)

    def test_test_clock_requires_explicit_fixture_marker(self) -> None:
        unmarked = self.begin_run(self.write_preflight(test_fixture=False))
        first_unmarked = self.act(
            unmarked,
            "back",
            env={"CSI_TEST_MODE": "1", "CSI_TEST_NOW": "1000.0"},
        )
        self.assert_success(first_unmarked)
        second_unmarked = self.act(
            unmarked,
            "back",
            env={"CSI_TEST_MODE": "1", "CSI_TEST_NOW": "1004.0"},
        )
        self.assert_error(second_unmarked, "pacing_violation")

        marked = self.begin_run(self.write_preflight(test_fixture=True))
        first_marked = self.act(
            marked,
            "back",
            env={"CSI_TEST_MODE": "1", "CSI_TEST_NOW": "1000.0"},
        )
        self.assert_success(first_marked)
        second_marked = self.act(
            marked,
            "back",
            env={"CSI_TEST_MODE": "1", "CSI_TEST_NOW": "1004.0"},
        )
        self.assert_success(second_marked)

    def test_concurrent_same_run_operation_fails_closed(self) -> None:
        if os.name == "nt":
            self.skipTest("the POSIX contention path is exercised on this host")
        run_dir = self.begin_run(self.write_preflight())
        marker = self.temp / "first-inspect-started"
        first_env = self.base_env.copy()
        first_env.update(
            {
                "CSI_FAKE_ADB_STARTED": str(marker),
                "CSI_FAKE_ADB_DELAY_SECONDS": "1.0",
            }
        )
        first = subprocess.Popen(
            [sys.executable, str(CLI), "inspect", "--run-dir", str(run_dir)],
            cwd=SKILL_ROOT,
            env=first_env,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5.0
            while not marker.exists() and first.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "the first inspect never entered fake ADB")

            contended = self.run_cli("inspect", "--run-dir", run_dir)
            self.assert_error(contended, "run_busy")

            stdout, stderr = first.communicate(timeout=10)
            self.assertEqual(
                first.returncode,
                0,
                msg=f"first inspect failed: stdout={stdout!r}; stderr={stderr!r}",
            )
            self.assertEqual(json.loads(stdout).get("status"), "ok")
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
