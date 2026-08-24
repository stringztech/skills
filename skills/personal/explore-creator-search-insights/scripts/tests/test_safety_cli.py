"""Process-level safety contract tests for the Creator Search Insights CLI.

These tests deliberately do not import production modules. Every assertion crosses the
public ``scripts/csi.py`` process seam, and every ADB interaction targets a temporary
fake executable created by the test. A real device or real ``adb`` binary is never used.
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
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[2]
CLI = SKILL_ROOT / "scripts" / "csi.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "safety"

FAKE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
FAKE_PNG = base64.b64decode(FAKE_PNG_B64)
FAKE_SCREEN_HASH = hashlib.sha256(FAKE_PNG).hexdigest()


class SafetyCliContractTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.temp = Path(self._temporary_directory.name)
        self.adb_log = self.temp / "fake-adb.jsonl"
        self.fake_adb = self.temp / "fake-adb"
        self._request_number = 0
        self._write_fake_adb()

        self.base_env = os.environ.copy()
        self.base_env.update(
            {
                "CSI_FAKE_ADB_LOG": str(self.adb_log),
                "CSI_FAKE_ADB_SCENARIO": "single",
            }
        )

    def _write_fake_adb(self) -> None:
        source = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import base64
            import json
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            log_path = Path(os.environ["CSI_FAKE_ADB_LOG"])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(args) + "\\n")

            scenario = os.environ.get("CSI_FAKE_ADB_SCENARIO", "single")

            if args == ["devices", "-l"]:
                print("List of devices attached")
                if scenario in {"single", "locked"}:
                    print("SAFE123\\tdevice product:test model:Fixture transport_id:1")
                elif scenario == "multiple":
                    print("SAFE123\\tdevice product:test model:Fixture transport_id:1")
                    print("SAFE999\\tdevice product:test model:Second transport_id:2")
                elif scenario == "unauthorized":
                    print("SAFE123\\tunauthorized usb:1-1 transport_id:1")
                elif scenario == "offline":
                    print("SAFE123\\toffline usb:1-1 transport_id:1")
                elif scenario == "none":
                    pass
                else:
                    print(f"unknown fake scenario: {scenario}", file=sys.stderr)
                    raise SystemExit(64)
                raise SystemExit(0)

            if len(args) < 3 or args[0] != "-s":
                print(f"unsupported unscoped fake adb call: {args}", file=sys.stderr)
                raise SystemExit(65)

            serial = args[1]
            command = args[2:]
            if serial not in {"SAFE123", "SAFE999"}:
                print("unknown serial", file=sys.stderr)
                raise SystemExit(66)
            if scenario == "unauthorized":
                print("error: device unauthorized", file=sys.stderr)
                raise SystemExit(1)
            if scenario == "offline":
                print("error: device offline", file=sys.stderr)
                raise SystemExit(1)

            if command == ["get-state"]:
                print("device")
                raise SystemExit(0)
            if command == ["shell", "wm", "size"]:
                print("Physical size: 1080x2410")
                raise SystemExit(0)
            if command == ["shell", "wm", "density"]:
                print("Physical density: 420")
                raise SystemExit(0)
            if command == ["shell", "getprop", "ro.build.version.release"]:
                print("15")
                raise SystemExit(0)
            if command == ["shell", "getprop", "persist.sys.locale"]:
                print("en-US")
                raise SystemExit(0)
            if command == ["shell", "dumpsys", "window", "policy"]:
                locked = scenario == "locked"
                print("mShowingLockscreen=" + ("true" if locked else "false"))
                raise SystemExit(0)
            if command == ["shell", "ls", "/system/bin/input"]:
                print("/system/bin/input")
                raise SystemExit(0)
            if command == [
                "shell",
                "dumpsys",
                "package",
                "com.zhiliaoapp.musically",
            ]:
                print("Package [com.zhiliaoapp.musically]")
                print("versionCode=410010 minSdk=23 targetSdk=35")
                print("versionName=41.1.0")
                raise SystemExit(0)
            if "screencap" in command:
                sys.stdout.buffer.write(base64.b64decode("__PNG__"))
                raise SystemExit(0)
            if "uiautomator" in command:
                print(
                    '<hierarchy rotation="0">'
                    '<node package="com.zhiliaoapp.musically" '
                    'text="Creator Search Insights" content-desc=""/>'
                    '<node package="com.zhiliaoapp.musically" '
                    'text="shrimp aquascape" content-desc="" '
                    'bounds="[36,700][556,780]"/>'
                    '</hierarchy>'
                )
                raise SystemExit(0)
            if command[:3] == ["shell", "am", "start"]:
                print("Starting: Intent { act=android.intent.action.MAIN }")
                raise SystemExit(0)
            if command[:2] == ["shell", "input"]:
                raise SystemExit(0)

            print(f"unsupported fake adb call: {args}", file=sys.stderr)
            raise SystemExit(67)
            """
        ).replace("__PNG__", FAKE_PNG_B64)
        self.fake_adb.write_text(source, encoding="utf-8")
        self.fake_adb.chmod(
            self.fake_adb.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    def run_cli(
        self,
        *arguments: object,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process_env = self.base_env.copy()
        if env:
            process_env.update(env)
        return subprocess.run(
            [sys.executable, str(CLI), *(str(argument) for argument in arguments)],
            cwd=SKILL_ROOT,
            env=process_env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def json_stdout(
        self, result: subprocess.CompletedProcess[str]
    ) -> dict[str, object]:
        self.assertTrue(
            result.stdout.strip(),
            msg=f"CLI emitted no JSON. stderr={result.stderr!r}",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                f"CLI stdout was not one JSON object: {error}; "
                f"stdout={result.stdout!r}; stderr={result.stderr!r}"
            )
        self.assertIsInstance(payload, dict)
        return payload

    def write_request(self, payload: dict[str, object]) -> Path:
        self._request_number += 1
        path = self.temp / f"request-{self._request_number}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def policy(self, request: dict[str, object]) -> dict[str, object]:
        result = self.run_cli(
            "policy", "--request-json", self.write_request(request)
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"policy failed: stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        payload = self.json_stdout(result)
        self.assertEqual(
            set(payload), {"allowed", "reason", "normalized_action"}
        )
        self.assertIsInstance(payload["reason"], str)
        self.assertTrue(payload["reason"])
        return payload

    def adb_calls(self) -> list[list[str]]:
        if not self.adb_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.adb_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def input_calls(self) -> list[list[str]]:
        risky: list[list[str]] = []
        for argv in self.adb_calls():
            command = argv[2:] if len(argv) >= 3 and argv[0] == "-s" else argv
            if command[:2] == ["shell", "input"]:
                risky.append(argv)
            elif command[:3] == ["shell", "am", "start"]:
                risky.append(argv)
        return risky

    def begin_run(self) -> tuple[Path, dict[str, object]]:
        output_root = self.temp / f"runs-{self._request_number}"
        report = json.loads(
            (FIXTURES / "preflight_ready_csi_list.json").read_text(encoding="utf-8")
        )
        report["adb_path"] = str(self.fake_adb.resolve())
        report["_test_fixture"] = True
        bound_preflight = self.temp / "preflight-ready-bound.json"
        bound_preflight.write_text(json.dumps(report), encoding="utf-8")
        result = self.run_cli(
            "begin",
            "--output-root",
            output_root,
            "--preflight-json",
            bound_preflight,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"begin failed: stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        payload = self.json_stdout(result)
        self.assertIn("capability_report", payload)
        self.assertIn("run_dir", payload)
        run_dir = Path(str(payload["run_dir"]))
        if not run_dir.is_absolute():
            run_dir = output_root / run_dir
        run_dir = run_dir.resolve()
        self.assertTrue(run_dir.is_dir())
        self.assertTrue(run_dir.is_relative_to(output_root.resolve()))
        return run_dir, payload

    def act(
        self,
        run_dir: Path,
        intent: str,
        *,
        screen_hash: str = FAKE_SCREEN_HASH,
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
            screen_hash,
            "--adb-path",
            self.fake_adb,
        ]
        if target is not None:
            arguments.extend(["--target", target])
        if bounds is not None:
            arguments.extend(["--bounds", bounds])
        return self.run_cli(*arguments, env=env)

    def assert_denied(self, payload: dict[str, object]) -> None:
        self.assertIs(payload["allowed"], False)
        self.assertIsNone(payload["normalized_action"])

    def test_command_allowlist_accepts_only_exact_read_only_templates(self) -> None:
        cases = [
            (["devices", "-l"], "enumerate_devices"),
            (["-s", "SAFE123", "get-state"], "get_device_state"),
            (
                ["-s", "SAFE123", "exec-out", "screencap", "-p"],
                "capture_screen",
            ),
            (
                [
                    "-s",
                    "SAFE123",
                    "shell",
                    "uiautomator",
                    "dump",
                    "/dev/tty",
                ],
                "dump_ui_stdout",
            ),
            (["-s", "SAFE123", "shell", "wm", "size"], "query_screen_size"),
            (["-s", "SAFE123", "shell", "wm", "density"], "query_screen_density"),
            (
                ["-s", "SAFE123", "shell", "getprop", "ro.build.version.release"],
                "query_android_version",
            ),
            (
                ["-s", "SAFE123", "shell", "getprop", "persist.sys.locale"],
                "query_device_locale",
            ),
            (
                ["-s", "SAFE123", "shell", "dumpsys", "window", "policy"],
                "query_lock_state",
            ),
            (
                ["-s", "SAFE123", "shell", "dumpsys", "window", "windows"],
                "query_foreground_window",
            ),
            (
                ["-s", "SAFE123", "shell", "ls", "/system/bin/input"],
                "verify_input_binary",
            ),
            (
                [
                    "-s",
                    "SAFE123",
                    "shell",
                    "dumpsys",
                    "package",
                    "com.zhiliaoapp.musically",
                ],
                "query_tiktok_package",
            ),
        ]

        for argv, normalized_action in cases:
            with self.subTest(argv=argv):
                payload = self.policy(
                    {"kind": "adb_command", "state": "PRECHECK", "argv": argv}
                )
                self.assertIs(payload["allowed"], True)
                self.assertEqual(payload["normalized_action"], normalized_action)

    def test_command_allowlist_rejects_mutation_and_shell_escape(self) -> None:
        commands = [
            ["root"],
            ["install", "payload.apk"],
            ["push", "local", "/sdcard/remote"],
            ["pull", "/sdcard/private", "local"],
            ["-s", "SAFE123", "shell", "pm", "clear", "com.zhiliaoapp.musically"],
            ["-s", "SAFE123", "shell", "settings", "put", "secure", "x", "1"],
            ["-s", "SAFE123", "shell", "content", "insert", "--uri", "x"],
            ["-s", "SAFE123", "shell", "sh", "-c", "input tap 1 1"],
            ["-s", "SAFE123", "shell", "wm", "size;", "rm", "-rf", "/"],
        ]

        for argv in commands:
            with self.subTest(argv=argv):
                payload = self.policy(
                    {"kind": "adb_command", "state": "PRECHECK", "argv": argv}
                )
                self.assert_denied(payload)

    def test_state_policy_is_fail_closed(self) -> None:
        allowed = [
            {"kind": "intent", "state": "READY", "intent": "open_tiktok"},
            {"kind": "intent", "state": "CSI_LIST", "intent": "scroll_list"},
            {"kind": "intent", "state": "CSI_LIST", "intent": "back"},
            {
                "kind": "intent",
                "state": "CSI_LIST",
                "intent": "tap_term_detail",
                "target": "shrimp aquascape",
                "bounds": [36, 700, 520, 80],
                "screen_size": [1080, 2410],
                "available_text_boxes": [
                    {"text": "shrimp aquascape", "bbox": [36, 700, 520, 80]}
                ],
            },
        ]
        denied = [
            {"kind": "intent", "state": "READY", "intent": "scroll_list"},
            {
                "kind": "intent",
                "state": "PROFILE",
                "intent": "tap_filter",
                "target": "Content gap",
            },
            {"kind": "intent", "state": "CSI_LIST", "intent": "open_tiktok"},
            {"kind": "intent", "state": "UNKNOWN", "intent": "back"},
        ]

        for request in allowed:
            with self.subTest(allowed=request):
                payload = self.policy(request)
                self.assertIs(payload["allowed"], True)
                self.assertIsNotNone(payload["normalized_action"])
        for request in denied:
            with self.subTest(denied=request):
                self.assert_denied(self.policy(request))

    def test_only_exact_fallback_search_text_is_allowed(self) -> None:
        allowed = self.policy(
            {
                "kind": "intent",
                "state": "TIKTOK_SEARCH",
                "intent": "type_search_query",
                "text": "creator search insights",
                "focused_control": "Search",
            }
        )
        self.assertIs(allowed["allowed"], True)

        for text in (
            "creator search insights; input keyevent 66",
            "follow me",
            "creator search insights\\npost",
        ):
            with self.subTest(text=text):
                denied = self.policy(
                    {
                        "kind": "intent",
                        "state": "TIKTOK_SEARCH",
                        "intent": "type_search_query",
                        "text": text,
                        "focused_control": "Search",
                    }
                )
                self.assert_denied(denied)

    def test_stale_screen_hash_blocks_action_before_adb_input(self) -> None:
        run_dir, _ = self.begin_run()
        result = self.act(
            run_dir,
            "scroll_list",
            screen_hash="0" * 64,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = self.json_stdout(result)
        self.assertIn("stale", json.dumps(payload).lower())
        self.assertEqual(self.input_calls(), [])

    def test_term_tap_accepts_left_text_bounds_and_logs_action(self) -> None:
        run_dir, _ = self.begin_run()
        result = self.act(
            run_dir,
            "tap_term_detail",
            target="shrimp aquascape",
            bounds="36,700,520,80",
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        payload = self.json_stdout(result)
        self.assertIs(payload.get("ok"), True)
        self.assertEqual(len(self.input_calls()), 1)
        self.assertIn("tap", self.input_calls()[0])
        action_logs = [
            *run_dir.rglob("*.jsonl"),
            *run_dir.rglob("*.ndjson"),
        ]
        self.assertTrue(action_logs, "an allowed act must update an action log")
        self.assertTrue(
            any(
                "tap_term_detail" in path.read_text(encoding="utf-8")
                for path in action_logs
            )
        )

    def test_term_tap_rejects_right_edge_and_out_of_bounds_geometry(self) -> None:
        unsafe_bounds = (
            "720,700,300,80",
            "-5,700,300,80",
            "1000,700,200,80",
        )
        run_dir, _ = self.begin_run()

        for bounds in unsafe_bounds:
            with self.subTest(bounds=bounds):
                calls_before = len(self.input_calls())
                result = self.act(
                    run_dir,
                    "tap_term_detail",
                    target="shrimp aquascape",
                    bounds=bounds,
                )
                self.assertNotEqual(result.returncode, 0)
                self.json_stdout(result)
                self.assertEqual(len(self.input_calls()), calls_before)

    def test_detect_stop_recognizes_all_fail_closed_screens(self) -> None:
        cases = json.loads(
            (FIXTURES / "stop_cases.json").read_text(encoding="utf-8")
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                text_path = self.temp / f"stop-{case['name'].replace(' ', '-')}.txt"
                text_path.write_text(case["text"], encoding="utf-8")
                result = self.run_cli("detect-stop", "--text-file", text_path)
                self.assertEqual(result.returncode, 0)
                payload = self.json_stdout(result)
                self.assertIs(payload.get("stop"), True)
                self.assertIn(
                    case["reason_contains"], str(payload.get("reason", "")).lower()
                )

        safe_result = self.run_cli(
            "detect-stop", "--text-file", FIXTURES / "safe_csi.txt"
        )
        self.assertEqual(safe_result.returncode, 0)
        safe_payload = self.json_stdout(safe_result)
        self.assertIs(safe_payload.get("stop"), False)

    def test_prompt_injection_is_data_not_an_instruction(self) -> None:
        detection = self.run_cli(
            "detect-stop", "--text-file", FIXTURES / "prompt_injection.txt"
        )
        self.assertEqual(detection.returncode, 0)
        self.assertIs(self.json_stdout(detection).get("stop"), False)

        injection = (FIXTURES / "prompt_injection.txt").read_text(
            encoding="utf-8"
        ).strip()
        request = {
            "kind": "intent",
            "state": "CSI_LIST",
            "intent": "tap_nav",
            "target": injection,
            "bounds": [36, 700, 520, 80],
            "screen_size": [1080, 2410],
        }
        self.assert_denied(self.policy(request))
        self.assertEqual(self.adb_calls(), [])

    def test_preflight_reports_before_any_input_and_begin_is_required(self) -> None:
        uninitialized = self.temp / "not-a-run"
        uninitialized.mkdir()
        direct_act = self.act(uninitialized, "open_tiktok")
        self.assertNotEqual(direct_act.returncode, 0)
        self.json_stdout(direct_act)
        self.assertEqual(self.input_calls(), [])

        preflight = self.run_cli(
            "preflight", "--adb-path", self.fake_adb, "--json"
        )
        self.assertEqual(
            preflight.returncode,
            0,
            msg=f"stdout={preflight.stdout!r} stderr={preflight.stderr!r}",
        )
        report = self.json_stdout(preflight)
        self.assertIs(report.get("ready"), True)
        capabilities = report.get("capabilities")
        self.assertIsInstance(capabilities, dict)
        self.assertIs(capabilities.get("device_unlocked"), True)
        self.assertIs(capabilities.get("input_binary"), True)
        self.assertEqual(report.get("app", {}).get("version_name"), "41.1.0")
        self.assertEqual(report.get("app", {}).get("version_code"), "410010")
        self.assertEqual(self.input_calls(), [])

        preflight_json = self.temp / "preflight-from-process.json"
        preflight_json.write_text(json.dumps(report), encoding="utf-8")
        output_root = self.temp / "reported-runs"
        begin = self.run_cli(
            "begin",
            "--output-root",
            output_root,
            "--preflight-json",
            preflight_json,
        )
        self.assertEqual(begin.returncode, 0)
        begun = self.json_stdout(begin)
        self.assertEqual(begun.get("capability_report"), report)
        self.assertEqual(self.input_calls(), [])

    def test_pacing_uses_fake_timestamps_and_never_bursts(self) -> None:
        run_dir, _ = self.begin_run()
        test_mode = {"CSI_TEST_MODE": "1", "CSI_TEST_NOW": "1000.0"}
        first = self.act(run_dir, "scroll_list", env=test_mode)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(len(self.input_calls()), 1)

        too_soon = self.act(
            run_dir,
            "scroll_list",
            env={"CSI_TEST_MODE": "1", "CSI_TEST_NOW": "1001.0"},
        )
        self.assertNotEqual(too_soon.returncode, 0)
        too_soon_payload = self.json_stdout(too_soon)
        self.assertRegex(
            json.dumps(too_soon_payload).lower(), r"pace|wait|too soon|interval"
        )
        self.assertEqual(len(self.input_calls()), 1)

        paced = self.act(
            run_dir,
            "scroll_list",
            env={"CSI_TEST_MODE": "1", "CSI_TEST_NOW": "1003.0"},
        )
        self.assertEqual(paced.returncode, 0)
        self.assertEqual(len(self.input_calls()), 2)

    def test_multiple_devices_without_serial_stop_before_input(self) -> None:
        result = self.run_cli(
            "preflight",
            "--adb-path",
            self.fake_adb,
            "--json",
            env={"CSI_FAKE_ADB_SCENARIO": "multiple"},
        )
        self.assertNotEqual(result.returncode, 0)
        payload = self.json_stdout(result)
        self.assertIs(payload.get("ready"), False)
        self.assertIn("multiple", json.dumps(payload).lower())
        self.assertEqual(self.input_calls(), [])

    def test_unauthorized_device_stops_before_input(self) -> None:
        result = self.run_cli(
            "preflight",
            "--adb-path",
            self.fake_adb,
            "--json",
            env={"CSI_FAKE_ADB_SCENARIO": "unauthorized"},
        )
        self.assertNotEqual(result.returncode, 0)
        payload = self.json_stdout(result)
        self.assertIs(payload.get("ready"), False)
        self.assertIn("unauthorized", json.dumps(payload).lower())
        self.assertEqual(self.input_calls(), [])

    def test_locked_device_stops_before_input(self) -> None:
        result = self.run_cli(
            "preflight",
            "--adb-path",
            self.fake_adb,
            "--json",
            env={"CSI_FAKE_ADB_SCENARIO": "locked"},
        )
        self.assertNotEqual(result.returncode, 0)
        payload = self.json_stdout(result)
        self.assertIs(payload.get("ready"), False)
        self.assertIs(payload.get("screen", {}).get("unlocked"), False)
        self.assertIs(payload.get("capabilities", {}).get("device_unlocked"), False)
        self.assertEqual(self.input_calls(), [])

    def test_explicit_serial_scopes_every_device_command(self) -> None:
        result = self.run_cli(
            "preflight",
            "--adb-path",
            self.fake_adb,
            "--serial",
            "SAFE123",
            "--json",
            env={"CSI_FAKE_ADB_SCENARIO": "multiple"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertIs(self.json_stdout(result).get("ready"), True)
        calls = self.adb_calls()
        self.assertTrue(calls)
        for call in calls:
            if call == ["devices", "-l"]:
                continue
            self.assertGreaterEqual(len(call), 3)
            self.assertEqual(call[:2], ["-s", "SAFE123"])
        self.assertEqual(self.input_calls(), [])

    def test_prohibited_actions_are_denied_by_policy_and_runtime(self) -> None:
        prohibited = (
            "like",
            "follow",
            "unfollow",
            "comment",
            "share",
            "send",
            "repost",
            "favorite",
            "bookmark",
            "save",
            "message",
            "live",
            "duet",
            "stitch",
            "record",
            "upload",
            "create",
            "post",
            "delete",
            "edit_profile",
            "settings",
            "payment",
            "download",
            "clipboard",
            "login",
            "enter_otp",
            "allow_permission",
            "accept_update",
            "open_video",
            "tap_camera",
            "keyevent_enter",
            "raw_adb",
        )
        for intent in prohibited:
            with self.subTest(intent=intent):
                payload = self.policy(
                    {
                        "kind": "intent",
                        "state": "CSI_LIST",
                        "intent": intent,
                        "target": intent,
                    }
                )
                self.assert_denied(payload)

        run_dir, _ = self.begin_run()
        calls_before = len(self.input_calls())
        result = self.act(
            run_dir,
            "like",
            target="Like",
            bounds="760,700,260,80",
        )
        self.assertNotEqual(result.returncode, 0)
        self.json_stdout(result)
        self.assertEqual(len(self.input_calls()), calls_before)


if __name__ == "__main__":
    unittest.main()
