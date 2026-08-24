# Platform and runtime

Use the bundled CLI as the only device-action entry point. Run commands from the skill directory with Python 3.

## Preconditions

- Use Android Platform Tools `adb`; pass its exact executable path to preflight instead of assuming it is on `PATH`.
- Require one `device`-state target or an explicit `--serial`. Fail on `offline`, `unauthorized`, emulator ambiguity, or multiple unselected targets.
- Require a user-unlocked, already-signed-in device. Do not install TikTok, sign in, grant permissions, or change settings.
- Verify the keyguard is not showing and the fixed Android input binary exists without issuing an input event. Record the exact ADB path, selected serial hash, device model, Android version, TikTok package/version, dimensions, density, rotation, locale, and timestamp offsets.

## Public CLI

Use `python3 scripts/csi.py` with one of these commands. All commands emit JSON on stdout and fail closed.

```text
preflight --adb-path PATH [--serial X] [--package PACKAGE] [--ocr-backend auto|tesseract|vision|accessibility] --json
begin --output-root DIR --preflight-json FILE
inspect --run-dir DIR [--adb-path PATH]
policy --request-json FILE
detect-stop --text-file FILE
act --run-dir DIR --intent INTENT --screen-hash HASH --adb-path PATH [--target LABEL] [--bounds X,Y,W,H] [--text TEXT] [--focused-control LABEL]
capture-tab --run-dir DIR --mode MODE --adb-path PATH [--max-scrolls N] [--category LABEL] [--language LABEL]... [--filter LABEL]... [--region LABEL]
extract --run-dir DIR
finalize --run-dir DIR
replay --source DIR --output-root DIR
```

Treat a nonzero exit, invalid JSON, `allowed: false`, stale screen hash, or incomplete precondition as a stop. Do not bypass the CLI with direct `adb shell input` commands.

Permit only the CLI's exact read-only ADB templates for device enumeration/state, screenshots, UI-tree stdout, screen dimensions/density, keyguard state, fixed input-binary presence, Android version/locale, foreground window, and the allowlisted TikTok package/version. Let `act` issue only state-valid, policy-approved input after `begin`; reject arbitrary shell commands, shell escapes, package/settings changes, file transfer, installation, and raw ADB intents.

Bind the exact resolved ADB path into the preflight report. Require `begin` to validate and persist it. Require every `act` and `capture-tab` path to resolve to that same executable; let `inspect` use the persisted path when its optional argument is omitted.

Build an intent policy request from the latest inspection as `{"kind":"intent","state":"CSI_LIST","intent":"scroll_list"}` and add only applicable `target`, `[x,y,width,height]` bounds, `screen_size`, exact search `text`, or `focused_control`. Never invent or reuse a stale state. Reserve `{"kind":"adb_command","state":"PRECHECK","argv":[...]}` for auditing an exact ADB template.

## Screen reading

- Use accessibility text first on every screen.
- Use `--ocr-backend auto` to make local Tesseract available as fallback when installed; otherwise rely on accessibility. Auto never selects macOS Vision.
- Use `--ocr-backend vision` only as an explicit macOS Vision request. It becomes available only on macOS with the bundled Swift helper and local `swift`; otherwise continue only if accessibility still satisfies screen reading.
- Use `--ocr-backend tesseract` to select only local Tesseract as OCR fallback. If it is unavailable, continue only if accessibility still satisfies screen reading. Use `accessibility` to disable OCR fallback.
- Record the selected backend and preserve accessibility, OCR, XML, and screenshot provenance. Do not send screen contents to a remote OCR service.

## Lifecycle

1. Run `preflight` and save its JSON output.
2. Present the capability result to the user in commentary. Do not run `begin` until this disclosure is visible.
3. Run `begin` with the saved report only after confirming it contains the exact resolved ADB path. Let it create the run directory and acquire the device-serial lease.
4. Run `inspect` before navigation and after every state-changing view action. Use its current screen hash, safe anchors, text boxes, and verified selections.
5. Run `detect-stop` on readable screen text. Stop on any positive or uncertain result.
6. Submit each proposed action to `policy`. Execute it with `act` only when its supplied target/bounds match a fresh internal anchor and the current screen hash still matches.
7. Use `capture-tab` only from a verified top-of-list state with the requested mode visible or previously selected. Require requested language, filter, and region values to have been observed selected. Default to 100 scrolls for `All` and 300 for finite modes.
8. Inspect term-detail, scope, and recency screens to emit `detail_field` observations and structure evidence. Run `extract`, inspect conflicts and completeness, then run `finalize`; retain its `evidence_zip_sha256`.
9. Use `replay` to copy a non-symlink PNG evidence source into a new offline run. It has no hardlink option and performs no ADB or device action.

## Concurrency

- Allow only one active run lease per device serial. Stop on `device_lease_active`; do not delete the lease manually.
- Let the CLI hold a nonblocking per-run operation lock around `inspect`, `act`, `capture-tab`, `extract`, and `finalize`. Stop on `run_busy` instead of retrying concurrently.
- Finalize a live run once. Let successful finalization mark it immutable and release its device lease.

## Runtime discipline

- Keep files inside the run directory or the user-requested output directory.
- Preserve raw screenshots and UI-tree files. Create derivatives with new IDs rather than overwriting evidence.
- Keep device capture and offline extraction separable so replay is deterministic.
- Use UI-tree text when available, OCR when needed, and visual verification for ambiguous high-impact fields.
- Preserve fixed, UI-settling waits. Do not add random delays or unrelated taps.
