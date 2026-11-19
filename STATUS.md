# PFA Jar — Current Coordination Status

## 2026-10-12 — Common CPU runtime deployment completed

- User explicitly approved runtime reflection. sync-improving.ps1 -Apply deployed py/cpu_runtime.py, py/pipeline_io_bootstrap.py, py/masonjar_worker.py; all three SHA-256 hashes verified. Process inspection confirmed no target app/child was running.
- Backup: deploy-backups/20261012-110724-2d3ca08ff1664fa98124ead49eec802e.
- Deployed worker JSON protocol/max.py --help smoke passed and logged available=16 threads=8. Actual end-to-end processing timing remains user runtime validation.
- Detect source file was excluded as proposed: earlier prediction cache/performance edits and the Detect-specific removal of its one-thread override remain source-only. Current deployed Detect still explicitly sets PyTorch to one thread. Common preprocessing CPU improvements are deployed.

## 2026-10-12 — CPU utilization (Codex; source only)

- Current owner: Codex. State: ready for runtime deployment review; user requested overall processing speed improvements.
- Added py/cpu_runtime.py: automatic native thread budget min(8, available CPUs minus one), minimum one; MASONJAR_CPU_THREADS=1 restores the conservative budget. Explicit library thread environment settings are preserved. This PC reports 16 logical CPUs, automatic budget 8.
- py/pipeline_io_bootstrap.py and py/masonjar_worker.py initialize bounded CPU pools. Worker no longer eagerly imports PyTorch for every non-detection job. Detect no longer forces intra-op CPU threads to one; inter-op remains one. Large-image tile concurrency is unchanged to preserve memory limits.
- Logs record available CPUs/budget and actual Detect PyTorch threads. Existing pending detection cache/performance edits were preserved.
- Validation: 59 CPU/sharpen batch/parity tests passed; worker JSON protocol + max.py --help smoke passed; changed Python syntax passed. Repaired stale sharpen test Namespace fixture missing the existing workers CLI argument.
- Synthetic 3072x3072 Gaussian blur, five iterations: 1 thread 20.44 ms, 8 threads 6.55 ms, identical pixels. This is not an end-to-end speed claim; real CPU/GPU/NAS pipeline validation remains pending.
- Runtime NOT updated: AI_COLLAB_GUIDE.md requires explicit approval for sync -Apply. CPU files: py/cpu_runtime.py, py/pipeline_io_bootstrap.py, py/masonjar_worker.py, py/find_neurons.py. Caution: find_neurons.py also contains earlier source-only cache/performance changes, requiring py/detect_perf.py and py/detect_prediction_cache.py if deployed wholesale. Do not deploy that file alone or silently include pending unrelated changes. No commit made before user app validation.

Updated: 2026-09-13

## Project locations

- Source: `D:\Claude\masonjar-7.0.2-MC.1-improving`
- Test runtime: `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64`
- Coordination records: `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination`
- Original reference source: `D:\Claude\archive\MasonJar-7.0.2-reference\masonjar-7.0.2`
- Original reference runtime: `D:\Claude\archive\MasonJar-7.0.2-reference\masonjar-win32-x64`

## Current state

- The improving source is the authoritative working tree.
- The test runtime is current for the deployed Adjustment Viewer changes: source and runtime `py\adjust.py` SHA-256 match.
- Latest selective runtime deployment backup: `deploy-backups\20260913-192511-1e0f1fa36bef437d83ee7c4b03a67c60`.
- A full packaged release has not been created. The current runtime is a development/test runtime updated through selective, hash-verified deployment.

## Delivered improvements

### CZI import and data handling

- Plane-by-plane Z-stack TIFF writing and streaming MAX reduction reduce peak memory use.
- Exact peak scaling is retained because sampled peak modes changed brightness on real CZI data.
- Exact-output LUT conversion accelerates uint16-to-uint8 conversion.
- DAPI single-Z selection, persistent worker handling, and isolated fallback improve stability.
- Full dotted filenames are preserved for Import, MAX, Seam Correction, and Adjustment Viewer lookup.

### Seam Correction

- `Known-geometry` is preferred when seamgrid metadata is available; `Grid-estimated` is the fallback.
- Known-geometry uses local, boundary-specific brightness and transition-ramp correction.
- Grid-estimated detection includes expected-spacing support and stronger candidate validation.
- Adjustment Viewer uses live correction, retains the Seam Correction state while switching sections/channels, and displays the resolved mode.
- DAPI seam previews use caching and adjacent-slice prefetch.

### Adjustment Viewer and atlas workflow

- DAPI and annotation map render as separate layers; brush updates use bounded raster changes and NumPy-backed Undo.
- Space+left-drag and right-drag pan are supported with hand cursors; painting is disabled outside the DAPI image.
- Annotation/DAPI panes are resizable with a draggable splitter; hiding Annotation expands DAPI.
- Viewer starts maximized, restores Options-panel width, and supports vertical Options scrolling without horizontal scrolling.
- Region Picker, Paint Target, Parcellation, and tissue-cleanup controls were reorganized for compact, responsive layouts.
- Atlas preview/DAPI display and Orient title-clipping issues were addressed.

## Verification completed

- CZI streaming, two-pass, extraction integration, and downstream regression scripts passed in the configured Python environment.
- Adjustment Viewer focused pytest suite last passed: `33 passed`.
- Runtime logs reviewed on 2026-09-13 showed no fatal/error/traceback entries during the examined Viewer sessions.
- Real application import completed after DAPI crash-safety work. The rare fallback path where the first DAPI candidate fails and a later candidate succeeds has not yet been observed end-to-end.

## Remaining validation and follow-up

1. Build and test a full packaged release before external distribution. Update `package.json` to a new release version first, then run `npm run build:release:win`.
2. Perform release acceptance checks: dotted-filename CZI import, DAPI section/channel switching, both Seam Correction modes, Viewer pan/splitter/brush behavior, and tissue cleanup layout.
3. If a DAPI first-candidate crash occurs, confirm automatic fallback to the next candidate in the application import path.
4. If further brush responsiveness is needed, profile drag-frame coalescing; common dirty-region updates are fast, but occasional large-stroke spikes remain possible.

## Operational notes

- For development testing, edit source first and use `scripts\sync-improving.ps1` only after explicit approval to reflect selected supported files into the test runtime. The script creates a backup and verifies SHA-256.
- Dependency, asset, package configuration, file deletion, or distribution changes require a full rebuild rather than selective synchronization.
- This source directory is not currently a Git repository. Create a repository and first commit before continuing long-term external development.
- The old status file was preserved at `handoffs\status-snapshots\STATUS-20260913-Developer-handoff-prep.md` before this update.
## 2026-10-08 — Count Brain channel/combination isolation (Codex)

- User approved source implementation: count/starters/<prediction+alignment combination> and count/somata/<combination>.
- js/count.js determines a stable output leaf before merge planning. js/pipeline_run.js accepts an explicit output leaf, including the empty legacy-flat root, and preview planning does not persist run mode or a slice list. Count overwrite no longer reuses an unrelated active output leaf.
- js/pipeline_runs.js discovers nested Count/Collate leaves. Existing results remain untouched. Legacy flat output remains an explicit opt-out of isolation.
- Durable LOG: count.plan records selected prediction/alignment runs, output path, mode, candidate/process/skip counts and summary, including pre-launch failures.
- Tests passed: test-pipeline-run.js (8), test-pipeline-runs.js (22), test-file-index.js (10); count.js syntax passed. Updated obsolete dotted filename test expectation to preserve the full stem. Temporary directory permission failure in the broad suite was resolved by using D:\Claude\test-temp.
- Runtime reflection: NOT applied; explicit user approval required. Files to deploy: js/count.js, js/pipeline_run.js, js/pipeline_runs.js. Actual Count Brain run and Collate selection still require runtime verification.
- Other existing pending source changes were preserved: detection GPU/stage performance logs and raw-prediction cache, plus package version 1.0.2. They were not deployed by this task. Real SAHI cache parity remains unverified.

## 2026-10-08 — Count Brain runtime reflection completed

- User explicitly approved deployment. sync-improving.ps1 applied js/count.js, js/pipeline_run.js, js/pipeline_runs.js; all three SHA-256 hashes verified.
- Backup: deploy-backups\20261008-155248-15b96b0b73f54443a77854df46be5f03.
- Actual Count Brain execution with somata after starters, and Collate Counts selection, remain user runtime checks. Detection source-only changes were not included.


## 2026-11-05 — Performance logging enabled by default (source only)
- User requested perf logging for normal executable launches. src/main.ts sets MASONJAR_PERF=1 when absent; explicit MASONJAR_PERF=0 is preserved. Compiled main.js updated.
- TypeScript compilation passed. Default/explicit 0/explicit 1 and child-process environment inheritance checks passed. Existing perf_log tracemalloc behavior is retained, so profiling overhead remains the same as perf.bat.
- Runtime NOT reflected: awaiting separate approval. Deployment file: main.js. Startup resource-check hang remains unfixed and separate from this change.


## 2026-11-06 — Shared project slice order (Codex, source only)

