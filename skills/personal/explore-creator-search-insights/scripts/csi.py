#!/usr/bin/env python3
"""Safe Creator Search Insights capture and offline artifact CLI."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET


CONTRACT_VERSION = "csi.android.capture/v1"
DEFAULT_PACKAGE = "com.zhiliaoapp.musically"
ALLOWED_PACKAGES = {DEFAULT_PACKAGE, "com.ss.android.ugc.trill"}
MIN_INPUT_INTERVAL_SECONDS = 1.5
MIN_SCROLL_INTERVAL_SECONDS = 2.5
MIN_TRANSITION_INTERVAL_SECONDS = 3.0
MAX_XML_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024
AUTHORIZED_DATA_STATES = {
    "CSI_HOME",
    "CSI_LIST",
    "FILTER_SHEET",
    "TERM_DETAIL",
    "SCOPE_SHEET",
    "RECENCY_SHEET",
}
SAFE_SERIAL = re.compile(r"^[A-Za-z0-9._:-]+$")
BOUNDS_PATTERN = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
METRIC_PATTERN = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?:[KMB])?(?!\w)", re.IGNORECASE)
PERCENTAGE_PATTERN = re.compile(r"(?:(?:▲|△|↑)\s*)?(\d+(?:[.,]\d+)?%\+?)")
PROHIBITED_INTENTS = {
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
}
SAFE_ANCHOR_LABELS = {
    "Menu",
    "More",
    "Filters",
    "Filter",
    "Profile",
    "Creator Search Insights",
    "TikTok Studio",
    "Creator tools",
    "Creator Tools",
    "All",
    "Content gap",
    "Searches by followers",
    "Suggested",
    "Trending",
    "Dance",
    "Featured",
    "Food",
    "Travel",
    "Fashion",
    "Sports",
    "Hobbies",
    "Science & Tech",
    "Home & Living",
    "Education",
    "Careers",
    "Vehicles",
    "Local life",
    "Photo posts",
    "Gaming",
    "Tourism",
    "Science",
    "Reset",
    "Apply",
    "English",
    "العربية",
    "Deutsch",
    "Español",
    "Français",
    "Bahasa Indonesia",
    "日本語",
    "한국어",
    "Português",
    "ภาษาไทย",
    "Türkçe",
    "Tiếng Việt",
    "High % Gap",
    "Search",
    "Confirm",
    "Global (all)",
    "Ethiopia",
    "Malaysia",
    "Indonesia",
    "United States of America",
    "Singapore",
    "Thailand",
    "Last 7 days",
    "Last 30 days",
    "Last 60 days",
    "Last 6 months",
    "Custom",
    "No more searches",
}
TAP_INTENTS = {
    "tap_profile",
    "tap_search",
    "open_profile_menu",
    "tap_creator_search_insights",
    "tap_tiktok_studio",
    "submit_search",
    "select_tab",
    "open_category",
    "open_filter",
    "select_filter",
    "apply_filter",
    "open_scope",
    "select_scope",
    "confirm_scope",
    "open_recency",
    "select_recency",
}
CONTROL_TEXT = {
    "Creator Search Insights",
    "All",
    "Content gap",
    "Searches by followers",
    "Suggested",
    "Trending",
    "Dance",
    "Featured",
    "Food",
    "Travel",
    "Fashion",
    "Sports",
    "Hobbies",
    "Science & Tech",
    "Home & Living",
    "Education",
    "Careers",
    "Vehicles",
    "Local life",
    "Photo posts",
    "Reset",
    "Apply",
    "Save preferences",
    "No more searches",
}
STRUCTURAL_LABELS = SAFE_ANCHOR_LABELS | {
    "Creation & business tools",
    "Save preferences",
    "Search popularity",
    "Related videos",
    "Viewer insights",
    "What viewers are searching",
    "Explore more topics",
    "Select country or region",
    "Select date range",
    "AI outline",
    "AI canvas",
    "Analytics",
    "Saved",
    "All tools",
}
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


class CliError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        emit(
            {
                "status": "error",
                "error": {"code": "invalid_arguments", "message": message},
            }
        )
        raise SystemExit(2)


def emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def fail(error):
    payload = {"status": "error", "error": {"code": error.code, "message": error.message}}
    payload["error"].update(error.details)
    emit(payload)
    return 2


def secure_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def secure_file(path):
    try:
        path.chmod(0o600)
    except OSError:
        pass


def atomic_write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        secure_file(path)
    except Exception:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path, text_value):
    atomic_write_bytes(path, text_value.encode("utf-8"))


def write_json(path, value):
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def write_ndjson(path, records):
    atomic_write_text(
        path,
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
    )


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliError("invalid_json", "Unable to read JSON", path=str(path), detail=str(error))
    if not isinstance(value, dict):
        raise CliError("invalid_json", "Expected a JSON object", path=str(path))
    return value


def read_ndjson(path):
    records = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("record is not an object")
                records.append(value)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise CliError(
            "invalid_ndjson",
            "Unable to read NDJSON",
            path=str(path),
            detail=str(error),
        )
    return records


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(path):
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
        if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
            return struct.unpack(">II", header[16:24])
    except OSError:
        pass
    return None, None


def json_cell(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def term_match_key(term):
    normalized = unicodedata.normalize("NFC", term)
    return " ".join(normalized.split())


def spreadsheet_safe(term):
    stripped = term.lstrip()
    dangerous = bool(stripped) and stripped[0] in "=+-@"
    return (("'" + term) if dangerous else term), dangerous


def validate_contract_version(value, path):
    if value.get("contract_version") != CONTRACT_VERSION:
        raise CliError(
            "contract_version_mismatch",
            "Unsupported contract version",
            path=str(path),
            expected=CONTRACT_VERSION,
            actual=value.get("contract_version"),
        )


def validate_run_id(value):
    run_id = str(value or "")
    if (
        not run_id
        or len(run_id) > 128
        or run_id in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id)
    ):
        raise CliError("invalid_run_id", "run_id must be a safe relative identifier")
    return run_id


def utc_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def test_mode_enabled(control):
    return control.get("test_mode_allowed") is True and os.environ.get("CSI_TEST_MODE") == "1"


def monotonic_wall_time(control):
    if test_mode_enabled(control):
        try:
            return float(os.environ.get("CSI_TEST_NOW", "0"))
        except ValueError as error:
            raise CliError("invalid_test_clock", "CSI_TEST_NOW must be numeric") from error
    return time.time()


def append_ndjson(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    secure_file(path)


@contextmanager
def operation_lock(run_dir):
    """Hold one cross-platform, process-scoped lock for a run mutation."""
    lock_path = run_dir / ".operation.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    handle = lock_path.open("r+b")
    try:
        if os.name == "nt":
            import msvcrt

            if lock_path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise CliError("run_busy", "Another controller operation owns this run") from error
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise CliError("run_busy", "Another controller operation owns this run") from error
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()
        if (run_dir / ".finalized.json").is_file():
            try:
                lock_path.unlink()
            except OSError:
                pass


def parse_bounds(value):
    if value is None:
        return None
    if isinstance(value, str):
        pieces = value.split(",")
        if len(pieces) != 4:
            return None
        try:
            return [int(piece.strip()) for piece in pieces]
        except ValueError:
            return None
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [int(piece) for piece in value]
        except (TypeError, ValueError):
            return None
    return None


def geometry_is_safe(bounds, screen_size, *, left_text=False):
    if bounds is None or screen_size is None or len(screen_size) != 2:
        return False
    try:
        x, y, width, height = [int(item) for item in bounds]
        screen_width, screen_height = [int(item) for item in screen_size]
    except (TypeError, ValueError):
        return False
    if min(x, y) < 0 or min(width, height, screen_width, screen_height) <= 0:
        return False
    if x + width > screen_width or y + height > screen_height:
        return False
    if left_text and x + width > int(screen_width * 0.70):
        return False
    return True


def bounds_match_anchor(bounds, anchor_bounds, tolerance=12):
    candidate = parse_bounds(bounds)
    anchor = parse_bounds(anchor_bounds)
    if candidate is None or anchor is None:
        return False
    return all(abs(candidate[index] - anchor[index]) <= tolerance for index in range(4))


def normalize_adb_command(argv):
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        return None
    if argv == ["devices", "-l"]:
        return "enumerate_devices"
    if len(argv) < 3 or argv[0] != "-s" or not SAFE_SERIAL.fullmatch(argv[1]):
        return None
    command = argv[2:]
    exact = {
        ("get-state",): "get_device_state",
        ("exec-out", "screencap", "-p"): "capture_screen",
        ("shell", "uiautomator", "dump", "/dev/tty"): "dump_ui_stdout",
        ("shell", "wm", "size"): "query_screen_size",
        ("shell", "wm", "density"): "query_screen_density",
        ("shell", "getprop", "ro.build.version.release"): "query_android_version",
        ("shell", "getprop", "persist.sys.locale"): "query_device_locale",
        ("shell", "dumpsys", "window", "policy"): "query_lock_state",
        ("shell", "dumpsys", "window", "windows"): "query_foreground_window",
        ("shell", "ls", "/system/bin/input"): "verify_input_binary",
    }
    if len(command) == 4 and tuple(command[:3]) == ("shell", "dumpsys", "package"):
        return "query_tiktok_package" if command[3] in ALLOWED_PACKAGES else None
    return exact.get(tuple(command))


def intent_policy(request):
    state = str(request.get("state") or "")
    intent = str(request.get("intent") or "")
    if intent in PROHIBITED_INTENTS:
        return False, "The intent is explicitly prohibited", None
    if state not in {
        "READY",
        "TIKTOK_HOME",
        "PROFILE",
        "PROFILE_MENU",
        "CREATOR_TOOLS",
        "TIKTOK_SEARCH",
        "CSI_HOME",
        "CSI_LIST",
        "FILTER_SHEET",
        "TERM_DETAIL",
        "SCOPE_SHEET",
        "RECENCY_SHEET",
    }:
        return False, "The current screen state is unknown", None

    if intent == "type_search_query":
        if (
            state == "TIKTOK_SEARCH"
            and request.get("text") == "creator search insights"
            and request.get("focused_control") == "Search"
        ):
            return True, "Exact fallback query in a verified TikTok Search field", intent
        return False, "Only the exact fallback query in a verified Search field is allowed", None

    allowed_by_state = {
        "READY": {"open_tiktok"},
        "TIKTOK_HOME": {"tap_profile", "tap_search", "back"},
        "PROFILE": {"open_profile_menu", "back"},
        "PROFILE_MENU": {
            "tap_creator_search_insights",
            "tap_tiktok_studio",
            "back",
        },
        "CREATOR_TOOLS": {"tap_creator_search_insights", "back"},
        "TIKTOK_SEARCH": {"submit_search", "tap_creator_search_insights", "back"},
        "CSI_HOME": {"select_tab", "open_category", "open_filter", "back"},
        "CSI_LIST": {
            "select_tab",
            "open_category",
            "open_filter",
            "scroll_list",
            "tap_term_detail",
            "back",
        },
        "FILTER_SHEET": {"select_filter", "apply_filter", "back"},
        "TERM_DETAIL": {"scroll_detail", "open_scope", "open_recency", "back"},
        "SCOPE_SHEET": {"select_scope", "confirm_scope", "back"},
        "RECENCY_SHEET": {"select_recency", "back"},
    }
    if intent not in allowed_by_state.get(state, set()):
        return False, "The intent is not allowlisted for the current state", None
    if intent == "tap_term_detail":
        if not str(request.get("target") or "").strip():
            return False, "A term label is required", None
        if not geometry_is_safe(
            parse_bounds(request.get("bounds")),
            request.get("screen_size"),
            left_text=True,
        ):
            return False, "Term taps must stay inside verified left-side text bounds", None
        term_boxes = request.get("available_text_boxes")
        if not isinstance(term_boxes, list) or not any(
            isinstance(item, dict)
            and str(item.get("text") or "") == str(request.get("target") or "")
            and bounds_match_anchor(request.get("bounds"), item.get("bbox"))
            for item in term_boxes
        ):
            return False, "The term label and bounds were not present in the fresh screen reading", None
    if intent in TAP_INTENTS:
        bounds = parse_bounds(request.get("bounds"))
        if not geometry_is_safe(bounds, request.get("screen_size")):
            return False, "A verified in-screen anchor rectangle is required", None
        target = str(request.get("target") or "").strip()
        if not target or target == "Save preferences":
            return False, "The requested target is absent or prohibited", None
        anchors = request.get("available_anchors")
        if not isinstance(anchors, list):
            return False, "Fresh safe-anchor evidence is required for a tap", None
        matched = any(
            isinstance(anchor, dict)
            and str(anchor.get("label") or "") == target
            and bounds_match_anchor(bounds, anchor.get("bounds"))
            for anchor in anchors
        )
        if not matched:
            return False, "The target does not match a fresh allowlisted screen anchor", None
    return True, "The semantic intent is allowlisted for the verified state", intent


def evaluate_policy(request):
    kind = request.get("kind") if isinstance(request, dict) else None
    if kind == "adb_command":
        normalized = normalize_adb_command(request.get("argv"))
        if normalized:
            return {
                "allowed": True,
                "reason": "The command exactly matches a read-only ADB template",
                "normalized_action": normalized,
            }
        return {
            "allowed": False,
            "reason": "The ADB argv does not match an exact read-only template",
            "normalized_action": None,
        }
    if kind == "intent":
        allowed, reason, normalized = intent_policy(request)
        return {"allowed": allowed, "reason": reason, "normalized_action": normalized}
    return {
        "allowed": False,
        "reason": "Unknown policy request kind",
        "normalized_action": None,
    }


STOP_RULES = [
    ("login", re.compile(r"\b(?:log in|sign in)\b.*\b(?:tiktok|continue)\b", re.I)),
    ("captcha or verification challenge", re.compile(r"\b(?:captcha|drag the puzzle|puzzle piece|verify to continue)\b", re.I)),
    ("anti-automation or rate limit", re.compile(r"\b(?:unusual traffic|suspicious activity|too many attempts|rate limit|automated activity)\b", re.I)),
    ("permission prompt", re.compile(r"\ballow\s+tiktok\s+to\s+access\b", re.I)),
    ("incoming call overlay", re.compile(r"\b(?:incoming|ongoing)\s+(?:voice\s+|video\s+)?call\b", re.I)),
    ("notification overlay", re.compile(r"\bnotifications?\b.*\b(?:clear all|manage notifications)\b", re.I | re.S)),
    ("forced update prompt", re.compile(r"\bupdate\s+tiktok\s+to\s+continue\b", re.I)),
    ("consent prompt", re.compile(r"\b(?:accept|agree to)\s+(?:the\s+)?(?:terms|consent)\b", re.I)),
]


def detect_stop_text(text):
    for reason, pattern in STOP_RULES:
        if pattern.search(text or ""):
            return {"stop": True, "reason": reason}
    return {"stop": False, "reason": "no stop condition observed"}


def run_process(argv, *, binary=False, timeout=20, check=True):
    try:
        result = subprocess.run(
            [str(item) for item in argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CliError("process_failed", "Unable to execute a required local process", detail=str(error))
    if check and result.returncode != 0:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        reported_argv = [str(item) for item in argv[1:]]
        if "-s" in reported_argv:
            serial_index = reported_argv.index("-s") + 1
            if serial_index < len(reported_argv):
                reported_argv[serial_index] = "<redacted-serial>"
        raise CliError(
            "process_failed",
            "A required local process returned a failure",
            argv=reported_argv,
            returncode=result.returncode,
            detail=str(stderr).strip()[:1000],
        )
    return result


def resolve_adb_path(value):
    candidate = os.path.expanduser(str(value or ""))
    if not candidate:
        candidate = shutil.which("adb") or ""
    elif not os.path.isabs(candidate):
        candidate = shutil.which(candidate) or candidate
    path = Path(candidate).resolve() if candidate else None
    if path is None or not path.is_file():
        raise CliError("adb_missing", "ADB executable was not found", adb_path=str(value or ""))
    return str(path)


def run_adb(adb_path, argv, *, binary=False, check=True):
    return run_process([adb_path, *argv], binary=binary, check=check)


def parse_devices(output):
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("list of devices") or line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, state = fields[:2]
        metadata = {}
        for field in fields[2:]:
            if ":" in field:
                key, value = field.split(":", 1)
                metadata[key] = value
        devices.append({"serial": serial, "state": state, "metadata": metadata})
    return devices


def parse_screen_size(output):
    matches = re.findall(r"(\d+)x(\d+)", output or "")
    if not matches:
        raise CliError("screen_size_unavailable", "ADB did not return a screen size")
    width, height = matches[-1]
    return int(width), int(height)


def parse_unlocked_state(output):
    """Return a verified keyguard state, or None when the dump is inconclusive."""
    text_value = output or ""
    patterns = (
        r"\bisStatusBarKeyguard\s*[=:]\s*(true|false)",
        r"\bmShowingLockscreen\s*[=:]\s*(true|false)",
        r"\bmKeyguardShowing\s*[=:]\s*(true|false)",
        r"\bkeyguardShowing\s*[=:]\s*(true|false)",
    )
    observed = []
    for pattern in patterns:
        observed.extend(match.casefold() for match in re.findall(pattern, text_value, re.I))
    delegate = re.search(
        r"KeyguardServiceDelegate(?P<body>.{0,1200})",
        text_value,
        re.I | re.S,
    )
    if delegate:
        showing = re.search(r"\bshowing\s*[=:]\s*(true|false)", delegate.group("body"), re.I)
        if showing:
            observed.append(showing.group(1).casefold())
    if "true" in observed:
        return False
    if "false" in observed:
        return True
    return None


def xml_root(xml_text):
    encoded = xml_text.encode("utf-8", "replace")
    if len(encoded) > MAX_XML_BYTES:
        raise CliError("ui_tree_too_large", "UI hierarchy exceeded the safety size limit")
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise CliError("unsafe_ui_tree", "UI hierarchy contains a prohibited declaration")
    start = xml_text.find("<hierarchy")
    if start < 0:
        raise CliError("ui_tree_unavailable", "ADB did not return a UI hierarchy")
    end = xml_text.rfind("</hierarchy>")
    if end < start:
        raise CliError("ui_tree_unavailable", "ADB returned an incomplete UI hierarchy")
    document = xml_text[start : end + len("</hierarchy>")]
    try:
        return ET.fromstring(document)
    except ET.ParseError as error:
        raise CliError("ui_tree_invalid", "UI hierarchy XML could not be parsed", detail=str(error))


def boxes_from_xml(xml_text):
    root = xml_root(xml_text)
    boxes = []
    packages = set()
    for node in root.iter("node"):
        package = node.attrib.get("package", "")
        if package:
            packages.add(package)
        bounds_match = BOUNDS_PATTERN.fullmatch(node.attrib.get("bounds", ""))
        bounds = None
        if bounds_match:
            x1, y1, x2, y2 = [int(item) for item in bounds_match.groups()]
            bounds = [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]
        for attribute in ("text", "content-desc"):
            text_value = node.attrib.get(attribute, "").strip()
            if text_value:
                boxes.append(
                    {
                        "text": text_value,
                        "bbox": bounds,
                        "confidence": 1.0,
                        "source": "accessibility",
                        "checked": node.attrib.get("checked") == "true",
                        "selected": node.attrib.get("selected") == "true",
                        "class": node.attrib.get("class", ""),
                        "resource_id": node.attrib.get("resource-id", ""),
                        "package": package,
                    }
                )
    rotation = root.attrib.get("rotation")
    return boxes, packages, int(rotation) if str(rotation).isdigit() else None


def state_from_text(text, package=""):
    folded = " ".join((text or "").casefold().split())
    if "select country or region" in folded:
        return "SCOPE_SHEET"
    if "select date range" in folded:
        return "RECENCY_SHEET"
    if "save preferences" in folded and "apply" in folded:
        return "FILTER_SHEET"
    if "search popularity" in folded and (
        "related videos" in folded or "viewer insights" in folded
    ):
        return "TERM_DETAIL"
    if "creation & business tools" in folded:
        return "PROFILE_MENU"
    if "creator tools" in folded and "creator search insights" in folded:
        return "CREATOR_TOOLS"
    if "search field" in folded and "creator search insights" in folded:
        return "TIKTOK_SEARCH"
    if "creator search insights" in folded:
        return "CSI_LIST"
    if "tiktok studio" in folded and "profile" in folded:
        return "PROFILE_MENU"
    if "edit profile" in folded or ("followers" in folded and "following" in folded):
        return "PROFILE"
    if "search" in folded and "search field" in folded:
        return "TIKTOK_SEARCH"
    if package in ALLOWED_PACKAGES:
        return "TIKTOK_HOME"
    return "READY"


def discover_ocr_backend(preference="auto"):
    tesseract = shutil.which("tesseract")
    if preference in {"auto", "tesseract"} and tesseract:
        return {"name": "tesseract", "path": str(Path(tesseract).resolve())}
    if preference == "tesseract":
        return None
    helper = Path(__file__).with_name("vision_ocr.swift")
    swift = shutil.which("swift")
    if preference == "vision" and sys.platform == "darwin" and swift and helper.is_file():
        return {
            "name": "macos_vision",
            "path": str(Path(swift).resolve()),
            "helper": str(helper.resolve()),
        }
    return None


def tesseract_boxes(image_bytes, backend):
    with tempfile.TemporaryDirectory(prefix="csi-ocr-") as directory:
        image_path = Path(directory) / "screen.png"
        image_path.write_bytes(image_bytes)
        result = run_process(
            [backend["path"], str(image_path), "stdout", "--psm", "6", "tsv"],
            check=False,
        )
    if result.returncode != 0:
        return []
    reader = csv.DictReader(result.stdout.splitlines(), delimiter="\t")
    grouped = {}
    for row in reader:
        text_value = str(row.get("text") or "").strip()
        if not text_value:
            continue
        try:
            confidence = max(0.0, float(row.get("conf") or -1) / 100.0)
            left = int(row.get("left") or 0)
            top = int(row.get("top") or 0)
            width = int(row.get("width") or 0)
            height = int(row.get("height") or 0)
        except ValueError:
            continue
        key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
        group = grouped.setdefault(key, {"tokens": [], "confidence": [], "rects": []})
        group["tokens"].append(text_value)
        group["confidence"].append(confidence)
        group["rects"].append((left, top, width, height))
    boxes = []
    for group in grouped.values():
        xs = [item[0] for item in group["rects"]]
        ys = [item[1] for item in group["rects"]]
        rights = [item[0] + item[2] for item in group["rects"]]
        bottoms = [item[1] + item[3] for item in group["rects"]]
        boxes.append(
            {
                "text": " ".join(group["tokens"]),
                "bbox": [min(xs), min(ys), max(rights) - min(xs), max(bottoms) - min(ys)],
                "confidence": sum(group["confidence"]) / len(group["confidence"]),
                "source": "ocr",
            }
        )
    return sorted(boxes, key=lambda item: ((item["bbox"] or [0, 0])[1], (item["bbox"] or [0, 0])[0]))


def vision_boxes(image_bytes, backend):
    with tempfile.TemporaryDirectory(prefix="csi-vision-") as directory:
        image_path = Path(directory) / "screen.png"
        image_path.write_bytes(image_bytes)
        result = run_process(
            [backend["path"], backend["helper"], str(image_path)],
            check=False,
        )
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    boxes = payload.get("boxes", []) if isinstance(payload, dict) else []
    return boxes if isinstance(boxes, list) else []


def local_ocr(image_bytes, backend):
    if not backend:
        return []
    if backend.get("name") == "tesseract":
        return tesseract_boxes(image_bytes, backend)
    if backend.get("name") == "macos_vision":
        return vision_boxes(image_bytes, backend)
    return []


def needs_ocr_fallback(accessibility_boxes):
    if len(accessibility_boxes) < 4:
        return True
    texts = [str(item.get("text") or "") for item in accessibility_boxes]
    in_csi = any("Creator Search Insights" in value for value in texts)
    has_row_metric = any(
        METRIC_PATTERN.search(value.replace(" ", "")) or PERCENTAGE_PATTERN.search(value)
        for value in texts
    )
    return in_csi and not has_row_metric and not any(
        value in {"Save preferences", "Select country or region", "Select date range"}
        for value in texts
    )


def safe_anchors(boxes):
    anchors = []
    for item in boxes:
        text_value = str(item.get("text") or "").strip()
        if text_value in SAFE_ANCHOR_LABELS or text_value.startswith("Selected ("):
            anchors.append(
                {
                    "label": text_value,
                    "bounds": item.get("bbox"),
                    "source": item.get("source", "unknown"),
                }
            )
    return anchors


def preflight_failure(reason, **extra):
    payload = {
        "contract_version": CONTRACT_VERSION,
        "ready": False,
        "reason": reason,
        "capabilities": {},
    }
    payload.update(extra)
    emit(payload)
    return 2


def command_preflight(args):
    try:
        adb_path = resolve_adb_path(args.adb_path)
    except CliError as error:
        return preflight_failure(error.message, error_code=error.code)
    enumeration = run_adb(adb_path, ["devices", "-l"], check=False)
    if enumeration.returncode != 0:
        return preflight_failure("Unable to enumerate Android devices", adb_path=adb_path)
    devices = parse_devices(enumeration.stdout)
    selected = None
    if args.serial:
        matches = [item for item in devices if item["serial"] == args.serial]
        if not matches:
            return preflight_failure("The requested device serial was not found", device_count=len(devices))
        selected = matches[0]
    else:
        if len(devices) == 0:
            return preflight_failure("No Android device is connected", device_count=0)
        if len(devices) > 1:
            return preflight_failure("Multiple Android devices are connected; select one with --serial", device_count=len(devices))
        selected = devices[0]
    serial = str(selected["serial"])
    state = str(selected["state"])
    if state != "device":
        return preflight_failure(
            "Device is %s, not authorized and ready" % state,
            device={"serial": serial, "connected": state != "offline", "authorized": False, "state": state},
        )

    state_result = run_adb(adb_path, ["-s", serial, "get-state"], check=False)
    size_result = run_adb(adb_path, ["-s", serial, "shell", "wm", "size"], check=False)
    screen_result = run_adb(
        adb_path,
        ["-s", serial, "exec-out", "screencap", "-p"],
        binary=True,
        check=False,
    )
    tree_result = run_adb(
        adb_path,
        ["-s", serial, "shell", "uiautomator", "dump", "/dev/tty"],
        check=False,
    )
    screenshot = screen_result.stdout if isinstance(screen_result.stdout, bytes) else b""
    screenshot_ok = (
        screen_result.returncode == 0
        and screenshot.startswith(b"\x89PNG\r\n\x1a\n")
        and 0 < len(screenshot) <= MAX_IMAGE_BYTES
    )
    try:
        width, height = parse_screen_size(size_result.stdout) if size_result.returncode == 0 else (0, 0)
    except CliError:
        width, height = 0, 0
    xml_text = tree_result.stdout if tree_result.returncode == 0 else ""
    try:
        accessibility_boxes, packages, rotation = boxes_from_xml(xml_text)
        tree_ok = True
    except CliError:
        accessibility_boxes, packages, rotation, tree_ok = [], set(), None, False
    backend = discover_ocr_backend(args.ocr_backend)
    ocr_boxes = (
        local_ocr(screenshot, backend)
        if screenshot_ok and backend and needs_ocr_fallback(accessibility_boxes)
        else []
    )
    readable = bool(accessibility_boxes or ocr_boxes)
    foreground_package = next(iter(packages & ALLOWED_PACKAGES), "")
    if not foreground_package and len(packages) == 1:
        foreground_package = next(iter(packages))
    package_probe = run_adb(
        adb_path,
        ["-s", serial, "shell", "dumpsys", "package", args.package],
        check=False,
    )
    package_output = package_probe.stdout if package_probe.returncode == 0 else ""
    android_probe = run_adb(
        adb_path,
        ["-s", serial, "shell", "getprop", "ro.build.version.release"],
        check=False,
    )
    locale_probe = run_adb(
        adb_path,
        ["-s", serial, "shell", "getprop", "persist.sys.locale"],
        check=False,
    )
    density_probe = run_adb(
        adb_path,
        ["-s", serial, "shell", "wm", "density"],
        check=False,
    )
    lock_probe = run_adb(
        adb_path,
        ["-s", serial, "shell", "dumpsys", "window", "policy"],
        check=False,
    )
    input_probe = run_adb(
        adb_path,
        ["-s", serial, "shell", "ls", "/system/bin/input"],
        check=False,
    )
    unlocked = (
        parse_unlocked_state(lock_probe.stdout)
        if lock_probe.returncode == 0
        else None
    )
    version_name_match = re.search(r"\bversionName=([^\s]+)", package_output)
    version_code_match = re.search(r"\bversionCode=(\d+)", package_output)
    package_verified = (
        package_probe.returncode == 0
        and args.package in package_output
        and version_name_match is not None
        and version_code_match is not None
    )
    all_text = "\n".join(str(item.get("text") or "") for item in [*accessibility_boxes, *ocr_boxes])
    stop = detect_stop_text(all_text)
    capabilities = {
        "device_authorized": state_result.returncode == 0 and state_result.stdout.strip() == "device",
        "screen_capture": screenshot_ok,
        "ocr": bool(backend),
        "ui_tree": tree_ok,
        "screen_reading": readable,
        "input_binary": input_probe.returncode == 0,
        "tap_navigation": state_result.returncode == 0 and input_probe.returncode == 0,
        "device_unlocked": unlocked is True,
    }
    ready = (
        all(capabilities[key] for key in ("device_authorized", "screen_capture", "screen_reading", "tap_navigation"))
        and tree_ok
        and width > 0
        and height > 0
        and rotation in {0, 1, 2, 3}
        and package_verified
        and unlocked is True
        and not stop["stop"]
    )
    reason = "ready" if ready else (stop["reason"] if stop["stop"] else "One or more required capabilities are unavailable")
    payload = {
        "contract_version": CONTRACT_VERSION,
        "ready": ready,
        "reason": reason,
        "adb_path": adb_path,
        "device": {
            "serial": serial,
            "connected": True,
            "authorized": capabilities["device_authorized"],
            "model": selected.get("metadata", {}).get("model", ""),
            "android_version": android_probe.stdout.strip() if android_probe.returncode == 0 else None,
            "locale": locale_probe.stdout.strip() if locale_probe.returncode == 0 else None,
        },
        "app": {
            "package": args.package if package_verified else "",
            "version_name": version_name_match.group(1) if version_name_match else None,
            "version_code": version_code_match.group(1) if version_code_match else None,
            "installed_verified": package_verified,
        },
        "capabilities": capabilities,
        "screen": {
            "width": width,
            "height": height,
            "rotation": rotation,
            "hash": hashlib.sha256(screenshot).hexdigest() if screenshot_ok else "",
            "state": state_from_text(all_text, foreground_package),
            "package": foreground_package,
            "title": "Creator Search Insights" if "Creator Search Insights" in all_text else "",
            "unlocked": unlocked,
            "density": density_probe.stdout.strip() if density_probe.returncode == 0 else None,
        },
        "ocr_backend": backend,
        "stop": stop,
        "telemetry_notice": (
            "Navigation and search are read-only for content, but TikTok may still record "
            "ordinary app telemetry or search history. Require saved screenshots instead "
            "when zero platform-visible activity is required."
        ),
    }
    emit(payload)
    return 0 if ready else 2


def command_policy(args):
    request = read_json(Path(args.request_json).expanduser().resolve())
    emit(evaluate_policy(request))
    return 0


def command_detect_stop(args):
    path = Path(args.text_file).expanduser().resolve()
    try:
        text_value = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CliError("text_unavailable", "Unable to read the requested text file", detail=str(error))
    emit(detect_stop_text(text_value))
    return 0


def lease_path_for(serial):
    digest = hashlib.sha256(serial.encode("utf-8")).hexdigest()[:24]
    root = Path(tempfile.gettempdir()) / "csi-android-device-leases-v1"
    secure_directory(root)
    return root / (digest + ".json")


def acquire_lease(serial, run_dir):
    lease_path = lease_path_for(serial)
    record = {
        "contract_version": CONTRACT_VERSION,
        "serial_hash": hashlib.sha256(serial.encode("utf-8")).hexdigest()[:16],
        "run_dir": str(run_dir),
        "acquired_at": utc_timestamp(),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(str(lease_path), flags, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(lease_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        existing_run = Path(str(existing.get("run_dir") or ""))
        if existing_run and not existing_run.exists():
            try:
                lease_path.unlink()
            except OSError:
                pass
            return acquire_lease(serial, run_dir)
        raise CliError(
            "device_lease_active",
            "An active device-serial lease already exists",
            active_run=str(existing.get("run_dir") or "redacted"),
        )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return str(lease_path)


def release_lease(control, run_dir):
    lease_value = str(control.get("lease_path") or "")
    if not lease_value:
        return False
    lease_path = Path(lease_value)
    try:
        record = json.loads(lease_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if Path(str(record.get("run_dir") or "")).resolve() != run_dir.resolve():
        return False
    try:
        lease_path.unlink()
        return True
    except OSError:
        return False


def unique_run_dir(output_root, run_id):
    candidate = output_root / run_id
    suffix = 1
    while candidate.exists():
        candidate = output_root / (run_id + "-%02d" % suffix)
        suffix += 1
    return candidate


def command_begin(args):
    preflight_path = Path(args.preflight_json).expanduser().resolve()
    report = read_json(preflight_path)
    validate_contract_version(report, preflight_path)
    if report.get("ready") is not True:
        raise CliError("preflight_not_ready", "Preflight did not report a ready device")
    capabilities = report.get("capabilities")
    if not isinstance(capabilities, dict):
        raise CliError("preflight_incomplete", "Preflight capability report is missing")
    required = (
        "device_authorized",
        "device_unlocked",
        "screen_capture",
        "screen_reading",
        "ui_tree",
        "input_binary",
        "tap_navigation",
    )
    if not all(capabilities.get(key) is True for key in required):
        raise CliError("preflight_incomplete", "A required device capability is unavailable")
    device = report.get("device") if isinstance(report.get("device"), dict) else {}
    serial = str(device.get("serial") or "")
    if not SAFE_SERIAL.fullmatch(serial):
        raise CliError("invalid_serial", "Preflight did not contain a safe selected serial")
    screen = report.get("screen") if isinstance(report.get("screen"), dict) else {}
    try:
        screen_size = [int(screen["width"]), int(screen["height"])]
    except (KeyError, TypeError, ValueError):
        raise CliError("preflight_incomplete", "Preflight did not contain valid screen dimensions")
    if min(screen_size) <= 0:
        raise CliError("preflight_incomplete", "Preflight screen dimensions are invalid")

    output_root = Path(args.output_root).expanduser().resolve()
    secure_directory(output_root)
    run_id = "csi-" + datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    run_dir = unique_run_dir(output_root, run_id)
    secure_directory(run_dir)
    secure_directory(run_dir / "evidence")
    adb_value = report.get("adb_path")
    if adb_value is None and isinstance(report.get("runtime"), dict):
        adb_value = report["runtime"].get("adb_path")
    if not adb_value:
        raise CliError(
            "preflight_incomplete",
            "Preflight must bind an exact ADB executable path before begin",
        )
    adb_path = resolve_adb_path(adb_value)
    app_report = report.get("app") if isinstance(report.get("app"), dict) else {}
    package = str(app_report.get("package") or screen.get("package") or "")
    if package not in ALLOWED_PACKAGES:
        raise CliError(
            "invalid_tiktok_package",
            "Preflight did not verify an allowlisted TikTok package",
            package=package or "unknown",
        )
    if (
        app_report.get("installed_verified") is not True
        or not str(app_report.get("version_name") or "")
        or not str(app_report.get("version_code") or "")
    ):
        raise CliError(
            "preflight_incomplete",
            "Preflight did not verify the installed TikTok app version",
        )
    screen_package = str(screen.get("package") or "")
    if screen_package and screen_package != package and screen.get("state") != "READY":
        raise CliError(
            "package_mismatch",
            "Preflight app and foreground package values disagree",
        )
    lease_path = None
    try:
        lease_path = acquire_lease(serial, run_dir)
        control = {
            "contract_version": CONTRACT_VERSION,
            "run_id": run_id,
            "serial": serial,
            "adb_path": adb_path,
            "package": package,
            "state": str(screen.get("state") or "READY"),
            "screen_size": screen_size,
            "last_screen_hash": str(screen.get("hash") or ""),
            "last_input_at": None,
            "not_before": None,
            "last_input_intent": None,
            "frame_sequence": 0,
            "action_count": 0,
            "run_started_at_epoch": time.time(),
            "max_action_count": 500,
            "max_runtime_seconds": 90 * 60,
            "lease_path": lease_path,
            "finalized": False,
            "ocr_backend": report.get("ocr_backend"),
            "test_mode_allowed": report.get("_test_fixture") is True,
            "top_verified": screen.get("at_top") is True,
            "verified_selections": [],
        }
        write_json(run_dir / ".control.json", control)
        (run_dir / "actions.ndjson").write_text("", encoding="utf-8")
        secure_file(run_dir / "actions.ndjson")
        run_input = {
            "contract_version": CONTRACT_VERSION,
            "run_id": run_id,
            "capture_window": {"started_at": utc_timestamp(), "ended_at": None},
            "device": {
                "serial": serial,
                "model": device.get("model", ""),
                "android_version": device.get("android_version", ""),
            },
            "app": report.get("app") or {"package": control["package"]},
            "capabilities": capabilities,
            "safety": {
                "read_only": True,
                "telemetry_disclosed_before_begin": True,
                "performed_action_types": [],
                "prohibited_actions_performed": [],
            },
            "limits": {
                "all_max_scrolls": 100,
                "finite_mode_max_scrolls": 300,
                "min_input_interval_seconds": MIN_INPUT_INTERVAL_SECONDS,
                "min_scroll_interval_seconds": MIN_SCROLL_INTERVAL_SECONDS,
                "min_transition_interval_seconds": MIN_TRANSITION_INTERVAL_SECONDS,
            },
            "stop": {"trigger": None, "evidence_ids": []},
            "list_contexts": [],
        }
        write_json(run_dir / "run-input.json", run_input)
        write_json(
            run_dir / "structure-input.json",
            {
                "contract_version": CONTRACT_VERSION,
                "run_id": run_id,
                "navigation": {"observed_path": []},
                "screens": [],
                "requested_control_checks": [],
            },
        )
        (run_dir / "observations-input.ndjson").write_text("", encoding="utf-8")
        secure_file(run_dir / "observations-input.ndjson")
    except Exception:
        if lease_path:
            release_lease({"lease_path": lease_path}, run_dir)
        try:
            (run_dir / "evidence").rmdir()
            run_dir.rmdir()
        except OSError:
            pass
        raise
    emit(
        {
            "status": "ok",
            "run_dir": str(run_dir),
            "capability_report": report,
            "telemetry_notice": (
                "Content remains unengaged, but navigation or TikTok search may create "
                "ordinary platform telemetry or search history."
            ),
        }
    )
    return 0


def load_control(run_dir):
    if (run_dir / ".finalized.json").is_file():
        raise CliError("run_finalized", "The run has already been finalized")
    control_path = run_dir / ".control.json"
    if not control_path.is_file():
        raise CliError("run_not_initialized", "The run was not initialized with begin")
    control = read_json(control_path)
    validate_contract_version(control, control_path)
    if control.get("finalized"):
        raise CliError("run_finalized", "The run has already been finalized")
    return control


def bound_adb_path(control, supplied=None):
    persisted = str(control.get("adb_path") or "")
    if not persisted:
        raise CliError("adb_path_missing", "The run does not have a persisted ADB path")
    if supplied:
        supplied_resolved = resolve_adb_path(supplied)
        if supplied_resolved != persisted:
            raise CliError(
                "adb_path_mismatch",
                "The supplied ADB path is different from the path persisted at begin",
            )
        return supplied_resolved
    return persisted


def snapshot(control, adb_path):
    serial = str(control.get("serial") or "")
    screen_result = run_adb(
        adb_path,
        ["-s", serial, "exec-out", "screencap", "-p"],
        binary=True,
        check=False,
    )
    if screen_result.returncode != 0 or not isinstance(screen_result.stdout, bytes):
        raise CliError("screen_capture_failed", "Unable to capture the current screen")
    image_bytes = screen_result.stdout
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(image_bytes) > MAX_IMAGE_BYTES:
        raise CliError("screen_capture_invalid", "The current screen capture is not a valid bounded PNG")
    tree_result = run_adb(
        adb_path,
        ["-s", serial, "shell", "uiautomator", "dump", "/dev/tty"],
        check=False,
    )
    if tree_result.returncode != 0:
        raise CliError("ui_tree_failed", "Unable to read the current UI hierarchy")
    xml_text = tree_result.stdout
    accessibility_boxes, packages, rotation = boxes_from_xml(xml_text)
    focus_result = run_adb(
        adb_path,
        ["-s", serial, "shell", "dumpsys", "window", "windows"],
        check=False,
    )
    confirmation = run_adb(
        adb_path,
        ["-s", serial, "exec-out", "screencap", "-p"],
        binary=True,
        check=False,
    )
    if confirmation.returncode != 0 or not isinstance(confirmation.stdout, bytes):
        raise CliError("screen_stability_failed", "Unable to confirm the inspected screen state")
    if hashlib.sha256(confirmation.stdout).digest() != hashlib.sha256(image_bytes).digest():
        raise CliError(
            "screen_state_changed",
            "The screen changed while it was being inspected; no input is allowed",
        )
    image_bytes = confirmation.stdout
    backend = control.get("ocr_backend")
    if not isinstance(backend, dict):
        backend = discover_ocr_backend()
    ocr_boxes = local_ocr(image_bytes, backend) if needs_ocr_fallback(accessibility_boxes) else []
    combined = [*accessibility_boxes, *ocr_boxes]
    screen_text = "\n".join(str(item.get("text") or "") for item in combined)
    package = ""
    if focus_result.returncode == 0:
        focus_match = re.search(
            r"mCurrentFocus[^\n]*\s([A-Za-z0-9._]+)/(?:[A-Za-z0-9.$_]+)",
            focus_result.stdout,
        )
        if focus_match:
            package = focus_match.group(1)
    if not package and accessibility_boxes:
        package = str(accessibility_boxes[0].get("package") or "")
    if not package:
        package = next(iter(packages & ALLOWED_PACKAGES), "")
    if not package and len(packages) == 1:
        package = next(iter(packages))
    current_state = state_from_text(screen_text, package)
    stop = detect_stop_text(screen_text)
    foreign_packages = sorted(value for value in packages if value not in ALLOWED_PACKAGES)
    if package in ALLOWED_PACKAGES and foreign_packages:
        stop = {
            "stop": True,
            "reason": "foreign app or system overlay",
            "foreign_package_count": len(foreign_packages),
        }
    return {
        "image": image_bytes,
        "xml": xml_text,
        "accessibility_boxes": accessibility_boxes,
        "ocr_boxes": ocr_boxes,
        "ocr_backend": backend,
        "screen_text": screen_text,
        "screen_hash": hashlib.sha256(image_bytes).hexdigest(),
        "foreground_package": package,
        "packages": sorted(packages),
        "rotation": rotation,
        "state": current_state,
        "safe_anchors": safe_anchors(combined),
        "stop": stop,
    }


def persist_snapshot(run_dir, control, captured):
    sequence = int(control.get("frame_sequence") or 0) + 1
    evidence_id = "frame-%06d" % sequence
    evidence_dir = run_dir / "evidence"
    secure_directory(evidence_dir)
    image_path = evidence_dir / (evidence_id + ".png")
    xml_path = evidence_dir / (evidence_id + ".xml")
    ocr_path = evidence_dir / (evidence_id + ".ocr.json")
    atomic_write_bytes(image_path, captured["image"])
    atomic_write_text(xml_path, captured["xml"])
    write_json(
        ocr_path,
        {
            "contract_version": CONTRACT_VERSION,
            "evidence_id": evidence_id,
            "backend": captured.get("ocr_backend"),
            "accessibility_boxes": captured["accessibility_boxes"],
            "ocr_boxes": captured["ocr_boxes"],
        },
    )
    secure_file(image_path)
    secure_file(xml_path)
    control["frame_sequence"] = sequence
    control["last_screen_hash"] = captured["screen_hash"]
    control["state"] = captured["state"]
    pending_action_id = control.pop("pending_action_id", None)
    if pending_action_id:
        append_ndjson(
            run_dir / "actions.ndjson",
            {
                "contract_version": CONTRACT_VERSION,
                "run_id": control["run_id"],
                "timestamp": utc_timestamp(),
                "kind": "action_result",
                "action_id": pending_action_id,
                "resulting_evidence_id": evidence_id,
                "resulting_screen_hash": captured["screen_hash"],
                "resulting_state": captured["state"],
            },
        )
    write_json(run_dir / ".control.json", control)
    return evidence_id


def record_structure_screen(run_dir, captured, evidence_id):
    structure_path = run_dir / "structure-input.json"
    structure = read_json(structure_path)
    labels = []
    visible_text_verbatim = []
    selected_labels = []
    for item in [*captured["accessibility_boxes"], *captured["ocr_boxes"]]:
        label = " ".join(str(item.get("text") or "").split())
        if (
            captured["state"] in AUTHORIZED_DATA_STATES
            and label
            and label not in visible_text_verbatim
        ):
            visible_text_verbatim.append(label)
        if (
            label
            and (item.get("checked") is True or item.get("selected") is True)
            and label not in selected_labels
        ):
            selected_labels.append(label)
        if label in STRUCTURAL_LABELS and label not in labels:
            labels.append(label)
    claim_evidence_ids = [evidence_id] if evidence_id else []
    privacy_storage = "raw_csi_evidence" if evidence_id else "memory_only_no_structure_claim"
    if not evidence_id and labels:
        evidence_dir = run_dir / "evidence"
        secure_directory(evidence_dir)
        sequence = len(list(evidence_dir.glob("safe-ui-*.json"))) + 1
        safe_evidence_id = "safe-ui-%06d" % sequence
        write_json(
            evidence_dir / (safe_evidence_id + ".json"),
            {
                "contract_version": CONTRACT_VERSION,
                "evidence_id": safe_evidence_id,
                "captured_at": utc_timestamp(),
                "screen_hash": captured["screen_hash"],
                "state": captured["state"],
                "observed_safe_labels": labels,
                "privacy": "sanitized allowlisted labels only; no screenshot or account text",
            },
        )
        claim_evidence_ids = [safe_evidence_id]
        privacy_storage = "sanitized_safe_labels"
    if not evidence_id and not labels:
        return
    structure.setdefault("screens", []).append(
        {
            "screen_id": evidence_id or claim_evidence_ids[0],
            "state": captured["state"],
            "observed_labels": labels,
            "visible_text_verbatim": visible_text_verbatim,
            "selected_labels": selected_labels,
            "claim_status": "observed",
            "scope": "the inspected %s screen at this capture time" % captured["state"],
            "evidence_ids": claim_evidence_ids,
            "screen_hash": captured["screen_hash"],
            "privacy_storage": privacy_storage,
            "captured_at": utc_timestamp(),
        }
    )
    write_json(structure_path, structure)


def record_navigation_action(run_dir, state, intent, target, screen_hash):
    labels = {
        "open_tiktok": "TikTok",
        "tap_profile": "Profile",
        "open_profile_menu": "Profile menu",
        "tap_tiktok_studio": "TikTok Studio",
        "tap_creator_search_insights": "Creator Search Insights",
        "tap_search": "Search",
        "submit_search": "Creator Search Insights search result",
    }
    if intent not in labels:
        return
    structure_path = run_dir / "structure-input.json"
    structure = read_json(structure_path)
    navigation = structure.setdefault("navigation", {})
    path = navigation.setdefault("observed_path", [])
    evidence_dir = run_dir / "evidence"
    secure_directory(evidence_dir)
    navigation_evidence_id = "nav-action-%06d" % (len(path) + 1)
    write_json(
        evidence_dir / (navigation_evidence_id + ".json"),
        {
            "contract_version": CONTRACT_VERSION,
            "evidence_id": navigation_evidence_id,
            "captured_at": utc_timestamp(),
            "kind": "navigation_action",
            "label": labels[intent],
            "intent": intent,
            "from_state": state,
            "screen_hash": screen_hash,
            "privacy": "sanitized action record; no screenshot or account text",
        },
    )
    path.append(
        {
            "order": len(path) + 1,
            "label": labels[intent],
            "role": "navigation_control",
            "interacted": True,
            "from_state": state,
            "target_text": target,
            "claim_status": "observed",
            "scope": "the verified navigation action at this capture time",
            "evidence_ids": [navigation_evidence_id],
            "screen_hash": screen_hash,
            "privacy_storage": "sanitized_action_evidence",
        }
    )
    write_json(structure_path, structure)


def inspect_capture(run_dir, control, adb_path, *, persist=True):
    captured = snapshot(control, adb_path)
    verified = control.setdefault("verified_selections", [])
    for item in captured["accessibility_boxes"]:
        value = str(item.get("text") or "").strip()
        if value and (item.get("checked") is True or item.get("selected") is True):
            if value not in verified and value != "Save preferences":
                verified.append(value)
    expected_package = str(control.get("package") or DEFAULT_PACKAGE)
    if captured["foreground_package"] not in ALLOWED_PACKAGES or (
        expected_package in ALLOWED_PACKAGES
        and captured["foreground_package"] != expected_package
    ):
        raise CliError(
            "wrong_foreground_package",
            "The foreground package does not match the authorized TikTok package",
            foreground_package=captured["foreground_package"] or "unknown",
        )
    persist_allowed = captured["state"] in AUTHORIZED_DATA_STATES
    if captured["stop"]["stop"] and any(
        marker in captured["stop"]["reason"]
        for marker in ("call", "notification", "permission", "login", "overlay")
    ):
        persist_allowed = False
    evidence_id = None
    if persist and persist_allowed:
        evidence_id = persist_snapshot(run_dir, control, captured)
    else:
        control["last_screen_hash"] = captured["screen_hash"]
        control["state"] = captured["state"]
        write_json(run_dir / ".control.json", control)
    record_structure_screen(run_dir, captured, evidence_id)
    captured["evidence_id"] = evidence_id
    return captured


def command_inspect(args):
    run_dir = Path(args.run_dir).expanduser().resolve()
    control = load_control(run_dir)
    adb_path = bound_adb_path(control, getattr(args, "adb_path", None))
    captured = inspect_capture(run_dir, control, adb_path)
    if captured["state"] in {"TERM_DETAIL", "SCOPE_SHEET", "RECENCY_SHEET"} and captured["evidence_id"]:
        append_detail_observations(run_dir, control, captured, captured["evidence_id"])
    payload = {
        "status": "ok",
        "run_dir": str(run_dir),
        "state": captured["state"],
        "screen_hash": captured["screen_hash"],
        "foreground_package": captured["foreground_package"],
        "safe_anchors": captured["safe_anchors"],
        "accessibility_boxes": captured["accessibility_boxes"] if captured["state"] in AUTHORIZED_DATA_STATES else [],
        "ocr_boxes": captured["ocr_boxes"] if captured["state"] in AUTHORIZED_DATA_STATES else [],
        "stop": captured["stop"],
        "evidence_id": captured["evidence_id"],
    }
    emit(payload)
    return 0


def next_observation_number(path):
    if not path.is_file():
        return 1
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count + 1


def line_sort_key(item):
    bbox = item.get("bbox") or [0, 0, 0, 0]
    return int(bbox[1]), int(bbox[0])


def rows_from_boxes(boxes, screen_size):
    width, height = screen_size
    tab_bottoms = []
    for item in boxes:
        normalized = " ".join(str(item.get("text") or "").split())
        bbox = parse_bounds(item.get("bbox"))
        if normalized in {"All", "Content gap", "Searches by followers"} and bbox:
            tab_bottoms.append(bbox[1] + bbox[3])
    viewport_top = max(tab_bottoms) + 8 if tab_bottoms else int(height * 0.25)
    usable = []
    for item in boxes:
        text_value = str(item.get("text") or "").rstrip("\r\n")
        bbox = parse_bounds(item.get("bbox"))
        if not text_value or bbox is None:
            continue
        if bbox[1] < viewport_top or bbox[1] > int(height * 0.94):
            continue
        copy = dict(item)
        copy["text"] = text_value
        copy["bbox"] = bbox
        usable.append(copy)
    usable.sort(key=line_sort_key)

    rows = []
    consumed = set()
    for index, candidate in enumerate(usable):
        if index in consumed:
            continue
        term = candidate["text"]
        normalized_term = " ".join(term.split())
        term_box = candidate["bbox"]
        if normalized_term in CONTROL_TEXT or normalized_term.startswith("▲") or normalized_term.startswith("△"):
            continue
        if term_box[0] > int(width * 0.70):
            continue
        if METRIC_PATTERN.fullmatch(normalized_term.replace(" ", "")) or PERCENTAGE_PATTERN.fullmatch(normalized_term):
            continue
        term_lines = [term]
        if index + 1 < len(usable):
            continuation = usable[index + 1]
            continuation_text = str(continuation["text"])
            continuation_normalized = " ".join(continuation_text.split())
            continuation_box = continuation["bbox"]
            vertical_gap = continuation_box[1] - (term_box[1] + term_box[3])
            is_value = (
                METRIC_PATTERN.fullmatch(continuation_normalized.replace(" ", ""))
                or PERCENTAGE_PATTERN.search(continuation_normalized)
                or continuation_normalized in CONTROL_TEXT
            )
            if (
                not is_value
                and -5 <= vertical_gap <= max(45, int(height * 0.02))
                and abs(continuation_box[0] - term_box[0]) <= int(width * 0.08)
            ):
                term_lines.append(continuation_text)
                term = term.rstrip() + " " + continuation_text.lstrip()
                left = min(term_box[0], continuation_box[0])
                top = min(term_box[1], continuation_box[1])
                right = max(term_box[0] + term_box[2], continuation_box[0] + continuation_box[2])
                bottom = max(term_box[1] + term_box[3], continuation_box[1] + continuation_box[3])
                term_box = [left, top, right - left, bottom - top]
                consumed.add(index + 1)
        following = []
        for other in usable[index + 1 :]:
            delta = other["bbox"][1] - term_box[1]
            if delta < 0:
                continue
            if delta > max(150, int(height * 0.07)):
                break
            following.append(other)
        metric_text = ""
        percentage_text = ""
        direction = ""
        secondary = ""
        secondary_kind = ""
        for other in following:
            value = other["text"]
            normalized_value = " ".join(value.split())
            percentage_match = PERCENTAGE_PATTERN.search(normalized_value)
            if percentage_match and not percentage_text:
                percentage_text = percentage_match.group(1)
                prefix = normalized_value[: percentage_match.start(1)]
                direction_match = re.search(r"(▲|△|↑)", prefix)
                direction = direction_match.group(1) if direction_match else ""
                continue
            compact = normalized_value.replace(" ", "")
            if not metric_text and METRIC_PATTERN.fullmatch(compact):
                metric_text = normalized_value
                continue
            if (
                normalized_value in {
                    "Great for a first post",
                    "High content gap",
                    "Performed well",
                    "Popular with your followers",
                    "Recently explored",
                    "Trending in ET",
                }
                or re.search(r"\+\s*posts$", normalized_value, re.I)
            ):
                secondary = normalized_value
                secondary_kind = "post_count" if "posts" in normalized_value.casefold() else "descriptor"
        if not metric_text and not percentage_text:
            continue
        rows.append(
            {
                "term": term,
                "term_lines": term_lines,
                "bbox": term_box,
                "primary_metric_text": metric_text,
                "blue_percentage_text": percentage_text,
                "direction_glyph_text": direction,
                "secondary_text_verbatim": secondary,
                "secondary_kind": secondary_kind,
                "read_method": candidate.get("source", "unknown"),
                "confidence": candidate.get("confidence"),
            }
        )
    unique = []
    seen = set()
    for row in rows:
        key = (row["term"], tuple(row["bbox"]))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def append_list_observations(run_dir, control, captured, evidence_id, context_id, scroll_index):
    path = run_dir / "observations-input.ndjson"
    start = next_observation_number(path)
    source_boxes = [*captured["accessibility_boxes"], *captured["ocr_boxes"]]
    rows = rows_from_boxes(source_boxes, control["screen_size"])
    for visible_index, row in enumerate(rows, start=1):
        observation = {
            "contract_version": CONTRACT_VERSION,
            "run_id": control["run_id"],
            "kind": "list_row",
            "observation_id": "obs-%07d" % start,
            "list_context_id": context_id,
            "evidence_id": evidence_id,
            "captured_at": utc_timestamp(),
            "screen_sequence": int(control.get("frame_sequence") or 0),
            "scroll_index": scroll_index,
            "visible_row_index": visible_index,
            "bbox": row["bbox"],
            "term_text_verbatim": row["term"],
            "term_text_lines": row["term_lines"],
            "primary_metric_text": row["primary_metric_text"],
            "primary_metric_icon": "",
            "blue_percentage_text": row["blue_percentage_text"],
            "direction_glyph_text": row["direction_glyph_text"],
            "sparkline_present": None,
            "secondary_text_verbatim": row["secondary_text_verbatim"],
            "secondary_kind": row["secondary_kind"],
            "read_method": row["read_method"],
            "confidence": row["confidence"],
            "review_status": "unreviewed",
            "original_ocr_text": "\n".join(row["term_lines"]),
        }
        append_ndjson(path, observation)
        start += 1
    return len(rows)


def detail_label_and_value(text_value):
    creators = re.search(r"\b([\d.,KMB]+)\s+creators?\s+also\s+posted\b", text_value, re.I)
    if creators:
        return "Creators also posted", text_value
    if text_value in {
        "Search popularity",
        "Related videos",
        "Viewer insights",
        "What viewers are searching",
        "Explore more topics",
        "Select country or region",
        "Select date range",
    }:
        return text_value, ""
    if text_value in {
        "Global",
        "Global (all)",
        "Last 7 days",
        "Last 30 days",
        "Last 60 days",
        "Last 6 months",
        "Custom",
    }:
        return "Visible selector value", text_value
    return "Visible detail text", text_value


def append_detail_observations(run_dir, control, captured, evidence_id):
    path = run_dir / "observations-input.ndjson"
    start = next_observation_number(path)
    boxes = [*captured["accessibility_boxes"], *captured["ocr_boxes"]]
    seen = set()
    written = 0
    for item in sorted(boxes, key=line_sort_key):
        text_value = str(item.get("text") or "")
        bbox = parse_bounds(item.get("bbox"))
        key = (text_value, tuple(bbox or []), item.get("source"))
        if not text_value or key in seen:
            continue
        seen.add(key)
        ui_label, value_text = detail_label_and_value(text_value)
        append_ndjson(
            path,
            {
                "contract_version": CONTRACT_VERSION,
                "run_id": control["run_id"],
                "kind": "detail_field",
                "observation_id": "obs-%07d" % start,
                "list_context_id": control.get("detail_context_id", ""),
                "evidence_id": evidence_id,
                "captured_at": utc_timestamp(),
                "term_observation_id": control.get("detail_term_observation_id"),
                "term_text_verbatim": control.get("detail_term", ""),
                "ui_path": captured["state"],
                "ui_label": ui_label,
                "value_text": value_text,
                "nested_value": None,
                "bbox": bbox,
                "read_method": item.get("source", "unknown"),
                "confidence": item.get("confidence"),
                "review_status": "unreviewed",
                "original_ocr_text": text_value,
            },
        )
        start += 1
        written += 1
    return written


def action_bounds(args):
    return parse_bounds(args.bounds) if getattr(args, "bounds", None) else None


def action_request(control, args, state):
    request = {
        "kind": "intent",
        "state": state,
        "intent": args.intent,
        "target": getattr(args, "target", None),
        "bounds": action_bounds(args),
        "screen_size": control.get("screen_size"),
    }
    if args.intent == "type_search_query":
        request["text"] = getattr(args, "text", None)
        request["focused_control"] = getattr(args, "focused_control", None)
    return request


def input_argv_for(control, args, normalized):
    serial = str(control["serial"])
    width, height = [int(item) for item in control["screen_size"]]
    if normalized == "open_tiktok":
        package = str(control.get("package") or DEFAULT_PACKAGE)
        return [
            "-s",
            serial,
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            "-p",
            package,
        ]
    if normalized in {"scroll_list", "scroll_detail"}:
        x = width // 2
        return [
            "-s",
            serial,
            "shell",
            "input",
            "swipe",
            str(x),
            str(int(height * 0.72)),
            str(x),
            str(int(height * 0.34)),
            "750",
        ]
    if normalized == "back":
        return ["-s", serial, "shell", "input", "keyevent", "KEYCODE_BACK"]
    if normalized == "type_search_query":
        return [
            "-s",
            serial,
            "shell",
            "input",
            "text",
            "creator%ssearch%sinsights",
        ]
    bounds = action_bounds(args)
    if bounds is None or not geometry_is_safe(bounds, control["screen_size"]):
        raise CliError("unsafe_geometry", "A verified in-screen target rectangle is required")
    x, y, width_value, height_value = bounds
    return [
        "-s",
        serial,
        "shell",
        "input",
        "tap",
        str(x + width_value // 2),
        str(y + height_value // 2),
    ]


def next_state_after(state, intent):
    transitions = {
        ("READY", "open_tiktok"): "TIKTOK_HOME",
        ("TIKTOK_HOME", "tap_profile"): "PROFILE",
        ("PROFILE", "open_profile_menu"): "PROFILE_MENU",
        ("PROFILE_MENU", "tap_tiktok_studio"): "CREATOR_TOOLS",
        ("PROFILE_MENU", "tap_creator_search_insights"): "CSI_LIST",
        ("CREATOR_TOOLS", "tap_creator_search_insights"): "CSI_LIST",
        ("CSI_LIST", "open_filter"): "FILTER_SHEET",
        ("CSI_LIST", "tap_term_detail"): "TERM_DETAIL",
        ("TERM_DETAIL", "open_scope"): "SCOPE_SHEET",
        ("TERM_DETAIL", "open_recency"): "RECENCY_SHEET",
    }
    if intent in {"scroll_list", "scroll_detail", "select_tab", "select_filter"}:
        return state
    return transitions.get((state, intent), "UNVERIFIED")


def action_interval(intent):
    if intent in {"scroll_list", "scroll_detail"}:
        return MIN_SCROLL_INTERVAL_SECONDS
    if intent in {"open_tiktok", "tap_profile", "open_profile_menu", "tap_creator_search_insights", "tap_tiktok_studio", "tap_term_detail", "back"}:
        return MIN_TRANSITION_INTERVAL_SECONDS
    return MIN_INPUT_INTERVAL_SECONDS


def verify_pacing(control, now):
    not_before = control.get("not_before")
    if not_before is not None and now < float(not_before):
        raise CliError(
            "pacing_violation",
            "The next input would be too soon; wait for the pacing interval",
            wait_seconds=round(float(not_before) - now, 3),
        )


def verify_run_budget(control):
    if int(control.get("action_count") or 0) >= int(control.get("max_action_count") or 500):
        raise CliError("action_budget_reached", "The run reached its device-input budget")
    if not test_mode_enabled(control):
        elapsed = time.time() - float(control.get("run_started_at_epoch") or time.time())
        if elapsed >= float(control.get("max_runtime_seconds") or 90 * 60):
            raise CliError("runtime_budget_reached", "The run reached its runtime budget")


def execute_action(run_dir, control, adb_path, args, *, fresh_capture=None):
    captured = fresh_capture or snapshot(control, adb_path)
    launching_from_ready = args.intent == "open_tiktok" and control.get("state") == "READY"
    if captured["foreground_package"] not in ALLOWED_PACKAGES and not launching_from_ready:
        raise CliError("wrong_foreground_package", "TikTok is not the verified foreground package")
    if captured["stop"]["stop"]:
        raise CliError("blocked_screen", "A fail-closed stop screen was detected", reason=captured["stop"]["reason"])
    supplied_hash = str(args.screen_hash or "")
    if supplied_hash != captured["screen_hash"]:
        raise CliError(
            "stale_screen_hash",
            "The supplied screen hash is stale and no input was sent",
            current_screen_hash=captured["screen_hash"],
        )
    state = captured["state"]
    expected_state = str(control.get("state") or "")
    if expected_state not in {state, "UNVERIFIED", "UNKNOWN"}:
        raise CliError(
            "device_state_diverged",
            "The current UI state diverged from the run state; inspect before continuing",
            expected_state=expected_state,
            actual_state=state,
        )
    request = action_request(control, args, state)
    request["available_anchors"] = captured["safe_anchors"]
    request["available_text_boxes"] = [
        {"text": item.get("text"), "bbox": item.get("bbox")}
        for item in [*captured["accessibility_boxes"], *captured["ocr_boxes"]]
    ]
    if args.intent == "apply_filter" and any(
        str(item.get("text") or "") == "Save preferences" and item.get("checked") is True
        for item in captured["accessibility_boxes"]
    ):
        raise CliError(
            "persistent_filter_blocked",
            "Apply was blocked because Save preferences is enabled",
        )
    decision = evaluate_policy(request)
    if not decision["allowed"]:
        raise CliError("action_denied", decision["reason"], intent=args.intent)
    now = monotonic_wall_time(control)
    verify_run_budget(control)
    verify_pacing(control, now)
    adb_argv = input_argv_for(control, args, decision["normalized_action"])
    result = run_adb(adb_path, adb_argv, check=False)
    if result.returncode != 0:
        raise CliError("input_failed", "The allowlisted device input failed", intent=args.intent)
    control["last_input_at"] = now
    control["not_before"] = now + action_interval(args.intent)
    control["last_input_intent"] = args.intent
    control["action_count"] = int(control.get("action_count") or 0) + 1
    action_id = "action-%06d" % control["action_count"]
    control["pending_action_id"] = action_id
    control["state"] = next_state_after(state, args.intent)
    if args.intent == "select_tab":
        control["selected_mode"] = args.target
        control["top_verified"] = True
    elif args.intent in {"scroll_list", "scroll_detail"}:
        control["top_verified"] = False
    elif args.intent in {"select_filter", "select_scope", "select_recency"}:
        pending = control.setdefault("pending_selections", [])
        if args.target not in pending:
            pending.append(args.target)
    if args.intent == "tap_term_detail":
        control["detail_term"] = args.target
        observations_path = run_dir / "observations-input.ndjson"
        if observations_path.is_file():
            for record in reversed(read_ndjson(observations_path)):
                if (
                    record.get("kind") == "list_row"
                    and record.get("term_text_verbatim") == args.target
                ):
                    control["detail_term_observation_id"] = record.get("observation_id")
                    control["detail_context_id"] = record.get("list_context_id")
                    break
    write_json(run_dir / ".control.json", control)
    append_ndjson(
        run_dir / "actions.ndjson",
        {
            "contract_version": CONTRACT_VERSION,
            "run_id": control["run_id"],
            "timestamp": utc_timestamp(),
            "kind": "action",
            "action_id": action_id,
            "intent": args.intent,
            "target": getattr(args, "target", None),
            "bounds": action_bounds(args),
            "prior_screen_hash": supplied_hash,
            "verified_state": state,
            "result": "sent",
        },
    )
    record_navigation_action(
        run_dir,
        state,
        args.intent,
        getattr(args, "target", None),
        supplied_hash,
    )
    record_performed_action(run_dir, args.intent)
    return {"ok": True, "intent": args.intent, "next_inspect_required": True}


def command_act(args):
    run_dir = Path(args.run_dir).expanduser().resolve()
    control = load_control(run_dir)
    adb_path = bound_adb_path(control, args.adb_path)
    result = execute_action(run_dir, control, adb_path, args)
    emit(result)
    return 0


def set_context(run_dir, context):
    run_path = run_dir / "run-input.json"
    run_input = read_json(run_path)
    contexts = run_input.setdefault("list_contexts", [])
    matches = [index for index, item in enumerate(contexts) if item.get("list_context_id") == context["list_context_id"]]
    if matches:
        contexts[matches[0]] = context
    else:
        contexts.append(context)
    write_json(run_path, run_input)


def record_performed_action(run_dir, intent):
    run_path = run_dir / "run-input.json"
    run_input = read_json(run_path)
    safety = run_input.setdefault("safety", {})
    actions = safety.setdefault("performed_action_types", [])
    if intent not in actions:
        actions.append(intent)
    write_json(run_path, run_input)


def capture_tab_failure(run_dir, context, captured, reason):
    context["capture_status"] = "blocked"
    context["endpoint_reached"] = False
    context["endpoint_text"] = ""
    context["stop_reason"] = reason
    set_context(run_dir, context)
    run_path = run_dir / "run-input.json"
    run_input = read_json(run_path)
    run_input["stop"] = {
        "trigger": reason,
        "evidence_ids": [captured.get("evidence_id")] if captured.get("evidence_id") else [],
    }
    write_json(run_path, run_input)


def command_capture_tab(args):
    run_dir = Path(args.run_dir).expanduser().resolve()
    control = load_control(run_dir)
    adb_path = bound_adb_path(control, args.adb_path)
    if control.get("state") not in {"CSI_LIST", "CSI_HOME"}:
        raise CliError("wrong_state", "capture-tab requires a verified Creator Search Insights list")
    if control.get("top_verified") is not True:
        raise CliError(
            "top_not_verified",
            "Capture must start from a verified top-of-list state, normally immediately after selecting the tab",
        )
    verified_selections = set(control.get("verified_selections") or [])
    requested_selections = set([*(args.language or []), *(args.filter or [])])
    if args.region:
        requested_selections.add(args.region)
    unverified = sorted(requested_selections - verified_selections)
    if unverified:
        raise CliError(
            "selection_not_verified",
            "Requested list settings were not observed as selected",
            selections=unverified,
        )
    max_scrolls = args.max_scrolls
    if max_scrolls is None:
        max_scrolls = 100 if args.mode == "All" else 300
    if max_scrolls < 0 or max_scrolls > 300:
        raise CliError("invalid_limit", "max-scrolls must be between 0 and 300")
    existing_contexts = read_json(run_dir / "run-input.json").get("list_contexts", [])
    context_id = "ctx-%03d-%s" % (
        len(existing_contexts) + 1,
        re.sub(r"[^a-z0-9]+", "-", args.mode.casefold()).strip("-") or "list",
    )
    context = {
        "list_context_id": context_id,
        "mode_label": args.mode,
        "category_label": args.category if args.category in verified_selections else "",
        "requested_category_label": args.category,
        "language_labels": args.language or [],
        "filter_labels": args.filter or [],
        "region_label": args.region or "",
        "capture_status": "partial_interrupted",
        "from_top": True,
        "endpoint_reached": False,
        "endpoint_text": "",
        "scroll_steps": 0,
        "screen_count": 0,
        "evidence_ids": [],
        "scope_note": "Point-in-time capture for the exact recorded list context",
    }
    set_context(run_dir, context)
    screen_hashes = []
    total_rows = 0
    for scroll_index in range(max_scrolls + 1):
        captured = inspect_capture(run_dir, control, adb_path, persist=True)
        context["screen_count"] += 1
        if captured["evidence_id"]:
            context["evidence_ids"].append(captured["evidence_id"])
        screen_hashes.append(captured["screen_hash"])
        if captured["stop"]["stop"]:
            capture_tab_failure(run_dir, context, captured, captured["stop"]["reason"])
            raise CliError(
                "capture_blocked",
                "Capture stopped immediately on a challenge or protected screen",
                reason=captured["stop"]["reason"],
                evidence_ids=[captured["evidence_id"]],
            )
        if captured["state"] != "CSI_LIST":
            context["capture_status"] = "partial_interrupted"
            context["stop_reason"] = "list_state_lost"
            set_context(run_dir, context)
            raise CliError(
                "list_state_lost",
                "The verified Creator Search Insights list state was lost before scrolling",
                evidence_ids=[captured["evidence_id"]] if captured["evidence_id"] else [],
            )
        visible_texts = {
            " ".join(str(item.get("text") or "").split())
            for item in [*captured["accessibility_boxes"], *captured["ocr_boxes"]]
        }
        if args.mode not in visible_texts and control.get("selected_mode") != args.mode:
            context["capture_status"] = "partial_interrupted"
            set_context(run_dir, context)
            raise CliError(
                "mode_not_verified",
                "The requested list mode was not visible or previously verified as selected",
                requested_mode=args.mode,
            )
        if args.category in visible_texts:
            context["category_label"] = args.category
        total_rows += append_list_observations(
            run_dir,
            control,
            captured,
            captured["evidence_id"],
            context_id,
            scroll_index,
        )
        endpoint = any(
            " ".join(str(item.get("text") or "").split()) == "No more searches"
            for item in [*captured["accessibility_boxes"], *captured["ocr_boxes"]]
        )
        if endpoint:
            context["capture_status"] = "point_in_time_endpoint_reached"
            context["endpoint_reached"] = True
            context["endpoint_text"] = "No more searches"
            break
        if len(screen_hashes) >= 3 and len(set(screen_hashes[-3:])) == 1:
            context["capture_status"] = "partial_interrupted"
            context["stop_reason"] = "same screen repeated after two verified scrolls"
            break
        if scroll_index >= max_scrolls:
            context["capture_status"] = "bounded_no_endpoint"
            break
        now = monotonic_wall_time(control)
        control["not_before"] = None if test_mode_enabled(control) else control.get("not_before")
        verify_run_budget(control)
        verify_pacing(control, now)
        scroll_decision = evaluate_policy(
            {
                "kind": "intent",
                "state": captured["state"],
                "intent": "scroll_list",
                "screen_size": control["screen_size"],
                "available_anchors": captured["safe_anchors"],
            }
        )
        if not scroll_decision["allowed"]:
            context["capture_status"] = "partial_interrupted"
            set_context(run_dir, context)
            raise CliError("scroll_denied", scroll_decision["reason"])
        width, height = [int(item) for item in control["screen_size"]]
        swipe = [
            "-s",
            str(control["serial"]),
            "shell",
            "input",
            "swipe",
            str(width // 2),
            str(int(height * 0.72)),
            str(width // 2),
            str(int(height * 0.34)),
            "750",
        ]
        result = run_adb(adb_path, swipe, check=False)
        if result.returncode != 0:
            context["capture_status"] = "partial_interrupted"
            set_context(run_dir, context)
            raise CliError("scroll_failed", "An allowlisted list scroll failed")
        context["scroll_steps"] += 1
        control["last_input_at"] = now
        control["not_before"] = now + MIN_SCROLL_INTERVAL_SECONDS
        control["last_input_intent"] = "scroll_list"
        control["action_count"] = int(control.get("action_count") or 0) + 1
        control["state"] = "CSI_LIST"
        control["top_verified"] = False
        write_json(run_dir / ".control.json", control)
        append_ndjson(
            run_dir / "actions.ndjson",
            {
                "contract_version": CONTRACT_VERSION,
                "run_id": control["run_id"],
                "timestamp": utc_timestamp(),
                "kind": "action",
                "action_id": "action-%06d" % control["action_count"],
                "intent": "scroll_list",
                "target": args.mode,
                "bounds": [0, int(height * 0.25), width, int(height * 0.69)],
                "prior_screen_hash": captured["screen_hash"],
                "verified_state": captured["state"],
                "result": "sent",
            },
        )
        control["pending_action_id"] = "action-%06d" % control["action_count"]
        write_json(run_dir / ".control.json", control)
        record_performed_action(run_dir, "scroll_list")
        if not test_mode_enabled(control):
            time.sleep(MIN_SCROLL_INTERVAL_SECONDS)
    set_context(run_dir, context)
    emit(
        {
            "status": "ok",
            "run_dir": str(run_dir),
            "mode": args.mode,
            "list_context_id": context_id,
            "capture_status": context["capture_status"],
            "screen_count": context["screen_count"],
            "scroll_steps": context["scroll_steps"],
            "endpoint_reached": context["endpoint_reached"],
            "endpoint_text": context["endpoint_text"],
            "raw_observation_count": total_rows,
        }
    )
    return 0


def source_evidence_path(source, evidence_id):
    frames = source / "frames"
    candidates = sorted(
        path
        for path in frames.glob(evidence_id + ".png")
        if path.is_file() and not path.is_symlink()
    )
    return candidates[0] if candidates else None


def command_replay(args):
    source = Path(args.source).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    run_input_path = source / "run-input.json"
    observations_path = source / "observations-input.ndjson"
    structure_path = source / "structure-input.json"
    if not source.is_dir():
        raise CliError("missing_source", "Replay source directory does not exist", path=str(source))
    run_input = read_json(run_input_path)
    validate_contract_version(run_input, run_input_path)
    observations = read_ndjson(observations_path)
    run_id = validate_run_id(run_input.get("run_id") or "offline-replay")
    missing = sorted(
        {
            str(item.get("evidence_id", ""))
            for item in observations
            if item.get("evidence_id") and source_evidence_path(source, str(item["evidence_id"])) is None
        }
    )
    if missing:
        raise CliError(
            "missing_evidence",
            "One or more observations reference missing evidence",
            evidence_ids=missing,
        )

    secure_directory(output_root)
    run_dir = output_root / run_id
    if run_dir.exists():
        raise CliError("run_exists", "Replay run directory already exists", path=str(run_dir))
    secure_directory(run_dir)
    secure_directory(run_dir / "evidence")

    shutil.copyfile(run_input_path, run_dir / "run-input.json")
    shutil.copyfile(observations_path, run_dir / "observations-input.ndjson")
    if structure_path.is_file():
        shutil.copyfile(structure_path, run_dir / "structure-input.json")
    else:
        write_json(
            run_dir / "structure-input.json",
            {"contract_version": CONTRACT_VERSION, "run_id": run_id},
        )
    for input_name in ("run-input.json", "observations-input.ndjson", "structure-input.json"):
        secure_file(run_dir / input_name)

    frames_dir = source / "frames"
    if not frames_dir.is_dir() or frames_dir.is_symlink():
        raise CliError("invalid_evidence_directory", "Replay frames must be a real local directory")
    frame_paths = sorted((path for path in frames_dir.iterdir() if path.is_file()), key=lambda path: path.name)
    seen_names = set()
    for source_path in frame_paths:
        if source_path.is_symlink() or source_path.suffix.lower() != ".png":
            raise CliError(
                "invalid_evidence_file",
                "Replay frames must be non-symlink PNG files",
                path=str(source_path),
            )
        destination_name = source_path.name
        if (
            destination_name in seen_names
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.png", destination_name)
        ):
            raise CliError("duplicate_evidence", "Replay evidence filenames must be unique")
        seen_names.add(destination_name)
        destination = run_dir / "evidence" / destination_name
        shutil.copyfile(source_path, destination)
        secure_file(destination)

    emit({"status": "ok", "run_dir": str(run_dir)})
    return 0


def conflict_values(records, field):
    values = []
    for record in records:
        value = str(record.get(field) or "")
        if value and value not in values:
            values.append(value)
    return values


def command_extract(args):
    run_dir = Path(args.run_dir).expanduser().resolve()
    if (run_dir / ".finalized.json").is_file():
        raise CliError("run_finalized", "Canonical artifacts cannot be changed after finalization")
    control_path = run_dir / ".control.json"
    if control_path.is_file() and read_json(control_path).get("finalized"):
        raise CliError("run_finalized", "Canonical artifacts cannot be changed after finalization")
    run_input = read_json(run_dir / "run-input.json")
    validate_contract_version(run_input, run_dir / "run-input.json")
    observations = read_ndjson(run_dir / "observations-input.ndjson")
    for record in observations:
        validate_contract_version(record, run_dir / "observations-input.ndjson")
        if record.get("run_id") != run_input.get("run_id"):
            raise CliError("run_id_mismatch", "Observation run_id does not match the run")
    write_ndjson(run_dir / "observations.ndjson", observations)

    details_by_term_observation = {}
    for record in observations:
        if record.get("kind") != "detail_field":
            continue
        term_observation_id = str(record.get("term_observation_id") or "")
        if term_observation_id:
            details_by_term_observation.setdefault(term_observation_id, []).append(record)

    context_by_id = {
        str(item.get("list_context_id")): item for item in run_input.get("list_contexts", [])
    }
    groups = {}
    group_order = []
    for record in observations:
        if record.get("kind") != "list_row":
            continue
        term = str(record.get("term_text_verbatim") or "")
        context_id = str(record.get("list_context_id") or "")
        if not term or context_id not in context_by_id:
            raise CliError(
                "invalid_observation",
                "List-row observation is missing a term or valid context",
                observation_id=record.get("observation_id"),
            )
        key = (context_id, term_match_key(term))
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(record)

    conflicts = []
    rows = []
    conflict_fields = ("primary_metric_text", "blue_percentage_text", "direction_glyph_text")
    context_orders = {}
    for global_order, key in enumerate(group_order, start=1):
        records = groups[key]
        first = records[0]
        context_id = key[0]
        context_orders[context_id] = context_orders.get(context_id, 0) + 1
        order = context_orders[context_id]
        context = context_by_id[context_id]
        terms = []
        for record in records:
            value = str(record.get("term_text_verbatim") or "")
            if value not in terms:
                terms.append(value)
        group_conflicts = []
        if len(terms) > 1:
            group_conflicts.append("term_text_verbatim")
            conflicts.append(
                {
                    "contract_version": CONTRACT_VERSION,
                    "run_id": run_input["run_id"],
                    "conflict_id": "conflict-%04d-term_text_verbatim" % global_order,
                    "entity_key": {"list_context_id": context_id, "term_match_key": key[1]},
                    "field": "term_text_verbatim",
                    "kind": "unresolved_variant",
                    "observed_values": terms,
                    "source_observation_ids": [
                        str(record.get("observation_id")) for record in records
                    ],
                    "source_evidence_ids": sorted(
                        {str(record.get("evidence_id")) for record in records}
                    ),
                    "resolution": {"status": "unresolved", "value": None},
                }
            )
        display_term = "" if group_conflicts else terms[0]
        safe_term, escaped = spreadsheet_safe(display_term)
        scalar_values = {}
        for field in conflict_fields:
            values = conflict_values(records, field)
            if len(values) > 1:
                group_conflicts.append(field)
                scalar_values[field] = ""
                conflicts.append(
                    {
                        "contract_version": CONTRACT_VERSION,
                        "run_id": run_input["run_id"],
                        "conflict_id": "conflict-%04d-%s" % (global_order, field),
                        "entity_key": {"list_context_id": context_id, "term_match_key": key[1]},
                        "field": field,
                        "kind": "temporal_change",
                        "observed_values": values,
                        "source_observation_ids": [
                            str(record.get("observation_id")) for record in records
                        ],
                        "source_evidence_ids": sorted(
                            {str(record.get("evidence_id")) for record in records}
                        ),
                        "resolution": {"status": "unresolved", "value": None},
                    }
                )
            else:
                scalar_values[field] = values[0] if values else ""

        secondary_values = []
        for record in records:
            text = str(record.get("secondary_text_verbatim") or "")
            kind = str(record.get("secondary_kind") or "")
            value = {"text": text, "kind": kind or "unknown"}
            if text and value not in secondary_values:
                secondary_values.append(value)
        source_observation_ids = [str(record.get("observation_id")) for record in records]
        source_evidence_ids = sorted({str(record.get("evidence_id")) for record in records})
        detail_records = []
        for observation_id in source_observation_ids:
            detail_records.extend(details_by_term_observation.get(observation_id, []))
        detail_scope = ""
        detail_recency = ""
        detail_creators = ""
        search_popularity_context = False
        for detail in detail_records:
            label = str(detail.get("ui_label") or "")
            value = str(detail.get("value_text") or "")
            ui_path = str(detail.get("ui_path") or "")
            if label == "Creators also posted" and not detail_creators:
                detail_creators = value
            elif label == "Search popularity":
                search_popularity_context = True
            elif label == "Visible selector value" and ui_path == "TERM_DETAIL":
                if value.startswith("Last ") or value == "Custom":
                    detail_recency = detail_recency or value
                else:
                    detail_scope = detail_scope or value
        source_evidence_ids = sorted(
            set(source_evidence_ids)
            | {str(record.get("evidence_id")) for record in detail_records if record.get("evidence_id")}
        )
        source_observation_ids.extend(
            str(record.get("observation_id"))
            for record in detail_records
            if record.get("observation_id")
        )
        rows.append(
            {
                "contract_version": CONTRACT_VERSION,
                "run_id": str(run_input["run_id"]),
                "list_context_id": context_id,
                "mode_label": str(context.get("mode_label") or ""),
                "category_label": str(context.get("category_label") or ""),
                "language_labels_json": json_cell(context.get("language_labels", [])),
                "first_observed_order": str(order),
                "term_display_safe": safe_term,
                "term_verbatim_json": json_cell(terms),
                "term_match_key": key[1],
                "csv_escape_applied": "true" if escaped else "false",
                "occurrence_count": str(len(records)),
                "primary_metric_text": scalar_values["primary_metric_text"],
                "blue_percentage_text": scalar_values["blue_percentage_text"],
                "direction_glyph_text": scalar_values["direction_glyph_text"],
                "metric_ui_context": "Search popularity (sampled detail)" if search_popularity_context else "list_row_unlabeled",
                "metric_semantics_status": "context_inferred_comparison_basis_unstated" if search_popularity_context else "unlabeled",
                "secondary_values_json": json_cell(secondary_values),
                "detail_scope_text": detail_scope,
                "detail_recency_text": detail_recency,
                "detail_creators_posted_text": detail_creators,
                "related_terms_json": "[]",
                "related_videos_json": "[]",
                "viewer_insights_json": "{}",
                "intent_summary_text": "",
                "source_observation_ids_json": json_cell(source_observation_ids),
                "source_evidence_ids_json": json_cell(source_evidence_ids),
                "conflict_fields_json": json_cell(group_conflicts),
                "capture_status": str(context.get("capture_status") or "failed"),
            }
        )

    with (run_dir / "terms.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TERMS_HEADERS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    secure_file(run_dir / "terms.csv")
    write_ndjson(run_dir / "conflicts.ndjson", conflicts)
    emit(
        {
            "status": "ok",
            "run_dir": str(run_dir),
            "observation_count": len(observations),
            "term_count": len(rows),
            "conflict_count": len(conflicts),
        }
    )
    return 0


def build_manifest(run_dir, run_id, observations):
    by_evidence = {}
    for record in observations:
        evidence_id = str(record.get("evidence_id") or "")
        if evidence_id:
            by_evidence.setdefault(evidence_id, []).append(record)
    structure_path = run_dir / "structure-input.json"
    structure = read_json(structure_path) if structure_path.is_file() else {}
    screens_by_id = {
        str(screen.get("screen_id")): screen
        for screen in structure.get("screens", [])
        if isinstance(screen, dict) and screen.get("screen_id")
    }
    run_input_path = run_dir / "run-input.json"
    run_input = read_json(run_input_path) if run_input_path.is_file() else {}
    context_by_evidence = {}
    for context in run_input.get("list_contexts", []):
        if not isinstance(context, dict):
            continue
        for evidence_id in context.get("evidence_ids", []):
            context_by_evidence[str(evidence_id)] = str(context.get("list_context_id") or "")
    evidence = []
    for path in sorted((run_dir / "evidence").glob("*.png"), key=lambda item: item.name):
        if not path.is_file():
            continue
        evidence_id = path.stem
        records = by_evidence.get(evidence_id, [])
        width, height = png_dimensions(path)
        contexts = sorted({str(item.get("list_context_id")) for item in records})
        captured_values = sorted(
            str(item.get("captured_at")) for item in records if item.get("captured_at")
        )
        sequences = [
            int(item.get("screen_sequence"))
            for item in records
            if str(item.get("screen_sequence") or "").isdigit()
        ]
        scroll_indices = sorted(
            {
                int(item.get("scroll_index"))
                for item in records
                if str(item.get("scroll_index") or "").lstrip("-").isdigit()
            }
        )
        structure_screen = screens_by_id.get(evidence_id, {})
        frame_match = re.fullmatch(r"frame-(\d+)", evidence_id)
        screen_state = str(structure_screen.get("state") or "")
        entry = {
            "evidence_id": evidence_id,
            "path": path.relative_to(run_dir).as_posix(),
            "media_type": "image/png",
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
            "width": width,
            "height": height,
            "captured_at": captured_values[0] if captured_values else structure_screen.get("captured_at"),
            "sequence": min(sequences) if sequences else (int(frame_match.group(1)) if frame_match else None),
            "screen_kind": screen_state.casefold() if screen_state else (
                "term_detail" if any(item.get("kind") == "detail_field" for item in records) else "list"
            ),
            "scroll_indices": scroll_indices,
            "list_context_id": contexts[0] if len(contexts) == 1 else context_by_evidence.get(evidence_id, ""),
            "observation_ids": [str(item.get("observation_id")) for item in records],
            "supporting_artifacts": [],
            "excluded": False,
            "exclusion_reason": None,
        }
        for suffix, media_type, field_name in (
            (".xml", "application/xml", "ui_tree_path"),
            (".ocr.json", "application/json", "ocr_artifact_path"),
        ):
            support = path.with_name(evidence_id + suffix)
            if support.is_file():
                relative = support.relative_to(run_dir).as_posix()
                entry[field_name] = relative
                entry["supporting_artifacts"].append(
                    {
                        "path": relative,
                        "media_type": media_type,
                        "sha256": sha256_file(support),
                        "byte_size": support.stat().st_size,
                    }
                )
        evidence.append(entry)
    for pattern in ("safe-ui-*.json", "nav-action-*.json"):
        for path in sorted((run_dir / "evidence").glob(pattern), key=lambda item: item.name):
            if not path.is_file():
                continue
            payload = read_json(path)
            evidence_id = str(payload.get("evidence_id") or path.stem)
            suffix_match = re.search(r"(\d+)$", evidence_id)
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "path": path.relative_to(run_dir).as_posix(),
                    "media_type": "application/json",
                    "sha256": sha256_file(path),
                    "byte_size": path.stat().st_size,
                    "captured_at": payload.get("captured_at"),
                    "sequence": int(suffix_match.group(1)) if suffix_match else None,
                    "screen_kind": str(payload.get("state") or payload.get("kind") or "sanitized_structure"),
                    "scroll_indices": [],
                    "list_context_id": "",
                    "observation_ids": [],
                    "supporting_artifacts": [],
                    "excluded": False,
                    "exclusion_reason": None,
                }
            )
    evidence.sort(key=lambda item: (str(item.get("path") or ""), str(item.get("evidence_id") or "")))
    return {"contract_version": CONTRACT_VERSION, "run_id": run_id, "evidence": evidence}


def sanitize_run(run_input):
    result = json.loads(json.dumps(run_input, ensure_ascii=False))
    device = result.get("device")
    if isinstance(device, dict) and "serial" in device:
        serial = str(device.pop("serial"))
        device["serial_hash"] = hashlib.sha256(serial.encode("utf-8")).hexdigest()[:16]
    statuses = [str(item.get("capture_status")) for item in result.get("list_contexts", [])]
    if not statuses or all(value == "not_attempted" for value in statuses):
        result["status"] = "partial"
    elif "blocked" in statuses:
        result["status"] = "blocked"
    elif "failed" in statuses:
        result["status"] = "failed"
    elif any(value in {"partial_interrupted", "bounded_no_endpoint"} for value in statuses):
        result["status"] = "partial"
    else:
        result["status"] = "completed"
    return result


def nested_evidence_ids(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                found.update(str(entry) for entry in item if entry)
            else:
                found.update(nested_evidence_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(nested_evidence_ids(item))
    return found


def invalid_structure_claims(value, path="structure"):
    problems = []
    if isinstance(value, dict):
        if "claim_status" in value:
            evidence_ids = value.get("evidence_ids")
            if not str(value.get("scope") or "").strip():
                problems.append(path + ": missing scope")
            if not isinstance(evidence_ids, list) or not any(str(item).strip() for item in evidence_ids):
                problems.append(path + ": missing evidence_ids")
        for key, item in value.items():
            problems.extend(invalid_structure_claims(item, path + "." + str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            problems.extend(invalid_structure_claims(item, "%s[%d]" % (path, index)))
    return problems


def derive_structure(structure):
    screens = structure.get("screens", []) if isinstance(structure.get("screens"), list) else []
    label_evidence = {}
    for screen in screens:
        for label in screen.get("observed_labels", []):
            label_evidence.setdefault(str(label), set()).update(screen.get("evidence_ids", []))
    modes = [
        label
        for label in ("All", "Content gap", "Searches by followers")
        if label in label_evidence
    ]
    if modes:
        structure["panel"] = {
            "list_modes": [
                {
                    "label": label,
                    "claim_status": "observed",
                    "scope": "captured Creator Search Insights mode controls",
                    "evidence_ids": sorted(label_evidence[label]),
                }
                for label in modes
            ]
        }
    category_order = (
        "Suggested",
        "Trending",
        "Dance",
        "Featured",
        "Food",
        "Travel",
        "Fashion",
        "Sports",
        "Hobbies",
        "Science & Tech",
        "Home & Living",
        "Education",
        "Careers",
        "Vehicles",
        "Local life",
        "Photo posts",
    )
    categories = [label for label in category_order if label in label_evidence]
    if categories:
        structure["content_categories"] = [
            {
                "label": label,
                "claim_status": "observed",
                "scope": "captured content-category selector",
                "evidence_ids": sorted(label_evidence[label]),
            }
            for label in categories
        ]
    filter_screens = [screen for screen in screens if screen.get("state") == "FILTER_SHEET"]
    if filter_screens:
        filter_evidence = sorted(
            {evidence_id for screen in filter_screens for evidence_id in screen.get("evidence_ids", [])}
        )
        visible_options = sorted(
            {
                label
                for screen in filter_screens
                for label in screen.get("observed_labels", [])
            }
        )
        visible_text_verbatim = []
        selected_labels = []
        for screen in filter_screens:
            for label in screen.get("visible_text_verbatim", []):
                if label not in visible_text_verbatim:
                    visible_text_verbatim.append(label)
            for label in screen.get("selected_labels", []):
                if label not in selected_labels:
                    selected_labels.append(label)
        structure["filter_surfaces"] = [
            {
                "context": "captured list filter sheet",
                "visible_options": visible_options,
                "visible_text_verbatim": visible_text_verbatim,
                "selected_labels": selected_labels,
                "options_coverage": "visible_sheet_only",
                "claim_status": "observed",
                "scope": "captured list filter sheet only",
                "evidence_ids": filter_evidence,
            }
        ]
        checks = structure.setdefault("requested_control_checks", [])
        if not any(item.get("control") == "High % Gap" for item in checks):
            checks.append(
                {
                    "control": "High % Gap",
                    "status": "observed" if "High % Gap" in visible_options else "not_observed",
                    "claim_status": "observed" if "High % Gap" in visible_options else "not_observed",
                    "scope": "captured list filter sheet only",
                    "evidence_ids": filter_evidence,
                }
            )
    detail_screens = [screen for screen in screens if screen.get("state") == "TERM_DETAIL"]
    detail_evidence = sorted(
        {evidence_id for screen in detail_screens for evidence_id in screen.get("evidence_ids", [])}
    )
    if detail_screens and detail_evidence:
        structure["term_detail"] = {
            "opened": True,
            "sections": sorted(
                {
                    label
                    for screen in detail_screens
                    for label in screen.get("observed_labels", [])
                    if label
                    in {
                        "Search popularity",
                        "Related videos",
                        "Viewer insights",
                        "What viewers are searching",
                        "Explore more topics",
                    }
                }
            ),
            "claim_status": "observed",
            "scope": "the sampled term detail screens only",
            "evidence_ids": detail_evidence,
        }
    ranking_evidence = sorted(
        {
            evidence_id
            for screen in screens
            if screen.get("state") == "CSI_LIST"
            for evidence_id in screen.get("evidence_ids", [])
        }
    )
    if ranking_evidence:
        structure["ranking"] = {
            "numeric_rank_observed": False,
            "basis": "first observed capture order only",
            "claim_status": "not_observed",
            "scope": "captured list rows only",
            "evidence_ids": ranking_evidence,
        }
    return structure


def build_summary(run, structure, observation_count, term_count, conflict_count):
    lines = [
        "# Creator Search Insights capture",
        "",
        "- Contract: `%s`" % CONTRACT_VERSION,
        "- Run: `%s`" % run.get("run_id", ""),
        "- Status: `%s`" % run.get("status", ""),
        "- Raw observations: **%d**" % observation_count,
        "- Deduplicated terms: **%d**" % term_count,
        "- Unresolved conflicts: **%d**" % conflict_count,
        "",
        "## Observed panel structure",
        "",
    ]
    path = structure.get("navigation_path", [])
    if not path and isinstance(structure.get("navigation"), dict):
        path = [
            item.get("label", "")
            for item in structure["navigation"].get("observed_path", [])
            if item.get("label")
        ]
    if path:
        lines.append("Navigation: " + " → ".join(str(item) for item in path))
        lines.append("")
    modes = structure.get("mode_labels", [])
    if not modes and isinstance(structure.get("panel"), dict):
        modes = [item.get("label", "") for item in structure["panel"].get("list_modes", [])]
    if modes:
        lines.append("List modes: " + ", ".join("`%s`" % item for item in modes))
        lines.append("")
    filters = structure.get("filter_surfaces", [])
    if filters:
        lines.append("Filters were recorded from the captured sheet; absence claims are scoped to that sheet only.")
        lines.append("")
    detail = structure.get("term_detail", {})
    if detail.get("opened"):
        lines.append("A sampled term detail was opened; its scope and recency apply only to that term.")
        lines.append("")
    lines.append("Rows use first-observed capture order; no numeric TikTok rank is inferred.")
    lines.append("")
    lines.append("Completeness is scoped to each recorded list context and capture window.")
    lines.append("")
    return "\n".join(lines)


def add_zip_entry(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    archive.writestr(info, data)


def add_zip_path(archive, name, source_path):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100600 << 16
    with source_path.open("rb") as source, archive.open(info, "w") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)


def command_finalize(args):
    run_dir = Path(args.run_dir).expanduser().resolve()
    if (run_dir / ".finalized.json").is_file():
        raise CliError("run_finalized", "The run has already been finalized")
    control_path = run_dir / ".control.json"
    if control_path.is_file():
        existing_control = read_json(control_path)
        if existing_control.get("finalized"):
            raise CliError("run_finalized", "The run has already been finalized")
    run_input = read_json(run_dir / "run-input.json")
    validate_contract_version(run_input, run_dir / "run-input.json")
    observations = read_ndjson(run_dir / "observations.ndjson")
    conflicts = read_ndjson(run_dir / "conflicts.ndjson")
    structure_input = read_json(run_dir / "structure-input.json")
    structure_input["contract_version"] = CONTRACT_VERSION
    structure_input["run_id"] = run_input["run_id"]
    structure_input = derive_structure(structure_input)
    list_rows = [item for item in observations if item.get("kind") == "list_row"]
    list_evidence = sorted({str(item.get("evidence_id")) for item in list_rows if item.get("evidence_id")})
    if list_rows and list_evidence:
        structure_input["row_field_presentations"] = [
            {
                "field": "primary_metric_text",
                "ui_label": None,
                "semantic_status": "unlabeled_list_metric",
                "claim_status": "observed" if any(item.get("primary_metric_text") for item in list_rows) else "not_observed",
                "scope": "captured list-row occurrences only",
                "evidence_ids": list_evidence,
            },
            {
                "field": "blue_percentage_text",
                "ui_label": None,
                "semantic_status": "unlabeled; comparison basis not stated",
                "claim_status": "observed" if any(item.get("blue_percentage_text") for item in list_rows) else "not_observed",
                "scope": "captured list-row occurrences only",
                "evidence_ids": list_evidence,
            },
        ]
    structure_input["secondary_label_vocabulary"] = sorted(
        {str(item.get("secondary_text_verbatim")) for item in list_rows if item.get("secondary_text_verbatim")}
    )

    evidence_ids = {path.stem for path in (run_dir / "evidence").iterdir() if path.is_file()}
    referenced = {str(record.get("evidence_id")) for record in observations if record.get("evidence_id")}
    missing = sorted(referenced - evidence_ids)
    if missing:
        raise CliError(
            "missing_evidence",
            "One or more canonical observations reference missing evidence",
            evidence_ids=missing,
        )
    allowed_statuses = {
        "point_in_time_endpoint_reached",
        "bounded_no_endpoint",
        "partial_interrupted",
        "blocked",
        "failed",
        "not_attempted",
    }
    contexts = run_input.get("list_contexts", [])
    if control_path.is_file() and not contexts:
        raise CliError("missing_context", "A live run cannot finalize without a captured list context")
    for context in contexts:
        status = str(context.get("capture_status") or "")
        if status not in allowed_statuses:
            raise CliError("invalid_completeness", "A list context has an invalid capture status", status=status)
        if status == "point_in_time_endpoint_reached" and not context.get("endpoint_reached"):
            raise CliError("invalid_completeness", "Endpoint status requires endpoint_reached=true")
    unsupported_claims = invalid_structure_claims(structure_input)
    if unsupported_claims:
        raise CliError(
            "unsupported_structure_claim",
            "Every structure claim must have an explicit scope and evidence",
            claims=unsupported_claims,
        )
    structural_missing = sorted(nested_evidence_ids(structure_input) - evidence_ids)
    if structural_missing:
        raise CliError(
            "missing_structure_evidence",
            "Structure claims reference missing evidence",
            evidence_ids=structural_missing,
        )

    manifest = build_manifest(run_dir, str(run_input["run_id"]), observations)
    run = sanitize_run(run_input)
    write_json(run_dir / "run.json", run)
    write_json(run_dir / "structure.json", structure_input)
    write_json(run_dir / "evidence-manifest.json", manifest)

    with (run_dir / "terms.csv").open(encoding="utf-8", newline="") as handle:
        term_count = sum(1 for _ in csv.DictReader(handle))
    summary = build_summary(run, structure_input, len(observations), term_count, len(conflicts))
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    secure_file(run_dir / "summary.md")

    zip_path = run_dir / "evidence.zip"
    temporary_zip = run_dir / ".evidence.zip.tmp"
    try:
        if temporary_zip.exists():
            temporary_zip.unlink()
        with zipfile.ZipFile(temporary_zip, "w") as archive:
            entries = [("evidence-manifest.json", run_dir / "evidence-manifest.json")]
            entries.extend(
                (path.relative_to(run_dir).as_posix(), path)
                for path in (run_dir / "evidence").iterdir()
                if path.is_file()
            )
            for name, source_path in sorted(entries, key=lambda item: item[0]):
                add_zip_path(archive, name, source_path)
        os.replace(temporary_zip, zip_path)
    except (OSError, zipfile.BadZipFile) as error:
        try:
            temporary_zip.unlink()
        except OSError:
            pass
        raise CliError(
            "packaging_failed",
            "Unable to build the evidence archive atomically",
            detail=str(error),
        )
    secure_file(zip_path)
    evidence_zip_sha256 = sha256_file(zip_path)
    run["evidence_zip_sha256"] = evidence_zip_sha256
    capture_window = run.get("capture_window")
    if isinstance(capture_window, dict) and not capture_window.get("ended_at"):
        capture_window["ended_at"] = utc_timestamp()
    write_json(run_dir / "run.json", run)
    lease_released = False
    if control_path.is_file():
        control = read_json(control_path)
        lease_released = release_lease(control, run_dir)
        try:
            control_path.unlink()
        except OSError:
            write_json(
                control_path,
                {
                    "contract_version": CONTRACT_VERSION,
                    "run_id": run_input["run_id"],
                    "finalized": True,
                    "sensitive_values_removed": True,
                },
            )
    write_json(
        run_dir / ".finalized.json",
        {
            "contract_version": CONTRACT_VERSION,
            "run_id": run_input["run_id"],
            "finalized_at": utc_timestamp(),
            "evidence_zip_sha256": evidence_zip_sha256,
            "device_lease_released": lease_released,
        },
    )
    for internal_name in ("run-input.json", "structure-input.json", "observations-input.ndjson"):
        internal_path = run_dir / internal_name
        try:
            internal_path.unlink()
        except OSError:
            pass
    emit(
        {
            "status": "ok",
            "run_dir": str(run_dir),
            "observation_count": len(observations),
            "term_count": term_count,
            "conflict_count": len(conflicts),
            "evidence_count": len(manifest["evidence"]),
            "evidence_zip_sha256": evidence_zip_sha256,
            "device_lease_released": lease_released,
        }
    )
    return 0


def build_parser():
    parser = JsonArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    preflight = subcommands.add_parser("preflight", help="Verify Android capture capabilities without input")
    preflight.add_argument("--adb-path", required=True)
    preflight.add_argument("--serial")
    preflight.add_argument("--package", choices=tuple(sorted(ALLOWED_PACKAGES)), default=DEFAULT_PACKAGE)
    preflight.add_argument(
        "--ocr-backend",
        choices=("auto", "tesseract", "vision", "accessibility"),
        default="auto",
    )
    preflight.add_argument("--json", action="store_true", help="Compatibility flag; output is always JSON")
    preflight.set_defaults(handler=command_preflight)

    begin = subcommands.add_parser("begin", help="Create a run and acquire its device lease")
    begin.add_argument("--output-root", required=True)
    begin.add_argument("--preflight-json", required=True)
    begin.set_defaults(handler=command_begin)

    inspect = subcommands.add_parser("inspect", help="Inspect and hash the current verified screen")
    inspect.add_argument("--run-dir", required=True)
    inspect.add_argument("--adb-path")
    inspect.set_defaults(handler=command_inspect)

    policy = subcommands.add_parser("policy", help="Evaluate an ADB argv or semantic intent")
    policy.add_argument("--request-json", required=True)
    policy.set_defaults(handler=command_policy)

    stop = subcommands.add_parser("detect-stop", help="Detect fail-closed screen text")
    stop.add_argument("--text-file", required=True)
    stop.set_defaults(handler=command_detect_stop)

    act = subcommands.add_parser("act", help="Execute one hash-bound, state-allowlisted action")
    act.add_argument("--run-dir", required=True)
    act.add_argument("--intent", required=True)
    act.add_argument("--screen-hash", required=True)
    act.add_argument("--adb-path", required=True)
    act.add_argument("--target")
    act.add_argument("--bounds")
    act.add_argument("--text")
    act.add_argument("--focused-control")
    act.set_defaults(handler=command_act)

    capture = subcommands.add_parser("capture-tab", help="Capture a bounded Creator Search Insights list")
    capture.add_argument("--run-dir", required=True)
    capture.add_argument("--mode", required=True, choices=("All", "Content gap", "Searches by followers"))
    capture.add_argument("--max-scrolls", type=int)
    capture.add_argument("--adb-path", required=True)
    capture.add_argument("--category", default="Suggested")
    capture.add_argument("--language", action="append", default=[])
    capture.add_argument("--filter", action="append", default=[])
    capture.add_argument("--region", default="")
    capture.set_defaults(handler=command_capture_tab)

    replay = subcommands.add_parser("replay", help="Create an offline run from saved evidence")
    replay.add_argument("--source", required=True)
    replay.add_argument("--output-root", required=True)
    replay.set_defaults(handler=command_replay)

    extract = subcommands.add_parser("extract", help="Build canonical observations and terms")
    extract.add_argument("--run-dir", required=True)
    extract.set_defaults(handler=command_extract)

    finalize = subcommands.add_parser("finalize", help="Validate and package a run")
    finalize.add_argument("--run-dir", required=True)
    finalize.set_defaults(handler=command_finalize)
    return parser


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # argparse treats a comma-delimited negative rectangle as another option.
    # Fold only this exact public option/value pair; policy still rejects it.
    for index in range(len(raw_argv) - 1):
        if raw_argv[index] == "--bounds" and raw_argv[index + 1].startswith("-"):
            raw_argv[index : index + 2] = ["--bounds=" + raw_argv[index + 1]]
            break
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    try:
        if args.command in {"inspect", "act", "capture-tab", "extract", "finalize"}:
            run_dir = Path(args.run_dir).expanduser().resolve()
            with operation_lock(run_dir):
                return int(args.handler(args) or 0)
        return int(args.handler(args) or 0)
    except CliError as error:
        return fail(error)
    except Exception as error:  # Keep the process seam machine-readable and fail closed.
        return fail(CliError("internal_error", "Unexpected failure", detail=str(error)))


if __name__ == "__main__":
    sys.exit(main())