- Current owner: Codex; next owner: Codex/user; state: source implemented, runtime reflection awaits explicit user approval.
- Scope authorized by user: Atlas Alignment, Viewer/Editor and Compare Adjacent share a persistent slice order, without renaming files.
- Atlas Alignment > Layout > Slice order… provides internal drag-and-drop, Up/Down, Reverse, natural Filename order, AP ascending (current positions, including model estimates). Cancel does not save an order. Current slice stays selected after reordering.
- Canonical record: <project>/.masonjar/slice_order.json, full slice IDs. Atomic replacement; an optimistic stale-dialog check rejects a changed on-disk order. Hidden subset IDs retain their slots; newly discovered files append in natural order. Missing/corrupt order falls back to natural sorting with a diagnostic log; corrupt files are not overwritten by the editor.
- py/map.py uses project order for navigation and Finish processing. Existing per-file AP, angles, labels and outputs remain attached by filename. Reorder pauses automatic AP extrapolation to preserve tuned positions; explicit Force AP step on Next still works in the new order. Order/AP lock saved in the alignment session. New order is consumed by Viewer/Editor/Compare Adjacent upon reopening (already-open Viewers are not rearranged).
- py/slice_index.py applies the order to matched Viewer image/annotation pairs, so Compare Adjacent's offsets follow the same list. Fixed remaining first-dot truncation in image/annotation/prediction parsers and Atlas slice IDs to avoid collapsing dotted filenames into one ID.
- Files to deploy together: py/map.py, py/slice_index.py, py/slice_order.py (new), py/slice_order_dialog.py (new). Test-only file: python/tests/test_slice_order.py.
- Verification: 63 pytest tests passed (slice order including offscreen Qt controls and real controller action via AST, slice index, adjust pairing, align session, AP extrapolation and prediction/autosave); four product files py_compile passed. No live project was reordered by tests. Actual application UX and reopen checks remain user verification.
- Runtime reflection: NOT applied. No rebuild/ZIP/commit performed. Previous unrelated pending changes preserved, including perf-default main.js and detection/CPU work.

## 2026-11-12 — PFA runtime separation from Mason Jar (source only)

- Current owner: Codex; state: implementation/test complete, runtime reflection awaits explicit user approval.
- Confirmed failure in runtime logs masonjar-20261112_120412.log and _115923.log: ~/.masonjar/benv/sitecustomize.py imports Mason Jar's runtime guard, which exits when PFA Jar starts Python. Both apps currently point at ~/.masonjar. Updating packages is a failed Python launch, not merely a slow download.
- Source now uses <current user's home>/.pfajar via main.ts and js/branding.js; no hardcoded username. Renderer paths resolve through js/home_dir.js. Project .masonjar/.belljar metadata, project filenames and existing analysis outputs are unchanged.
- First-run migration offers data-only copies from ~/.masonjar or ~/.belljar. Only models/embeddings/nrrd/manifest.json are eligible; Python, benv, sitecustomize, runtime guard and settings are not copied. Existing destination entries are retained; copies stage into temporary directories before rename. Source files are read-only.
- Python interpreter/venv are freshly installed into .pfajar. Bootstrap pip and venv commands use quoted absolute interpreter paths; child environments remove PYTHONHOME/PYTHONPATH/VIRTUAL_ENV and disable user site-packages. PFAJAR_HOME is supplied to child processes. Python dialog prefs and alignment shortcut files use .pfajar, and CLI SAM defaults use .pfajar.
- Reworked nested bootstrap resource promises so failures propagate. Downloads handle HTTP/request/stream errors and idle timeout. Resources extract to staging and replace only after success; manifests record successful downloads. Rollback failures retain the backup. Cached data can be reused on fresh environment installation. Bootstrap failures now log and show an error instead of remaining on Updating packages.
- Preserved existing generated-main indexMetadataResult error reporting by bringing its missing TypeScript equivalent into src/main.ts before compilation (no intended new metadata behavior).
- Checks passed: tsc compilation, test-home-dir.js (including data-only migration, original guard preservation and project-format assertions), test-runtime-isolation.js (10 checks including pip failure propagation, new environment commands, cache reuse, local tar success/failure, HTTP/connection failure), test-dialog-preferences.js, main.js syntax and touched Python py_compile. test-update-manager.js reports OK but Windows CIM subprocess checks were restricted in sandbox. Tests used D:\Claude\test-temp and did not install packages or touch the actual user runtime.
- README, publish-release user-home text and developer path examples updated. test-runtime-isolation.js added to test:js.
- Runtime reflection: NOT applied. No real ~/.pfajar environment has been created, no ~/.masonjar files were edited/deleted, no rebuild/ZIP/commit. First install on the executable still needs verification. Startup may need significant disk space and time for a new interpreter/packages and duplicated model data.
- Core deployment files: main.js, js/branding.js, js/home_dir.js, py/dialog_preferences.py, py/align_shortcuts.py. Source map.py/find_neurons.py CLI defaults were updated too, but those files contain earlier pending slice-order/detection changes and require their dependencies if deployed. Earlier pending perf-default main.js change is also in generated main.js. Review this combined scope before reflection; do not deploy the earlier pending files blindly.

## 2026-11-12 — Explicit startup error UI and automatic log opening (source only)

- User requested that an error never leave Updating packages looking active, and that the relevant error log be shown automatically.
- reportBootstrapFailure now records environment, command/error details and captured stderr/stdout/stack; emits bootstrapFailed; force-opens the log window and focuses it after the error dialog (normal logs still do not steal focus).
- Loading page stops/hides the spinner, displays Setup failed — updating has stopped, shows plain-text error details, and provides Show error log. Late progress messages cannot overwrite a fatal error state. No silent retry; restart after resolving the problem.
- Fixed log queue flushing while the log page is loading: error lines remain buffered until IPC listeners are ready, avoiding an empty error-log window.
- Verification: TypeScript compile passed; test-runtime-isolation.js now passes 11 checks including automatic log opening and deferred error-queue delivery; test-loading-failure.js passes spinner, failed state, details, stale progress and log button checks. Test added to test:js. Actual Electron window behavior remains user runtime validation.
- Runtime NOT reflected; deploy together with the pending .pfajar environment changes, plus js/loading.js and pages/loading.html. Existing user environments untouched. STATUS backup created before this update.

## 2026-11-12 — Environment isolation/error UI reflected in runtime

- User explicitly approved runtime reflection. sync-improving.ps1 applied 7 files and verified SHA-256: main.js, js/branding.js, js/home_dir.js, js/loading.js, pages/loading.html, py/dialog_preferences.py, py/align_shortcuts.py.
- Backup: deploy-backups/20261112-124153-44fab678b8ab44a89dff9cf8a7b229f1. Process check required sandbox escalation for Windows CIM and completed successfully before deployment.
- Runtime now selects ~/.pfajar and enables perf by default (earlier main.js change included). Existing ~/.masonjar and projects were not changed. No real .pfajar setup was run by Codex; next user launch should offer Copy model/atlas data, then install fresh Python/packages.
- Restored only the development test:js command registration to match runtime package.json: package/dependency metadata is identical. Standalone test-home-dir.js, test-runtime-isolation.js, test-loading-failure.js remain available; earlier statements saying new tests were registered in test:js are superseded.
- Source-only changes still pending: editable slice order and detection profiling/cache, plus CLI SAM default path updates inside map.py/find_neurons.py. These were not deployed as part of this approved focused change. Main application supplies runtime model paths explicitly.
- Actual first install, normal menu entry, Python tool launch and error-window UX remain user runtime checks. No full rebuild/ZIP/commit.

## 2026-11-12 — Live package installation details (source only; active install untouched)

- User authorized implementation/testing while their current package installation runs. Runtime reflection is deliberately deferred until installation completes and the app is closed, followed by explicit approval.
- Added js/package_install.js: shared pip runner for first-install and subsequent dependency checks. Uses absolute Python executable and separate argv with spawn(shell:false), unbuffered stdout/stderr, incremental UTF-8 decoding, plain-text/ANSI-cleaned lines, 200ms batching, one-second process heartbeat, bounded UI/error tails (150 lines) and durable per-line main log output.
- main.ts/main.js route both setupEnvironment and updatePythonDependencies through installPackages. Existing requirements, venv isolation, cache policy difference and fatal-error handling retained; no actual pip command executed by tests.
- Loading page shows current output/activity, elapsed and last-output times, scrollable recent lines and running/completed/failed state. Quiet periods >=15s explicitly say the process is still running, without claiming a hang or guessing overall progress percentages. Earlier bootstrapFailed UI keeps precedence over delayed progress.
- Verification: TypeScript compile; test-package-install.js (pre-close streaming, partial UTF-8, stdout/stderr, success/nonzero/spawn failures, quiet heartbeat, batching bounds, literal argv); test-loading-progress.js; test-loading-failure.js; test-runtime-isolation.js (11 checks, now verifies both routes use the same runner); JS syntax and focused diff whitespace checks all passed.
- Deployment files when approved: main.js, js/package_install.js (new), js/loading.js, pages/loading.html. No package/dependency metadata change. Tests are standalone scripts, not added to package.json.
- No executable/runtime file was changed, no current install was interrupted, and no user environment was modified. Actual display during a real installation remains a post-deployment user check.

## 2026-11-12 — PFA Jar 1.0.3 selective runtime update completed

- User approved runtime reflection as version 1.0.3. Verified no PFA/Mason/Python installation processes were running before mutation; deployment script rechecked app/child processes immediately before applying.
- Source package.json and package-lock.json root versions set to 1.0.3. Runtime package.json, PFA Jar.exe FileVersion and ProductVersion all verified as 1.0.3. No Electron/dependency version changes.
- Reflected files: main.js, js/package_install.js (new), js/loading.js, pages/loading.html, runtime package.json and executable version resources. All six deployed hashes verified. Backup: deploy-backups/20261112-132236-11de898310de4f2aaca4d6ec28bcc7f5.
- sync-improving.ps1 now supports explicit -UpdateVersion: it rejects package changes beyond version, stages/validates Windows version metadata with bundled rcedit, and backs up both executable and app files before applying. Existing dependency/build changes still require full build review. Version-only acceptance/rejection tests passed.
- Before deployment, test-package-install, test-loading-progress, test-loading-failure and test-runtime-isolation (11 checks) passed. Runtime module load verified without starting installation. Actual UI during next package check remains user acceptance.
- This is a selective update of the existing executable, not a full source rebuild. Pending slice-order and detection/cache source changes were not included. Previously deployed .pfajar separation, error log opening and perf-default remain active. No ZIP generated, no Git commit/push, no install started by Codex and no user environment changed by this deployment.

## 2026-11-13 — PFA Jar 1.0.3 source staging prepared (no commit/push)

- User explicitly asked Codex to select changes already reflected in executable 1.0.3 and stage them. 34 whole files plus only the deployed revision of py/find_neurons.py are staged (35 total). Working source files were hash-checked unchanged during staging.
- Partial staging uses the deployed find_neurons.py blob directly in the index; current source retains pending cache/profiling changes. py/map.py and py/slice_index.py match runtime HEAD, so their pending slice-order changes are not staged. New detect_perf/cache and slice_order modules/tests remain unstaged.
- Included: runtime isolation, live package details/error UI, perf default, channel-aware Count planning, already-deployed CPU runtime changes, metadata 1.0.3, related docs/maintenance scripts and tests. Local coordination files AI_COLLAB_GUIDE.md and STATUS.md remain unstaged.
- 17 staged product files compared with deployed runtime: exact normalized text parity, except js/dialog_preferences.js has only a user-home comment update. Staged runtime code has no imports of pending detect cache or slice order modules.
- Validation ran against a snapshot extracted from the index, not the mixed working tree: test-home-dir, test-package-install, test-loading-progress, test-loading-failure, test-runtime-isolation (11 checks), test-file-index (10), test-pipeline-run (8), test-pipeline-runs (22), test-dialog-preferences; all passed. CPU Python tests: 3 passed. git diff --cached --check passed.
- Snapshot: verification/staged-1.0.3-20261113-164710. Index backup and staged plan JSON: handoffs/staging-1.0.3/. No runtime file/user Python environment changed, no commit/tag/push/ZIP.
- Next: user review staged changes, then authorize commit/push. Do not run git add . or git add py/find_neurons.py before committing; that would add unreflected work. git status displays MM for the partially staged file by design.
- Prepared index tree: bc034ac2cbe58267ef12eacd014fe5703892c33b


## 2026-11-13 — Startup package log presentation (Codex; source only)

- Current owner: Codex; next owner: user review; state: source tests passed, runtime deployment approval pending.
- Normal pip `Requirement already satisfied` messages were flooding the loading page and the separate log window. Package checks now use short friendly activity summaries. Raw output remains available in collapsed Technical details and the session log file; user-facing log window receives start/completion/failure summaries only.
- Failed pip exit still opens technical details; existing bootstrap failure dialog/log-window behavior is preserved. Warnings on stderr alone are not treated as failures. No dependency installation policy or package versions changed.
- Files: src/main.ts, compiled main.js, js/package_install.js, js/loading.js, pages/loading.html; tests scripts/test-package-install.js and scripts/test-loading-progress.js.
- Validation: TypeScript compilation; package streaming, loading progress, loading failure, runtime isolation (11 checks) passed. No pip/network/user runtime touched. Targeted whitespace check passed. Existing unrelated source edits preserved.
- Runtime/release staging/ZIP NOT updated: explicit deployment approval required. Deploy main.js, js/package_install.js, js/loading.js, pages/loading.html together after approval, restart app and confirm normal/error presentation.
- STATUS files backed up before this append in coordination/handoffs/startup-log-TIMESTAMP (timestamped directory for this operation).

## 2026-11-13 — Startup package log presentation deployed

- User approved reflection and confirmed app closed. Process guard passed; sync-improving.ps1 -Apply deployed main.js, js/package_install.js, js/loading.js, pages/loading.html with syntax checks and SHA-256 verification (4 files).
- Runtime backup: deploy-backups/20261113-172206-bf48ffdbfba54b109ca9bbbbd4c04c95.
- State: runtime updated; user startup UI verification pending. Normal package output is summarized, raw output remains in collapsed technical details/session file, and real failure reporting is retained.
- Release staging, Desktop copies, ZIP and GitHub assets were not updated. No app was launched automatically.

## 2026-11-13 — Startup log UI confirmed; Desktop copy updated

- User confirmed normal log presentation in the designated D: runtime, then explicitly requested the same update for the Desktop copy.
- Deployed main.js, js/package_install.js, js/loading.js, pages/loading.html to C:/Users/mirih/Desktop/PFA-Jar-1.0.3-win32-x64/PFA-Jar-1.0.3-win32-x64/resources/app. Same sync script logic with a temporary target override; process guard, syntax checks, backups and all four SHA-256 verifications passed. Temporary script removed; permanent sync target unchanged.
- Backup: deploy-backups/20261113-172423-994296736ba44dcaa21982b3f888b9d1. Desktop startup verification remains pending; ZIP/GitHub assets unchanged.

## 2026-11-17 — Interactive Cell Detection Viewer (Codex; source implementation complete)

- Current owner: Codex. Next owner: user deployment review. State: source implementation and automated checks passed; runtime reflection approval pending.
- User requested an Adjustment-Viewer-like detection workflow and a launch button below Back to workspace under Start > Workspace > Cell detection. Added Open Cell Detection Viewer there, visible only in the detection category.
- Independent Electron window with signal branch/dataset picker, input/output folder selection, Somata/Nuclei/custom model, channel/plane selection, Previous/Next/slice list, zoom/pan and BBox visibility. Detection settings collapse after detection to expose live filters.
- Persistent supervised Python child performs CLAHE + existing YOLO/SAHI tiled inference for the current full-resolution plane. Default candidate confidence floor 0.10, live confidence 0.50, minimum area 200 px2, minimum eccentricity 0.20, intensity cutoff 0. Model and candidate measurements are reused; raw candidate cache is JSON keyed by source/model identity, plane, tile, floor, device/library versions and batch engine code hash. Normal filter changes do not rerun inference. Lowering the candidate floor requires a new detection run.
- Area upper-outlier rule, eccentricity and intensity-p90 behavior match the existing filtering rules on the cached candidates. Inference at a lower confidence followed by SAHI merging and live confidence filtering is not claimed pixel/BBox-identical to a separate batch inference at a higher confidence. Actual tissue accuracy/threshold selection remains user validation.
- Save atomically writes a Count-Brain-compatible Predictions_<full dotted slice ID>.pkl and per-slice detection_live_settings.json. Saved filter settings restore for matching inference settings. Default output is a separate uniquely named detection run via existing signal-aware path resolver; a user-selected output is retained. Same slice ID from different source/plane is rejected in a shared live output. Existing result replacement asks first. Unsaved navigation/close asks first; closing kills the child and restores main-window focus.
- Asynchronous request handling serializes work, validates image tokens, rejects stale requests, clears failed image/detection state, locks save during incomplete/invalid filters, prevents editing during save, and reports errors. Inactive worker releases I/O fairshare between operations. Stage/tile progress and operation timings reach logs.
- Verification: TypeScript compilation and JS/Python syntax passed. Seven Python regression tests passed in existing .pfajar Python (including cache reuse, batch-filter comparison, channel selection, stale tokens, atomic-write failure preservation, dotted names, empty detections and a real Count Brain subprocess counting one synthetic ACAd cell). Host regression passed sender guard/serialization/failure/close cleanup. Hidden Electron UI smoke passed folder load, detection, live BBox/count changes with one inference, invalid numeric input recovery, save and pane layout. Existing menu-category and file-index tests passed.
- Real YOLO/SAHI protocol smoke on a temporary 256x256 synthetic image using existing chaosdruid.pt passed on cuda:0 (14 candidates, 0 retained/saved at test filters). This checks execution, not biological accuracy. No real project data used. No dependencies installed. Temporary diagnostics/screenshots are under D:/Claude/test-temp/detect-live-ui-*.
- Reflect these NINE files together after explicit approval: main.js, js/menu_category.js, pages/menu_category.html, js/detect_live_host.js, js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_core.py, py/detect_live_worker.py. src/main.ts is compiled into main.js. No find_neurons.py or its pending detect_perf/cache changes need to be deployed for this feature; the viewer uses helpers already present in the deployed batch module.
- Tests added: scripts/test-detect-live.py, scripts/test-detect-live-host.js, scripts/smoke-detect-live.js. Existing unrelated pending changes retained. No runtime/Desktop/release ZIP reflection and no commit/push performed.

## 2026-11-17 — Detection calibration defaults to one slice; optional all-slice application

- User prioritized finding accurate parameters on one slice before applying them to the dataset. Detect current slice remains the default and is the only inference launched by that button. Added separate Apply current settings to all slices button, enabled only after a valid calibrated detection/filter result.
- Whole-folder scope means all currently listed images in the selected signal dataset/input folder, using the selected channel/plane. Confirmation lists image count, model, confidence, area, eccentricity, intensity, tile, candidate floor and new output path. It runs only after confirmation, with a frozen settings snapshot.
- Results are saved into a distinct new *_all run to preserve the calibration outputs. Raw measurements reuse the calibration output cache, including the already-tested slice. Each image is loaded/detected/saved serially through the same worker and filter code, retaining bounded one-plane working memory and model reuse.
- Added Stop after current slice, per-slice failure continuation, completed/failed counts and atomic live_batch_summary.json. Current slice/preview/pan/zoom/filter choices are restored after completion or stopping. Whole-run controls and navigation lock while active. Duplicate normalized slice IDs are rejected before starting.
- Validation: hidden Electron smoke passed default single-slice behavior, fixed filters across two-slice application, current-slice restoration, stop-after-current behavior, partial save failure continuation and summary counts. Existing live filtering, invalid input recovery and saving checks still passed. Expected injected save-failure stderr is part of this passing test. JavaScript syntax passed. Python unchanged from previously passing seven tests.
- Changed js/detect_live.js, pages/detect_live.html, css/detect_live.css and scripts/smoke-detect-live.js. Runtime reflection remains pending explicit user approval for the complete nine-file viewer deployment listed above. No runtime or release assets changed.

## 2026-11-17 — Cell Detection Viewer Go to

- Added Go to… beside Previous/Next. Opens a modal section-number input with the current position, valid 1–N range, Enter submission, Cancel and Escape. Uses the existing loadSlice navigation including unsaved-change confirmation and busy/bulk locks. Numbers refer to positions in the displayed sorted slice list.
- Updated pages/detect_live.html, js/detect_live.js and css/detect_live.css. Hidden Electron regression passed invalid-section rejection, section 2/section 1 navigation, existing detection/filter/save/all-slice/stop/partial-failure flows. JS syntax passed. Runtime deployment approval remains pending for the complete viewer feature.

## 2026-11-17 — Cell Detection Viewer runtime deployment

- User explicitly approved runtime reflection and then confirmed PFA Jar closed. Process guard passed. sync-improving.ps1 -Apply deployed all nine viewer files and verified SHA-256 hashes after syntax checks.
- Files: main.js, js/menu_category.js, pages/menu_category.html, js/detect_live_host.js, js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_core.py, py/detect_live_worker.py.
- Runtime backup: deploy-backups/20261117-182931-4a2a0d1de32e4c458c2b8fb6688c47ab. Source pending find_neurons.py/cache/performance and slice-order changes were excluded.
- State: deployed to D:/Claude/masonjar-7.0.2-MC.1-improving-win32-x64; user real-tissue UI/parameter validation pending. Launch Start > Workspace > Cell detection > Open Cell Detection Viewer (below Back to workspace). Default detects current slice only; separate all-slice confirmation, stop-after-current and Go to included.
- Desktop copies, release ZIP and GitHub assets not updated. No app launched automatically. STATUS files backed up before this append.

## 2026-11-17 — Detection Viewer immediate output default (source only)

- User reported blank Predictions output folder on opening. Previously generated only after Load folder. Now initializes immediately after project/dataset restoration using the project predictions role, selected signal branch and a unique live run name.
- Default refreshes for input dataset/model changes and loading a folder while preserving a manually selected output. Legacy mode uses the workspace predictions preset, then an input-relative live_predictions folder, then a home/PFAJar/predictions fallback when no input exists. Merely displaying a default creates no directories.
- Changed js/detect_live.js and extended scripts/smoke-detect-live.js. Hidden Electron smoke passed immediate absolute output default, no premature folder creation, manual output preservation and existing detection/navigation/filter/bulk/stop/failure tests. JS syntax passed.
- Runtime update pending explicit approval; deploy only js/detect_live.js after approval. Other viewer runtime files remain deployed from the previous step.

## 2026-11-17 — Empty section selector fixed (source only)

- Root cause: initial viewer restored an input folder but did not populate files until Load folder was clicked; section select rendered an empty enabled dropdown. Now automatically loads the restored input folder on opening, and automatically loads an accepted input/branch/dataset change. This loads previews only, never auto-starts cell detection.
- Empty or unreadable folder clears stale files/index/preview, shows No sections loaded and disables the section selector/navigation. Busy/bulk states block overlapping folder loads. Existing unsaved-change confirmation remains on dataset changes.
- Changed js/detect_live.js, pages/detect_live.html, scripts/smoke-detect-live.js. Hidden Electron regression passed auto-population from restored workspace, no auto-inference, switching to an empty folder and recovering on a valid folder, plus existing calibration/Go to/filter/bulk/stop/failure flows. Test capture moved under artifacts/ so its PNG is not incorrectly treated as an input slice.
- Latest runtime logs also contain a separate plane-index validation error: a one-plane image was requested at a nonzero index. For single-plane grayscale input, Channel / plane index must remain 0; sections are selected via the header selector/Go to.
- Runtime pending explicit approval: deploy js/detect_live.js (includes the earlier immediate default-output fix) and pages/detect_live.html together. STATUS backups created before this append. No runtime files changed.

## 2026-11-17 — Resizable Detection Viewer options panel (source only)

- User requested left/right dragging to resize the right options panel. Added an 8 px separator between preview and options with horizontal resize cursor, pointer capture, cancellation cleanup and keyboard Left/Right support. Initial width remains 340 px; minimum normally 220 px, maximum preserves 180 px of preview plus separator. Width clamps when the window resizes.
- Canvas resize now preserves zoom and the image coordinate at viewport center instead of refitting the image on every sidebar/window resize. Narrow controls wrap within the scrollable panel; fieldset min-width reset prevents intrinsic-width clipping.
- Changed pages/detect_live.html, css/detect_live.css and js/detect_live.js. Hidden Electron regression passed pointer-event-driven panel expansion, minimum-width clamp, unchanged zoom and image-center position, plus earlier auto-section, output-default, filtering/save/bulk/navigation tests. Hidden-window pointer capture is stubbed in this test because synthetic events lack an OS pointer; real mouse interaction remains user acceptance after deployment.
- Runtime pending explicit approval. Deploy js/detect_live.js, pages/detect_live.html, css/detect_live.css together; this includes earlier pending default-output and automatic-section-loading fixes. STATUS backed up before append. No executable or runtime files changed.

## 2026-11-17 — Detection Viewer single-channel preprocessed inputs (source only)

- User confirmed that the viewer targets channel-separated, already-preprocessed/MAX images. Removed Channel / plane index from the UI, single/bulk requests and all-slice confirmation. Signal branch/dataset/input folder chooses the channel.
- Worker validates one image series and one non-spatial plane before reading TIFF data, accepts singleton extra dimensions, and rejects multi-plane/channel files with a clear request for a single-channel MAX/preprocessed image. PNG/JPEG must decode as 2D grayscale. Legacy nonzero plane requests also receive a clear error. Internal plane=0 remains in existing cache/settings identities to preserve earlier single-plane caches and saved filters.
- Changed pages/detect_live.html, js/detect_live.js, py/detect_live_worker.py. Updated regression scripts. All seven Python tests passed, including multiple-plane rejection and real Count Brain consumption; Electron smoke passed no plane selector, load/filter/save/bulk/navigation/auto-sections/splitter behaviors. JS syntax passed. Expected injected save-failure stderr belongs to the passing smoke test.
- Runtime pending explicit approval for FOUR files: js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_worker.py. Includes earlier pending output default, auto-section loading and resizable sidebar changes. STATUS backed up before append. No runtime or release updates performed.

## 2026-11-17 — Detection Viewer WinError 3 during cache rename (source only)

- User reported Detect current slice failing on Y:/Matt_Jacobs/.../05_predictions/somata/<long-run>. Session log masonjar-20261117_183024.log confirms YOLO inference completed (1053 tiles, approximately 190 s), then measurements completed and os.replace(temp, cache.json) failed. Total failed detect request about 204.7 s.
- Path.resolve converted the mapped output drive to its UNC canonical path. Normalized UNC output directory is 195 characters, temporary cache path about 224, SHA256 cache target 282 (> classic Windows MAX_PATH). Failure occurred at the optional cache rename, not YOLO inference.
- detect_live_core.windows_io_path adds extended-length Windows drive/UNC prefixes for I/O. Atomic writes now use prefixed target, parent and temporary paths. Worker reads cache/settings with the helper and preserves the user's mapped output path via abspath rather than resolve. Final pickle/metadata atomic writes also receive long-path support.
- Cache write OSError is logged as cache_write_warning; completed candidate records remain available for live filtering and final saving. Viewer displays a warning while keeping detection successful. Actual prediction save failures still surface as failures.
- Verification: 8 Python tests passed, including real >260-character local paths created/replaced/read, UNC prefix/idempotence, injected cache rename failure followed by filtering and final save, prior Count Brain compatibility. Hidden Electron smoke passed all existing Viewer workflows. Actual SMB share write was not tested or modified.
- Changed py/detect_live_core.py, py/detect_live_worker.py and js/detect_live.js plus tests. Runtime reflection still requires explicit approval. Deploy FIVE pending files together: js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_core.py, py/detect_live_worker.py. Includes earlier output-default/section/splitter/single-channel changes. No runtime, Desktop or release assets changed. STATUS backed up before append.

## 2026-11-17 — Detection Viewer fixes deployed

- User approved reflection and requested shutdown confirmation. Target app/child process guard passed on retry. sync-improving.ps1 -Apply deployed five files with syntax checks and SHA-256 verification: js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_core.py, py/detect_live_worker.py.
- Backup: deploy-backups/20261117-185858-1013c115f00d4267a2649415fbde3a99.
- Includes immediate default Predictions output, automatic section loading, resizable options panel preserving image zoom/center, removal of channel/plane selector with single-plane input validation, extended-length Windows/UNC output I/O and nonfatal candidate-cache write failures.
- State: reflected in the designated D: runtime; user real-server Detect current slice verification pending. Prior failed inference did not persist candidates, so that slice needs one new detection run. Desktop copies/ZIP/GitHub assets unchanged. STATUS backed up before append; app not launched automatically.

## 2026-11-17 — Original input image toggle (source only)

- User requested an original image toggle. Added Original image beside BBoxes, with pressed-state color. Default remains the CLAHE detection preview; enabled toggle shows the chosen preprocessed input before CLAHE. This is the selected input image, not a separate original CZI. Uint16/normalized float input uses the same linear 8-bit display conversion as detection preprocessing.
- Worker returns two bounded previews (max edge 2048) from one source read, retaining only detection grayscale at full resolution. Toggle switches decoded previews in the renderer, preserving BBoxes, zoom, pan, filters and saved predictions, with no backend request/reinference. Selection carries across sections. Disabled while no original preview is loaded.
- Added preview=False to worker loads used by bulk application/calibration restoration, avoiding unnecessary encoding/transfers for off-screen slices.
- Nine Python tests passed, including exact original-versus-CLAHE preview content, skipped bulk encoding, long paths/cache failure and real Count Brain compatibility. Electron smoke passed pixel changes on toggle, exact return to processed+BBoxes, unchanged view transform and no worker calls, plus previous viewer workflows. JS syntax passed. Expected injected save failure is part of the passing smoke test.
- Changed js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_worker.py plus regression scripts. Runtime reflection pending explicit approval for those four files. STATUS backed up before append. No executable, Desktop or release assets changed.

## 2026-11-17 — BBox hover measurements and exclusion guidance (source only)

- User requested hovering over a cell BBox to learn what filter values would exclude it. Added a pointer tooltip showing candidate ID, model confidence, BBox area px2, measured eccentricity and CLAHE intensity p90, alongside current cutoffs.
- Suggestions respect backend comparisons: confidence and intensity must exceed the measured value, while minimum area/eccentricity may equal it. Suggested values use safe precision and valid bounds. Missing measurements are not offered as exclusion filters. Confidence=1 and p90=255 explicitly cannot be excluded by that respective threshold alone. Guidance states that global cutoffs affect other candidates and real cells.
- Uses cached candidate measurements only. Displayed/retained BBoxes are hit-tested in original-image coordinates with zoom-aware tolerance, preferring the smaller overlapping box. Pointer work is requestAnimationFrame-throttled. Tooltip hides on pan/redraw, pointer leave, hidden BBoxes, invalid/pending filters and ongoing worker/bulk work.
- New js/detect_live_info.js plus edits to js/detect_live.js, pages/detect_live.html, css/detect_live.css. Unit tests passed precision/equality/maximum/unknown rules and overlapping-box hit testing. Hidden Electron smoke passed hovering BBox #2, expected thresholds, hide on BBox toggle and zero backend calls for hover, alongside all previous workflows.
- Pending runtime approval for FIVE files together: js/detect_live_info.js (new), js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_worker.py. Includes pending Original image toggle from prior request. No executable/runtime/release assets changed. STATUS backed up before append.

## 2026-11-17 — Detection Viewer measurable progress and worker heartbeat (source only)

- User could not distinguish Detect current slice processing from a freeze. Existing installed SAHI 0.11.36 get_sliced_prediction does not expose the progress_callback used conditionally by the batch helper, leaving long inference silent.
- Added serialized structured progress events (request ID, stage, current/total, elapsed, time since actual advancement) and one-second worker heartbeats. Protocol writes are locked; heartbeat is stopped/joined before final reply. Host forwards matching progress without resolving the outstanding operation and ignores stale IDs.
- Viewer has a sticky visible processing card with current stage, elapsed time, indeterminate progress for unknown-duration stages, measured tile/measurement fractions when available, worker-response recency and last actual progress age. It reports missing worker responses explicitly instead of declaring the job healthy. Completion/failure stops timers. Filter-only operations do not flash/reset the card.
- Stages cover input read, CLAHE, preview, detector preparation/model load, candidate cache read, inference, final image pass/merging, candidate measurements, cache write and final result saving. Fractions are per-stage, not invented whole-operation percentages.
- Tiled inference progress counts actual perform_inference completions using the same SAHI slice geometry (10% overlap), with emissions rate-limited to 0.3s. Restores original model method even on error; full-image pass is retained and covered by final image pass/merging stage. No detection settings changed.
- Validation: 11 Python tests passed including heartbeat idle-age/no false advancement/shutdown, unchanged inference calls/return/method restoration, previous save/cache/Count Brain cases. Host tests passed progress forwarding without finishing requests. Electron smoke passed visible 1/4 (25%) progress, worker response and completion plus existing Viewer workflows. Actual chaosdruid.pt/CUDA protocol smoke on a synthetic 700x700 image confirmed tile count 4, final progress 4/4, postprocess/measurements stages and 33 progress messages during the operation. No real project data used.
- Changed py/detect_live_worker.py, js/detect_live_host.js, js/detect_live.js, pages/detect_live.html and css/detect_live.css plus regression scripts. Pending deployment approval for SIX files together: those five plus new js/detect_live_info.js for the previously pending BBox hover feature. Also includes pending Original image toggle. No runtime, Desktop, ZIP or GitHub updates. STATUS backed up before append.

## 2026-11-18 — Detection Viewer section sorting and manual order (source only)

- User requested filename default plus AP sorting and manual order. Added Section order controls with Filename, AP ascending, AP descending, Custom and Edit…. Natural filename order remains the fresh default. Chosen mode is remembered per project (legacy per input folder).
- AP positions are read from the project's DAPI alignment_session.json by full slice ID (or filename with the existing dotted/OME stem rules). These are saved atlas AP positions and may include estimated positions; no conversion to bregma mm or unsaved alignment values is claimed. Missing AP sorts last, with filename tie-breaking. AP modes disable when no current input slices have saved AP; note shows coverage and source tooltip.
- Edit modal supports drag/drop, Up/Down, Save and Cancel. Reuses existing py/slice_order.py schema and save_order merge/optimistic-conflict behavior: project .masonjar/slice_order.json; legacy fallback input/.pfa_viewer_slice_order.json. Hidden IDs from other signal subsets are retained. Corrupt files are reported/preserved, stale concurrent edits rejected and every visible slice must occur exactly once.
- Sorting remaps the current selection by filename, preserving displayed image, BBoxes, parameters, worker token and dirty state without reloading/reinference. Previous/Next/Go to and all-slice processing follow the selected order. Reopen restores the chosen mode/custom order. No image/annotation/AP values or filenames are modified.
- Implementation: new js/detect_live_order.js; edited js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_worker.py. Worker adds order_info/order_save using existing slice_index helpers and slice_order module. Existing pending map.py/slice_index.py/slice_order_dialog.py edits are excluded from future deployment.
- Validation: all 13 detect-live Python regressions passed in existing .pfajar environment, including AP metadata/zero/missing/dotted IDs, hidden subset preservation, conflict and corrupt-file checks. New JS sort unit passed. Host regression and Electron UI smoke passed AP current-image preservation, no worker calls for sorting, manual drag/Up/Down save, custom reopen and prior detection/progress/filter/bulk cases. Existing slice-order core tests: 10 passed, 2 Qt tests deselected. An initial full dev-venv run had those 2 failures solely because qtpy is absent there; new Viewer uses Electron and its GUI was tested. No dependency installs. Syntax passed.
- Runtime approval pending for EIGHT files together: js/detect_live_order.js (new), js/detect_live_info.js (new), js/detect_live.js, js/detect_live_host.js, pages/detect_live.html, css/detect_live.css, py/detect_live_worker.py, py/slice_order.py (existing source-only shared helper, now needed). Includes pending Original image toggle, BBox tooltip and progress/heartbeat. py/detect_live_core.py long-path fix is already deployed. Source/runtime slice_index both already expose natural_sort_key and required stem functions, verified before integration.
- STATUS backed up before append. No runtime/Desktop/ZIP/GitHub update or commit/push. Real project AP availability and mouse reordering remain user acceptance after deployment.

## 2026-11-18 — Save / Discard / Cancel before section navigation (source only)

- User requested Save in the existing section-change discard/cancel prompt. Added an accessible modal with Save, Discard and Cancel. Previous/Next/Go to/dropdown/input-folder changes use the shared asynchronous leave check.
- Save awaits the existing current-slice save operation and moves only on success. Save failure or overwrite refusal retains the current section, preview, candidate state and unsaved filters. Discard explicitly clears the dirty state and moves without saving. Cancel/Escape preserve the current section. Save disables until detected candidates and filters are valid, and updates if a pending filter check completes. Duplicate prompt entry is guarded.
- save() now returns success/failure, including overwrite cancellation, so navigation can wait for persistence. Existing explicit overwrite confirmation and Count Brain output format are retained. This change targets section navigation; the separate whole-window close confirmation is unchanged.
- Changed js/detect_live.js, pages/detect_live.html and css/detect_live.css; regression expanded in scripts/smoke-detect-live.js. Electron smoke passed Cancel/no load, Save error/no movement, successful Save-before-load ordering, Discard/no save, Save disabled without detected candidates and Escape cancellation, plus previous full workflows. JS syntax passed. Injected save errors in stderr are expected test cases.
- Runtime reflection pending explicit approval for the complete EIGHT-file bundle recorded in the prior section (includes pending original toggle, BBox hover, progress and ordering). STATUS backed up before append. No runtime/Desktop/ZIP/GitHub changes.

## 2026-11-18 — Section ordering moved to standalone Atlas alignment tool (source only)

- User requested section ordering outside Cell Detection Viewer. Added Align Sections Order (test) above Align Sections under Start > Workspace > Atlas alignment. It opens an independent window with DAPI preview, filename/AP ascending/AP descending/custom order, draggable section list, Up/Down, Save and Reload.
- Removed section sort/editor controls and dialog from Cell Detection Viewer. Detection now automatically follows the saved project slice_order IDs (filename order if none), including Previous/Next/Go to and whole-folder application. Full filename IDs keep image/detection/AP associations stable. Reopen or Load folder is needed to pick up externally changed order in an already-open detection window.
- Standalone defaults to the project's DAPI input folder (legacy workspace DAPI fallback). It uses saved AP metadata from alignment_session.json, including estimates, missing AP last. Shared .masonjar/slice_order.json is saved using existing subset-preserving/optimistic-conflict helper. For a user-selected folder outside the active project, uses that folder's metadata rather than the active project's order. Invalid reload restores the previous input context.
- Window host now registers two isolated windows/IPC routes with per-window supervised workers. The order window accepts only order_info/order_save/order_preview; it cannot start cell detection. Source preview reader was extracted without changing detection preprocessing. Order preview loads raw DAPI/2D TIFF thumbnails without CLAHE or model imports.
- Reviewed existing source-only py/map.py/slice_index.py integration: Align Sections loads saved project order and locks AP extrapolation when ordering changes; Viewer/Editor/Compare Adjacent use saved ID order through build_adjust_pairs. Their earlier pending modifications are ordering plus full dotted-ID preservation. Include these with the shared helper/dialog at deployment so independent order applies to alignment as intended.
- Validation: 14 live-viewer Python tests passed in .pfajar (new raw order preview test included). Standalone Electron smoke passed DAPI selection, AP sort preserving selected filename, drag/Up/Down, Save, external-write conflict preservation, Reload/reopen and no inference actions. Detection Electron smoke passed absence of embedded sorting controls and shared-order navigation plus prior workflows. Host guard/isolation, menu placement, sorting and BBox unit checks passed. Existing slice_order and slice_index suites: all 21 tests passed, including Qt cases, using existing .pfajar Qt and development pytest via a process-local sys.path append, offscreen, no package installation.
- Initial unscoped-temp dev test attempt hit test temp cleanup/project-discovery errors; rerunning with D:/Claude/test-temp and fairshare disabled passed. Expected injected save/conflict stderr belongs to passing UI tests. No actual project files were touched by tests.
- New files: js/section_order.js, pages/section_order.html, scripts/smoke-section-order.js. Edited js/menu_category.js, js/detect_live_host.js, js/detect_live.js, pages/detect_live.html, css/detect_live.css, py/detect_live_worker.py and tests.
- Runtime approval pending for FOURTEEN files together: js/menu_category.js, js/section_order.js, pages/section_order.html, js/detect_live_order.js, js/detect_live_info.js, js/detect_live.js, js/detect_live_host.js, pages/detect_live.html, css/detect_live.css, py/detect_live_worker.py, py/slice_order.py, py/slice_order_dialog.py, py/slice_index.py, py/map.py. Existing main.js already calls registerDetectionViewer, which now registers both windows, so no additional main change is needed. Exclude pending find_neurons.py/detect_perf/cache changes.
- Includes earlier pending original-image toggle, BBox hover, progress/heartbeat and navigation Save/Discard/Cancel. STATUS backed up before append. No runtime/Desktop/ZIP/GitHub update or commit/push. Real project menu/preview/shared-order acceptance remains after approved deployment.


## 2026-11-18 — Section Active state and synchronized horizontal reflection (source only)

- User confirmed: inactive sections must be excluded from all subsequent analysis stages; left/right reflection must apply together to DAPI, analysis channels and annotations. No runtime reflection/rebuild approval was given for this task.
- Standalone Align Sections Order (test) now includes selected-section Active checkbox, Inactive/Mirrored list markers and Flip selected section left/right. Inactive sections remain visible/reactivatable in the order editor. Save order / active states preserves full IDs and hidden sections, checks stale metadata, and retains flip orientation. Saved order/active state changes require reopening other viewers to rebuild their navigation lists; server-side detection/save also rechecks eligibility.
- Shared project .masonjar/section_settings.json schema: {version:1, sections:{fullID:{active:bool,flip_x:bool}}}; default active=true, flip_x=false. Legacy standalone folder uses .pfa_section_settings.json for Active state, but physical flipping requires a project. Invalid metadata and pending flip journals block processing instead of silently including sections. Fixed root discovery so nested annotation-leaf .masonjar metadata cannot shadow the project policy/order.
- Active exclusion integrated in common JS planning (including explicit overwrite slice IDs), batch detect/QC/count/intensity readiness, Python slice-list/Viewer pairing and direct readers for MAX, preprocess inputs (Sharpen/Top-hat/BaSiC), Seam Correction, Align Sections, detection, Count Brain, intensity, parcellation and tissue cleanup apply. Batch with no active intersection skips rather than dropping the filter and scanning all files. Collate recomputes per-section CSV blocks when inactive IDs exist so previously generated Totals do not re-include them; unsupported old per-section formats fail with a rerun instruction. Existing files are retained, not deleted. Import raw CZI is not edited.
- Physical reflection plans exact full filenames (not s1/s10 prefixes) across project data images, original Z-stack TIFF pages, annotation arrays, full-detail parcellation restore arrays, prediction arrays/BBox PNGs, archived keep masks and cleanup draft masks. Scores/dimensions retained; BBox x endpoints reflect in pixel-centre coordinates. Source CZI files and immutable backup folders are not reflected. Unknown matching data formats and image/annotation role paths outside project data/ are refused before any replacement; this first implementation intentionally requires the standard project layout.
- Flip transaction stages all replacements with originals retained under .masonjar/section_flip_backups/<timestamp-uuid>. File size/mtime/policy snapshots are rechecked before replacing anything. Atomic section_flip_pending.json journal blocks analysis during commit. Exceptions restore original files; an interrupted commit offers backup recovery on reopening the order editor. Original backups remain after success. Electron supervisor requires other Python windows/jobs to close, prevents new jobs during reflection/recovery, and holds the gate until a killed child has actually exited. This gate covers application-managed processes, not arbitrary independently started external tools.
- Atlas session restoration mirrors template/labels and old damage masks as needed, caches include orientation, and inactive saved AtlasSlice entries are not rehydrated. New CZI extraction honours saved flip_x for image/MAX/preview pixels. Geometry history records file-specific flip operations; CZI re-extraction publishes reset events for replaced files so seam-grid orientation does not accumulate obsolete transforms. Existing detection candidate caches invalidate by source mtime; live detection rejects on-disk image changes until reload.
- Validation passed: 10 new temporary-project policy/flip tests (dotted IDs; nested leaf metadata; explicit/unfiltered reader exclusion; old CSV Totals; TIFF pages/pixels; annotation/BBox/keep-mask/full-detail restore reflection; two-flip restoration; stale/unsupported preflight; injected commit failure rollback and interrupted recovery; new CZI orientation; Atlas cache orientation; seam-history reset). Existing 14 live-detection Python regressions passed, 21 slice-order/index pytest cases passed incl Qt, 4 prediction-cache tests passed. File-index tests now 11 passing including inactive overwrite/all-inactive/pending/corrupt cases. Host tests passed gate denial/release and delayed child-exit release. Independent Electron UI smoke passed Active save/reload and flip review/apply wiring with mocked backend plus existing order/sort/drag/conflict tests; Cell Detection Viewer Electron smoke passed previous workflows. Existing CZI extract integration and streaming tests passed. TypeScript compilation, changed Python AST/JS/PowerShell syntax and git diff whitespace checks passed. Expected injected-save/conflict stderr belongs to passing tests. No real sample was transformed and no claim of user visual acceptance is made.
- New source helpers: py/section_policy.py, py/section_flip.py, js/section_policy.js. Source scripts/sync-improving.ps1 now permits only two additional compiled root files, python_job.js and batch_queue.js, for this feature's supervisor/batch integration. No -Apply invocation occurred.
- Complete pending runtime file list is scripts/section-order-runtime-files.json (33 files; includes previous pending standalone viewer/original toggle/hover/progress/save-navigation bundle). main.js/python_job.js/batch_queue.js require app restart. IMPORTANT: find_neurons.py already had source-only perf/cache changes before this task; adding Active filtering/full dotted IDs requires deploying that file with detect_perf.py and detect_prediction_cache.py together. Those earlier changes were retained, not silently discarded; their cache unit tests and existing live-inference/filter regression passed. Review the whole pending bundle before explicit user-approved deployment. Development tests/docs/manifest are not runtime files.
- Execution: source only; runtime/Desktop/releases/GitHub untouched; no ZIP, commit or push. STATUS copies backed up separately before append; their different prior histories are intentionally preserved.

Current handoff checkpoint: current owner Codex; next owner user/Codex; status ready for deployment review. Work completed: Active exclusion and synchronized reflection source/tests. Next caution: close analysis windows, obtain explicit runtime approval, then use the pending manifest; test on a disposable project copy before reflecting real data. Runtime reflected: No, explicit user approval pending.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\section-active-flip-20261118-121054


## 2026-11-18 — Shared section order and Active propagation across analysis stages (source only)

- User reiterated that section order/Inactive must propagate to Align Sections, Viewer/Editor, Cell Detection and other analysis stages. Clarification received: DAPI/somata/starter all use the same filename 001; prior example 001v was not an actual naming mismatch. No alias stripping or filename renaming is needed or implemented. Settings are per full section ID across all channel folders, not per TIFF Z plane.
- Reviewed existing order/eligibility consumers. Found and fixed gaps: common JS processing IDs and explicit planRun lists now use saved project order as well as Active filtering; common Python active_ids/active_files apply saved order after filtering; detection no longer re-sorts files after applying policy; Count Brain reapplies policy/order after its filename-sorting pair helper; cleanup apply jobs follow ordered active IDs; cleanup draft restoration refreshes the current project list instead of resurrecting stale inactive/missing IDs or an old order. Selected cleanup section is retained by ID when possible.
- Default/no saved order preserves each caller's previous ordering. Saved IDs take priority; newly discovered sections append naturally, and missing channel subsets are not invented. No filenames, AP positions, labels or previously generated results are rewritten by order/Active changes. Align Sections and Viewer/Compare Adjacent already use shared ID ordering; Cell Detection Viewer uses order_info and shared IDs. Previously open windows require reopen/reload to rebuild navigation; running jobs retain the list they started with. Parallel jobs can finish out of submission order. Collate remains aggregate arithmetic rather than section navigation, and keeps prior inactive-exclusion behavior.
- Tests: 11 policy/flip regression cases passed, including new three-branch DAPI/somata/starters test: saved 003/001/002 plus Inactive 001 yields 003/002 for direct image listing and Viewer pairs in every branch; reactivation restores 003/001/002; AP metadata bytes remain unchanged. File-index suite now 12 passed with shared-order/Inactive assertions for align/adjust/detect/count/intensity/MAX/sharpen/top-hat/seam/cleanup/parcellation and explicit unsorted overwrite inputs. Pipeline-run suite 8 passed; slice-order/index pytest 21 passed; live-detection regression 14 passed. Modified JS syntax and diff whitespace checks passed. Tissue cleanup draft guard was reviewed in source; real-app acceptance remains pending deployment.
- Changed source: js/section_policy.js, js/file_index.js, js/tissue_cleanup_wizard.js, py/section_policy.py, py/find_neurons.py, py/count.py, py/tissue_cleanup.py, plus tests/manifest. Complete pending runtime manifest scripts/section-order-runtime-files.json now has 34 entries (adds js/tissue_cleanup_wizard.js to prior bundle). Previous source-only detection perf/cache dependencies still require bundled review as recorded above.
- STATUS source and coordination histories backed up separately before append. Source only: no runtime reflection, real project mutation, ZIP, commit or GitHub change. Await explicit user approval for runtime deployment; test shared navigation/Inactive behavior after reopening the relevant viewers.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\shared-section-order-20261118-121530


## 2026-11-18 — Automatic saved BBox/parameter restoration on section navigation (source only)

- User requested saved BBoxes and applied parameters to load automatically when navigating after Apply current settings to all slices. Before this change, load cleared candidates and saved parameters were only read by Detect; Apply All also kept the calibration output field instead of selecting its newly saved run.
- New py/detect_live_saved.py stores a self-contained immutable content-hashed JSON snapshot in each selected output run's .live_saved/ directory. It includes all candidate boxes/scores/area/eccentricity/intensity, kept indices, normalized filter parameters, candidate floor/inference settings, source path/size/mtime/dimensions and SHA-256 of the Count Brain-compatible prediction pickle. detection_live_settings.json references the snapshot only after pickle and snapshot write success. Superseded snapshots are removed best-effort only after metadata commit, so repeated saves do not accumulate old successful snapshots indefinitely. Inference candidate cache remains optional and separate.
- Worker load accepts the selected output run and restores a matching snapshot without importing/running YOLO for restoration. Renderer restores saved BBoxes, filters, candidate floor, tile/model path, closes Detection setup and leaves dirty=false. Exact model paths are retained; Somata/Nuclei labels are retained only if they resolve to that same model. Live filter changes use all saved candidates, including candidates previously rejected, with the usual confidence-floor restriction.
- Successful Apply All selects its new output run. Calibration restoration uses its saved snapshot and retains displayed image/pan/zoom. Subsequent Previous/Next/dropdown/Go to loads from that run with no Detect call. If a stopped/failed batch did not save the current calibration section, existing cache-based calibration recovery remains and unsaved state is retained; missing results are not shown as successful saved data. Other saved sections remain usable.
- Safety: mismatched branch/source path, changed image, corrupt/missing candidate snapshot/pickle, incomplete save or parameter/pickle mismatch yields an explicit warning and no stale BBoxes/parameters. Never infer automatically to conceal a miss. Zero detections are a valid saved result. Legacy results saved before this snapshot format need one Detect + Save (or a new Apply All run) to enable complete automatic restoration; filtered pickles alone cannot recover rejected candidates/measurements. Reopening a viewer can restore when the user selects the same saved output folder; no claim is made that a fresh viewer's new-run default discovers an old run automatically.
- Validation: new six Python saved-navigation regressions passed: different parameters per dotted-ID section, all-candidate re-filtering and no inference, new worker reopening, valid zero detections, no writes on restore, changed source/other channel rejection, changed or missing pickle rejection, interrupted metadata commit rejection, legacy/missing result warnings and safe superseded-snapshot cleanup. Existing 14 live-detection Python tests passed, including Count Brain output compatibility. Updated Electron smoke passed Apply All -> output-run switch -> Next/Previous automatic BBoxes/intensity restoration and no extra Detect; calibration restoration now needs no fourth Detect (only initial + two bulk calls). Previous progress/hover/resize/save/discard/cancel/stop/partial-failure flows still passed. Host/BBox unit tests, JS/Python syntax and diff whitespace passed. Expected injected failures in smoke stderr are test fixtures, not actual project failures.
- Changed runtime sources: js/detect_live.js, py/detect_live_worker.py, new py/detect_live_saved.py; test updates are development only. Complete pending manifest scripts/section-order-runtime-files.json now has 35 entries (includes earlier pending shared-order/Active/flip work). Need include new helper at approved deployment. No dependencies added.
- STATUS source/coordination backed up separately before append. Runtime/Desktop/real projects/ZIP/GitHub unchanged; no commit or push. Explicit runtime reflection approval remains pending. User acceptance after deployment: Apply All, navigate away/back, confirm per-section saved BBoxes/parameters without another detection, including a zero-cell section.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\detection-saved-navigation-20261118-122318


## 2026-11-18 — Viewer settings transfer to existing Cell Detection menu (source only)

- User clarified desired workflow: send values tuned in Cell Detection Viewer to the existing Cell Detection menu and run there. Added Use settings in Cell Detection… next to the live-save/apply controls. Enabled only with valid calibrated candidates/filters and no pending Viewer operation. It sends the current exact input folder, predictions output base (not the calibration run leaf), exact model path/model kind, tile, final Confidence, Area, Eccentricity and Intensity cutoff. The independent Viewer remains open and unsaved calibration is retained.
- Supervised host validates payload and sender; main window navigates to detect_wizard.html with a one-shot settings payload, restores/focuses main window, and refuses replacing the main page while other Python analysis jobs/windows are active. No detection is launched by handoff. Existing Next — Process performs normal validated run planning/output generation/analysis. User can review input/output/run mode before starting. Single-channel mode is explicit and legacy flat-output is disabled for the transfer.
- Wizard applies values after workspace presets and dataset picker initialization. Matches the signal dataset when catalogued; otherwise shows Viewer input (exact folder) rather than silently switching input to a default. Rejects a project-context mismatch and disables Next on failed transfer. Opens Advanced settings to make values visible. Transfer query is removed from browser history after successful application so revisiting does not silently reapply an old payload.
- Important semantics: Candidate confidence floor is a Viewer-specific candidate collection setting; existing Cell Detection uses the final Live Confidence as its YOLO confidence. Exact parameter transfer does not claim byte-identical BBoxes because changing candidate threshold can affect model/NMS candidates. This distinction appears in the transfer notice. The performance suggestions from the preceding read-only review (GPU tile batching/preprocess cache) are not implemented by this workflow change.
- Fixed existing detect_common parser ignoring valid Viewer endpoint values: Area 0, Eccentricity 0/1 and Confidence 1 now survive parameter parsing rather than reverting to defaults. Other manual-input/default behavior retained.
- Tests passed: new handoff unit test covers exact fields, endpoint/zero filters through real parseDetectParams, single-channel/non-flat defaults, invalid values and stale project; expanded host test covers sender guard and busy-operation rejection. Hidden Viewer UI smoke verifies actual transfer button/payload and no new detection, alongside all previous save/restore/filter/progress/bulk/stop/navigation/resize cases. Hidden existing wizard smoke verifies exact form values, no auto-run, real Next -> runDetection IPC args (including zeros/Confidence 1/custom model), new output under chosen base, preset/dataset override protection, signal branch selection and stale-project blocking. Real detection IPC is captured, no Python job or real project is started by tests. Existing pipeline-run 8 tests passed. TypeScript compilation and JS syntax passed; diff whitespace check passed. Expected save-error smoke stderr belongs to injected tests.
- Changed runtime sources: js/detect_live.js, js/detect_live_host.js, pages/detect_live.html, css/detect_live.css, src/main.ts -> main.js; new js/detect_handoff.js; js/detect_wizard.js, js/detect_common.js, pages/detect_wizard.html. Tests are development-only. Complete pending runtime manifest scripts/section-order-runtime-files.json now has 39 entries including previous pending work. No dependencies/version changes.
- STATUS histories backed up separately before append. Source only; runtime/Desktop/GitHub/real projects untouched; no -Apply, ZIP, commit or push. Explicit runtime approval remains pending. After approved deployment/restart, user acceptance: calibrate a section, Use settings in Cell Detection, review values/input channel/output/run mode, Next — Process.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\viewer-to-detection-settings-20261118-125645


## 2026-11-18 — Deployment approved; performance experiments deferred; waiting for app closure

- User reports five-hour usage remaining below 15%. Explicitly deferred new performance logging plus same-section tile-batch 1/2/4 comparison until later, and approved deploying all other completed updates. Do not start the deferred benchmark or new optimization implementation during this deployment. This is a five-hour limit report, not evidence of the separately defined long-term <10% handoff threshold.
- Deferred task for next session: add separate timings for input read, uint16 conversion, CLAHE, preview/BGR, SAHI slicing/model execution/full-image pass/merge, candidate measurements and output; log actual device, tile count, batch size, GPU memory and cache hit/miss. Then compare tile batches 1/2/4 on the same processed section/model/tile size 640/confidence/settings with model warmup and inference cache bypass, preserving overlap/full-image/merge behavior. Compare elapsed time, peak memory, BBox coordinates/scores/counts and downstream filter output. Existing installed SAHI hardcodes one tile per batch, so batching requires a tested adapter rather than merely changing an exposed setting. Keep Tile size changes separate, as small-cell sensitivity can change. No benchmark/code for this deferred item has been added.
- Prior read-only log evidence: completed normal detection sections M457_s001..s011 total 1868.194s; inference-labelled interval 1493.8376s (79.96%), input read 22.142s (1.19%), remainder 352.2144s. Representative Viewer fresh-detection 31 triples, log lines 900..1195: load/preprocess/preview 1050.7277s, detect 4747.2183s, save 2.5768s. Load is not yet phase-separated, so do not claim all of it is CLAHE. Current GPU utilization snapshot cannot establish historical device use. No GPU benchmark was run.
- Completed deployment scope remains scripts/section-order-runtime-files.json, 39 files: shared ordering/Inactive/flip, pending Viewer original/hover/progress/save navigation, saved BBox/parameter restoration, and Viewer-to-existing-Cell-Detection settings transfer, with required helpers and compiled main/python supervisor/batch files. Existing completed detect_perf/cache helpers are dependencies; they are not the newly deferred instrumentation/batch experiment. TypeScript compilation succeeded and read-only deployment preview found 39 changed/new files; package/dependencies/version unchanged.
- Initial -Apply stopped at Windows CIM query restriction before writes. Retried the same authorized script with system-query access; it correctly stopped before writes because target PFA Jar processes plus masonjar_worker.py are still running. Confirmed target executable path D:/Claude/masonjar-7.0.2-MC.1-improving-win32-x64/PFA Jar.exe. No user process was killed, no runtime files copied, no deployment backup created yet. Asked user to finish work and close main/analysis windows. This was an application-running guard, not an automatic-approval rejection.
- Next action after closure: reuse existing explicit deployment approval; run sync-improving.ps1 with the 39-file JSON manifest and -Apply (system-query access may be needed), then verify SHA-256 for all files and record backup/outcome. Do not ask deployment permission again. Do not package a ZIP or update Desktop/GitHub. Runtime acceptance remains user-run after restart.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\approved-deploy-waiting-for-close-20261118-125906


## 2026-11-18 — Approved 39-file section/order/Viewer bundle deployed

- User renewed instruction to reflect the executable. Reused explicit approval; no new implementation or deferred performance experiment was started. sync-improving.ps1 completed target-process closure checks, source JS/Python syntax checks, backup, copy and SHA-256 verification. All 39 selected files were applied successfully to D:/Claude/masonjar-7.0.2-MC.1-improving-win32-x64/resources/app.
- Deployment backup: D:/Claude/masonjar-7.0.2-MC.1-improving-win32-x64_coordination/deploy-backups/20261118-171710-a504f908fb874a7f91d944904936c774. Independently rehashed every file against source after deployment: 39/39 match. Confirmed existing runtime cpu_runtime.py and detect_live_core.py dependency files are present. Scope recorded by scripts/section-order-runtime-files.json. Version remains 1.0.3; selected-file reflection, not full packaging/rebuild.
- Includes prior completed standalone Align Sections Order, shared section ordering/Inactive exclusion across same-ID channels, synchronized reflection and recovery guard, pending detection Viewer original/hover/progress/save-navigation changes, automatic saved BBox/parameter restoration after Apply All, and Viewer -> existing Cell Detection settings handoff, with required supervisor/batch/cache/perf helpers. New proposed phase-log instrumentation and tile-batch 1/2/4 comparison remain deferred exactly as requested; do not treat the deployed earlier perf helper as completion of that deferred work.
- STATUS histories backed up separately before this append. No program launched by deployment, no sample/project files transformed, no ZIP/Desktop/GitHub/commit/push updates. Source/runtime selected scope now synchronized. Runtime user acceptance remains: restart PFA Jar, verify section order/Inactive on disposable project, Viewer saved-result navigation and Use settings in Cell Detection transfer. Physical reflection should first be tried on a disposable project copy; transaction backups are created only when the user actually applies a flip.

Current checkpoint: runtime reflected Yes (39 files, hashes verified); next step user application verification after restart; performance logging and tile-batch benchmarks explicitly parked for later usage window.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\deployed-section-viewer-20261118-171745


## 2026-11-18 — Cell Detection Viewer menu placement and 1.0.4 prepared; waiting for closure

- User approved moving Open Cell Detection Viewer from below Back to workspace to immediately above Cell Detection, renaming it Cell Detection Viewer, and reflecting executable version 1.0.4. Original/preprocessing image toggle interpretation is explicitly undecided; no image-display behavior change made.
- Source js/menu_category.js now uses the common action button renderer with Cell Detection Viewer first in detection tools; old special footer-handler block removed. pages/menu_category.html footer button removed to avoid duplication. Source package.json version and package-lock.json root/root-package versions updated from 1.0.3 to 1.0.4; deep comparison confirmed only those version fields changed, not dependencies. Dependency package versions containing 1.0.3 are intentionally unchanged.
- Menu-order regression assertions and JS syntax passed; deployment preview accepts version-only package difference. Existing selected-file update supports executable Windows FileVersion/ProductVersion via rcedit staging, without full repackaging or dependency changes. Planned invocation after closure: sync-improving.ps1 -Files 'js/menu_category.js','pages/menu_category.html' -UpdateVersion -Apply. Includes package.json plus staged PFA Jar.exe version metadata. Source package-lock is for source/development packaging and not a runtime dependency update.
- Actual approved -Apply was attempted with Windows process-query access and stopped before backup/copy because target PFA Jar or child processes are still running. Runtime remains 1.0.3 and old menu placement; prior 39-file deployment remains in place. Asked user to close app/analysis windows. No user process killed. Reuse existing approval after closure; do not ask deployment permission again. After success verify menu file hashes, package version and executable FileVersion/ProductVersion 1.0.4; append outcome and backup path.
- STATUS source and coordination backed up separately before append. No ZIP/GitHub/Desktop changes. Performance phase logging and tile-batch 1/2/4 experiments remain deferred; no new implementation of them.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\version-104-waiting-for-close-20261118-180354


## 2026-11-18 — Menu placement and version 1.0.4 deployed

- User confirmed application closure. Completed previously approved sync-improving.ps1 -Files js/menu_category.js,pages/menu_category.html -UpdateVersion -Apply. Four files applied and SHA-256 verified: menu JS, category HTML, package.json and staged PFA Jar.exe version resource update. Deployment backup: D:/Claude/masonjar-7.0.2-MC.1-improving-win32-x64_coordination/deploy-backups/20261118-180438-f96d36564b1540cbb7cbcdb6328dbce3.
- Independently confirmed runtime/source hashes for both menu files and package.json, runtime package version 1.0.4, and Windows executable FileVersion and ProductVersion both 1.0.4. This is selected-file reflection plus version-only executable metadata update, not full packaging. Dependencies unchanged; source package-lock root version is 1.0.4 as recorded previously.
- Workspace Cell detection now lists Cell Detection Viewer directly above Cell Detection. Previous footer Open Cell Detection Viewer removed. Previous 39-file feature bundle remains applied. Original image interpretation and phase-log/tile-batch performance experiments remain undecided/deferred respectively; no extra work added.
- STATUS histories backed up separately before append. No app launched, no sample data changed, no ZIP/Desktop/GitHub/commit/push updates. User should reopen PFA Jar and verify new menu placement and version display.

Current checkpoint: runtime reflected Yes; product/app Windows version 1.0.4 verified; next step user visual acceptance.

STATUS backup: D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination\handoffs\deployed-104-20261118-180503
