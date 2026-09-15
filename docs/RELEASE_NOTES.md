# Mason Jar release notes (human-facing)

Copy for GitHub releases and suggested git commits. **Newest version at the top.**

Do not put file paths, test names, or IPC implementation details here—keep release notes readable for lab users.

---

## v7.0.2-MC.1

**What's new**

- **Safe custom-build update policy:** Automatic and mandatory update checks are disabled. Manual checks use the Mason Jar custom-build repository, so this build cannot be replaced by an upstream release at startup.
- **More reliable CZI import:** Large Z-stacks use less peak memory, DAPI extraction handles single-plane inputs more safely, and filenames containing periods work throughout the import workflow.
- **Improved Seam Correction:** Known-geometry correction is used when tile-grid data is available, with Grid-estimated correction as the fallback. Viewer previews run live and retain the selected correction state while you navigate.
- **Faster, clearer Viewer/Editor interaction:** DAPI and annotations render as separate layers, brush editing and Undo are more responsive, pan is available with mouse gestures, and Annotation/DAPI panes can be resized.
- **Refined alignment and cleanup tools:** Atlas/DAPI preview behavior, Options-panel navigation, region controls, and tissue-edge cleanup layout were improved.

**Commit subject**

Integrate 7.0.2-MC.1 import, seam, and viewer improvements

**Commit body**

Improve CZI streaming and filename handling, refine live seam correction, and update Viewer/Editor, Atlas, and tissue-cleanup workflows.

---

## v7.0.2

**What's new**

- **BaSiC Preview works again:** Preview correction no longer fails immediately with a Python exit error. The preview accepts the same ROI arguments as Sharpen/Top-hat.
- **Tune pan/zoom matches other preprocess tools:** The preview window uses the visible viewport (not a fixed corner crop). After Preview, pan/zoom clears the corrected ROI and restores navigation so a new Preview loads the new region—same behavior as Sharpen.

**Commit subject**

Fix BaSiC preview ROI and Tune viewport pan

**Commit body**

Accept Sharpen-style preview CLI flags, fit BaSiC on DAPI PNGs, and reuse the shared preprocess viewport ROI contract so pan no longer slides a fixed crop.

---

## v7.0.1

**What's new**

- **BaSiC wizard finds DAPI and max after CZI import:** Channel discovery now uses the open project bundle path (same as Sharpen), so Proceed no longer reports empty datasets when files are present.
- **Leave Tune:** Back to attribution, Back to preprocessing, and Cancel (workspace) are available on step 1.
- **Completed tasks tracking:** Successful BaSiC runs correctly update project processing state for the workspace hub.

**Commit subject**

Fix BaSiC wizard channel discovery and Tune exit

**Commit body**

Use getBundleRoot for discovery like other preprocess wizards, add Tune leave links, and persist processing.basic on the real project object.

---

## v7.0.0

**What's new**

- **BaSiCPy Shading Correction:** New Image preprocessing wizard corrects uneven illumination per channel using the BaSiCPy package (MIT). Attribution screen → tune each channel → process with resume if a run was interrupted → summary. Writes corrected signal leaves under max datasets (`…/basic/…`), DAPI copies in `00_dapi_basic`, and low-res `*_basic` previews for viewers. Align warping still uses uncorrected `00_dapi`.
- **Viewer toggles:** Viewer/Editor and Align Napari can show BaSiC-corrected previews when available (display only).
- **Completed tasks:** Workspace hub lists BaSiC outputs after a successful run.
- **Batch:** Shading correction is available as a batch step (depends on max projection).

**Credits**

- Algorithm: Peng et al., Nat Commun 8:14836 (2017). Software: BaSiCPy (peng-lab), MIT License. Mason Jar does not run MATLAB BaSiC.

**Commit subject**

Add BaSiCPy shading correction wizard and viewer toggles

**Commit body**

Ship BaSiCPy-based per-channel shading correction with attribution, resume/repair, project tracking, Adjust/Napari display toggles, and batch integration. Align warp remains on uncorrected DAPI.

---

## v6.0.36

**What's new**

- **Hierarchy follows region pick:** Right-clicking a labeled area (or committing a Search match) now switches Hierarchy — or CCF Level in advanced mode — to a list that contains that region, and selects it in the Area dropdown. Paint target and Hierarchy stay in sync; the mixed-resolution warning still only appears when you change Hierarchy yourself.

**Commit subject**

Sync Hierarchy to the region you pick in Viewer/Editor

**Commit body**

When right-click or Search selects an atlas label outside the current Hierarchy tier, switch to the finest matching tier (or CCF level) without firing the mixed-tier dialog, then select that region in the Area list.

---

## v6.0.35

**What's new**

- **Right-click region pick in Viewer/Editor:** Clicking a labeled area now keeps that region as the paint target even when Hierarchy is on a coarser tier (for example Functional areas while the annotation still has cortical layers). Previously the pick could snap back to the first region in the Hierarchy list.

**Commit subject**

Fix right-click region pick in Viewer/Editor

**Commit body**

Stop the Area hierarchy rebuild from overwriting a right-clicked atlas label with the first region in the current tier list when that label is outside the list.

---

## v6.0.34

**What's new**

- **Don’t show again on the post-Save Isolate Regions warning:** After saving annotations with mixed or mismatched label resolution, the Isolate Regions notice can be dismissed permanently (restore under **Settings → Dialogs**). Dock warnings still appear.

**Commit subject**

Let users dismiss the post-Save Isolate Regions warning

**Commit body**

Add Don’t-show-again for the Viewer/Editor notice that appears after Save when label resolution may affect Isolate Regions, using the same dialog preferences as the other Adjust suppressions.

---

## v6.0.33

**What's new**

- **Viewer/Editor views stay centered and locked together:** Enabling Allow Adjustment no longer drops the images to the bottom of the panes. Zoom (slider, `+/-`, mouse wheel) keeps the tissue under the center of the view, and both DAPI and annotation panes stay in sync.
- **Mouse pan:** Middle-drag (or Space+left-drag) pans both panes together without interrupting paint or region pick.

**Commit subject**

Keep Adjust views centered and synced while zooming

**Commit body**

Lock dual-pane scene bounds and scroll sync so Allow Adjustment no longer jumps the images, and apply viewport-center zoom plus middle-button pan so DAPI and annotation stay aligned.

---

## v6.0.32

**What's new**

- **Area parts hierarchy tier:** Between Sub-areas and Cortical layers, Hierarchy and parcellation now offer **Area parts** — named subdivisions without cortical layers (for example VISp, RSPagl / RSPd / RSPv, AUDp). Applying Area parts rolls laminar labels up to those parents across the whole CCFv3 tree, not just one region family.
- **Clearer Full detail vs Layers:** Full detail keeps annotation IDs as drawn (no rollup). Tooltips on the parcellation tier list spell that out.

**Commit subject**

Add Area parts tier for CCF editing without layers

**Commit body**

Add a Hierarchy / parcellation tier for immediate non-layer parents of cortical layers so editors can paint and roll up to VISp, RSPagl, AUDp, and similar nodes without collapsing to coarser parents or keeping laminar IDs.

---

## v6.0.31

**What's new**

- **Viewer/Editor feels snappier while painting:** Long brush strokes stay live on screen, but heavy label audits wait until you release the mouse.
- **Zoom and pan from the keyboard:** Zoom slider goes from 50% to 1000% and keeps both panes locked. Use `-` / `+` for 10% steps and arrow keys to pan about one-fifth of the view (stops at the edges).
- **Search finds any atlas region:** The Search bar looks across all categories; press Enter to set the paint target. Hierarchy / Area dropdowns stay tier-scoped for deliberate painting.
- **Larger brush and Delete → Lost in Warp:** Brush size goes up to 50. Delete or Backspace fills the highlighted region with Lost in Warp (and sets that as the paint target); Undo still works.
- **Fewer repeat confirmation clicks:** Mixed-resolution tier notices and the Save overwrite confirm can be dismissed with “Don’t show again.” Restore them under **Settings → Dialogs**. Suppressions clear automatically after an app update.
- **Fresher project-index spinner lines:** The “busy indexing” overlay messages are tightened up (same jokes, shorter punchlines).

**Commit subject**

Improve Viewer/Editor paint, zoom, search, and dialogs

**Commit body**

Make Adjust painting snappier by deferring UI audits until mouse release, add keyboard zoom/pan and unscoped search with Enter, raise brush size to 50, map Delete to Lost in Warp, add suppressible dialogs with Settings reset and clear-on-update, and refresh the project-index busy spinner copy.

---

## v6.0.30

**What's new**

- **Batch Cell Detection can use each project’s QC intensity threshold:** After Detect QC scout has suggested a cutoff for a project, check **Use QC-derived intensity threshold (per project)** in Batch Detect parameters. Each project gets its own suggested value instead of one global intensity cutoff.

**Commit subject**

Let batch Detect use each project’s QC intensity threshold

**Commit body**

Batch Cell Detection can apply the intensity cutoff suggested by Detect QC scout on a per-project basis, so you no longer have to copy the same number into the global intensity field for every animal.

---

## v6.0.29

**What's new**

- **Tissue edge cleanup paints removed areas black:** Red (remove) regions are filled with black on Apply, matching what the green/red overlay suggests. Previously the tool tried to match the frame-edge brightness, which could leave a grey background when tissue touched the scan edge.

**Commit subject**

Fill tissue cleanup remove regions with black

**Commit body**

Tissue edge cleanup Apply now replaces red (remove) pixels with black by default instead of estimating background from the image border, so edge tissue can no longer leave a grey field behind.

---

## v6.0.28

**What's new**

- **Batch sharpen / top-hat show up in Cell Detection:** Batch now writes sharpened (and top-hat) datasets under the correct signal branch folder (for example somata), so the Intensity dataset dropdown lists them next to max projection.
- **Repair on open:** Opening a project that already has misplaced batch sharpen folders moves them into the signal branch with a short log message — no need to re-run batch sharpen when the images are already on disk.

**Commit subject**

Fix batch max-family write paths so Detect lists sharpen

**Commit body**

Infer the signal branch from the batch input leaf when writing max/sharpen/top-hat outputs, prefer the active max-family leaf for signal-dataset steps, and rename orphaned 03_max/{kind}/{slug} folders into the signal branch on project open.

---

## v6.0.27

**What's new**

- **Updater validation build:** Pre-release with no other product changes, so machines on the fixed 6.0.26 can confirm Update Now finishes installing and relaunches on the new version.

**Commit subject**

Ship 6.0.27 pre-release to validate Update Now

**Commit body**

Version bump only so lab installs on fixed 6.0.26 can exercise Update Now end-to-end against a newer pre-release.

---

## v6.0.26

**What's new**

- **Update Now completes and reopens:** After downloading a Windows update, Mason Jar closes, finishes installing in the background, and relaunches on the new version instead of leaving you stuck on the old build (which could trigger another required-update loop).
- **Install check uses the real app version file:** Update Now verifies the version from Electron’s `resources/app` package file (and still accepts a root copy for compatibility), so a successful install is no longer treated as a failure when that file is not next to `masonjar.exe`.

**Commit subject**

Fix Windows Update Now verify path and quit handoff

**Commit body**

Launch the apply script via a breakaway cmd start so Electron’s job object cannot kill it on quit, verify the installed version from resources/app/package.json (with a root package.json zip shim for older clients), register the script’s own PID in update.lock, and fail clearly if elevation is cancelled.

---

## v6.0.25

**What's new**

- **Batch modernization:** Batch runs the same main tools as the workspace (where they can run without interaction). Deprecated DAPI cleanup is removed from Batch. Top-hat is available as a first-class Batch step. Max / sharpen / detect outputs follow the same signal-branch folders as the single-tool wizards.
- **Detect QC scout (Batch):** Run cell detection across projects to gather the full QC package (histogram graphs, summary, threshold suggestions) under the predictions tree without writing detection PKLs or changing the active detection run.
- **QC scout in the project:** Completed tasks shows Detect QC scout with Browse. Cell Detection shows a banner when scout data exists, can apply the suggested intensity cutoff, and asks before a full Detect run if that threshold has not been applied yet (Apply and run / Skip and run / Cancel).
- **Batch signal datasets:** Sharpen, Top-hat, Detect, Detect QC scout, and Isolate Regions let you pick Max / Sharpen / Top-hat as the per-project input kind, matching the Cell Detection dataset families.

**Commit subject**

Modernize Batch with Top-hat and Detect QC scout

**Commit body**

Align Batch with current main tools (path/branch parity, Top-hat, drop DAPI cleanup), add Detect QC scout that writes full QC under predictions without PKLs, and surface scout results in Completed tasks and the Cell Detection wizard with an apply/skip pre-run prompt.

---

## v6.0.24

**What's new**

- **Faster CZI probe:** Probing large multi-Z mosaic CZIs no longer scans every Z plane’s mosaic tiles up front. Probe stays quick for folder import; sparse-Z detection still runs correctly during extract.
- **Fair-share project index:** Opening or refreshing a large project index now counts toward network fair-share, so another Mason Jar instance running a heavy pipeline job shares the NAS link instead of starving the index load.
- **Align Finish confirmation:** Clicking Finish in Napari asks you to confirm before warping, with Cancel as the safe default.
- **Align warp progress in Mason Jar:** After you confirm Finish, Mason Jar comes back to the front immediately and shows a live warping progress log. When warping completes, a Done panel summarizes results with next-step buttons (Detect, Alignment menu, Workspace).

**Commit subject**

Speed up CZI probe, fair-share index loads, and Align Finish UX

**Commit body**

Make multi-Z mosaic CZI probe metadata-cheap again, register project index refresh under io fair-share, and after Napari Align Finish confirmation restore Mason Jar with a warp progress panel and Done summary.

---

## v6.0.23

**What's new**

- **Loading project index:** When you open the workspace or a pipeline tool and Mason Jar is scanning the project’s files, a floating “Loading project index…” panel appears with a short spinning console of lighthearted status lines so the window no longer looks frozen. It clears once banners and tools are ready. Long-running job consoles (CZI extract, batch, Isolate Regions, and similar) are unchanged.

**Commit subject**

Show a loading overlay while the project index builds

**Commit body**

Opening the workspace or tool setup pages now shows a floating spinner with a fading funny-message console until the project file index and run lists finish populating, so the UI no longer looks hung with no feedback.

---

## v6.0.22

**What's new**

- **Update Now:** With only one Mason Jar window open, Update Now no longer asks you to close “other instances.” Previously the updater mistook Electron’s background helper processes for extra copies of the app, so the update could not start.

**Commit subject**

Fix Update Now false other-instance detection

**Commit body**

Count only Mason Jar main processes when checking peers before Update Now; ignore Electron helper processes that share masonjar.exe.

---

## v6.0.21

**What's new**

- **Multiple windows:** You can open more than one Mason Jar at a time again (useful on shared lab machines). Updating still asks you to close other copies from the same install folder before Update Now runs.
- **Update wait screen:** If you open Mason Jar while an update is installing, you see the usual startup screen with “Installing update — please wait…” until it is safe to continue — not a quit dialog or a silent wait.
- **CZI import probe:** When a Zeiss file uses a pixel type libCZI cannot sample-read, probe records a warning and skips further sample reads for that file so the rest of the folder can finish. If the shared Python worker dies mid-probe (rare), Mason Jar automatically retries that folder up to two more times so you usually do not need to click Re-probe.

**Commit subject**

Restore multi-instance Mason Jar and harden update wait UX

**Commit body**

Allow multiple Mason Jar windows again, keep update.lock only while apply runs, and show the normal loading splash while an update installs. Also soft-fail CZI probe sample reads after unsupported PixelType errors and auto-retry folder probe when the shared worker exits.

---

## v6.0.20

**What's new**

- **Network fair-share:** Finished pipeline jobs no longer keep counting toward the shared NAS bandwidth limit after they complete. Previously a finished tool could leave a “ghost” job in the fair-share list until you fully quit Mason Jar, which made active work look more throttled than it should.

**Commit subject**

Clear finished jobs from network fair-share when tools complete

**Commit body**

The long-lived Python worker now tears down fair-share registration after each tool so ghost jobs stop limiting bandwidth for everyone else.

---

## v6.0.19

**What's new**

- **Windows Update Now restart:** Installing an update no longer asks “Quit Mason Jar?” in the middle of the install. Canceling that prompt could leave the updater waiting while the app stayed open, so Mason Jar did not reopen until you quit manually.
- **Safer updates:** Only one Mason Jar window runs at a time. If another copy is open from the same install folder, Update Now asks you to close it first. A second launch while an update is installing is blocked. If Mason Jar is still running after the updater waits, the install aborts instead of replacing files in use.
- **Version backups optional:** Updates no longer copy a full backup of the install folder by default. Settings → Updates has **Keep version backups** (off by default) and **Delete old version backups** to remove prior `….backup-<version>` folders.
- Portable installs may live in any folder name (including Explorer’s versioned unzip folder such as `masonjar-win32-x64-6.0.16`); updates still replace files in place.

**Commit subject**

Harden Windows updater quit, instances, and optional backups

**Commit body**

Skip quit confirmation during update apply, enforce single-instance and fail-closed process wait, and make version backups opt-in with cleanup on Settings → Updates.

---

## v6.0.18

**What's new**

- **CZI Cancel extraction:** Cancel now stops a running CZI import promptly (including long mosaic reads). Previously the Cancel button could appear to do nothing while Python kept writing files.
- **Orient after Repair previews:** After repairing missing previews from the Orient step, the wizard returns to Orient instead of leaving you stuck on Extract with no Next button. A **Continue to Orient** control is also available on Extract if needed.
- **Pending orientation saved as you edit:** Rotate/flip choices on Orient are written to the project as you click, so they survive closing the wizard before Confirm geometry.
- **Safer re-Extract:** If this project already finished a CZI extract, starting Extract again skips to Orient when outputs look complete, or asks for confirmation before overwriting.

**Changes**

- Worker kill for CZI jobs terminates the Python worker process so in-progress extract cannot continue after Cancel.

**Commit subject**

Stop CZI Cancel hanging and return Orient after preview repair

**Commit body**

Cancel now hard-stops the Python worker so CZI extract stops writing. Orient repair returns to Orient; pending geometry persists; full re-extract is guarded when import is already complete.

---

## v6.0.17

**What's new**

- **Count Brain CSV:** When alignment or Viewer/Editor parcellation is set to cortical layers, the count results CSV includes laminar rows (for example VISp1, VISp4) again. Mixed parcellation across sections is supported: each section’s resolved labels appear in the same CSV (do not sum a parent region with its layer children).

**Changes**

- Count output follows the acronyms actually used after parcellation-aware rollup instead of dropping layer names from the spreadsheet.

**Commit subject**

Include laminar acronyms in Count Brain CSV after layers parcellation

**Commit body**

Count results now emit resolved used region acronyms so cortical-layer parcellation appears in the CSV. Mixed per-section tiers are logged and written without collapsing layers away.

---

## v6.0.16

**What's new**

- No user-facing pipeline changes.

**Changes**

- Internal operator documentation for KIM-SERVER kernel pool investigation consolidated; obsolete Proc measurement scripts removed from the repo.

**Commit subject**

Consolidate Proc pool handoff docs and remove investigation scripts

**Commit body**

Replaces the long Proc leak handoff with a slim operator doc. Drops pool_leak_watch and related gate scripts now that diagnosis is Windows-first on the host.

---

## v6.0.15

**What's new**

- **Tissue edge cleanup Apply** works again on preview PNGs from CZI import and re-import (including projects where OpenCV reads PNGs as three-channel BGR on disk).

**Changes**

- Tissue cleanup Apply uses the same grayscale conversion as the wizard preview when reading PNG files, fixing a regression from the v4.0.5 streaming Apply refactor.

**Commit subject**

Fix tissue cleanup Apply on BGR-encoded preview PNGs

**Commit body**

Apply now converts BGR/BGRA preview PNG reads to 2D gray before masking, matching the wizard path. Fixes Unsupported ndim=3 failures on 00_dapi and _previews after CZI re-import.

---

## v6.0.14

**What's new**

- **Network fair-share in the title bar:** When NAS fair-share is enabled, the window title (and Windows taskbar) shows active job count, your per-job speed limit, and this instance’s throttled NAS throughput—updated every few seconds so you can see it while pipeline jobs run on other pages.
- **Required updates:** Packaged Mason Jar now checks GitHub for the latest **stable** release on startup. If your version is behind, the app locks pipeline tools and automatically downloads and installs the update on Windows (no dismiss button). If another copy of Mason Jar is already running, you’ll be asked to close all instances first.
- **Workspace hub:** The compact network-share line on the Pipeline page uses the same wording as the title bar.

**Commit subject**

Fair-share title bar stats and required stable updates on startup

**Commit body**

Shows fair-share job count, Mbps limit, and NAS throughput in the native window title. Adds mandatory auto-update to GitHub Latest stable for packaged builds, with multi-instance guard and a locked update wizard on Windows.

---

## v6.0.13

**What's new**

- **Server stability (Phase F):** Cell Detection forces single-threaded torch/OpenMP on the Electron path so detect jobs do not spawn extra worker processes. Align and Adjust now call explicit process cleanup on every exit path.

**Commit subject**

Harden detect and GUI tool cleanup for server stability

**Commit body**

Limits torch/OpenMP threads in find_neurons on the Electron path. Ensures Align/Adjust always kill supervised Python shells on finalize.

---

## v6.0.12

**What's new**

- **Server stability (multi-day uptime):** Mason Jar no longer starts a new Python process for every pipeline task. A single supervised worker runs batch jobs in-process, with proper cleanup when tools finish or the app quits. This reduces process churn on shared lab servers that previously needed a reboot after several days.
- Align and Adjust still use their own Python windows (required for the interactive viewers); all other tools share the worker.
- If a job misbehaves, set environment variable `MASONJAR_PYTHON_WORKER=0` to restore the old one-process-per-job behavior.

**Commit subject**

Reduce Python process churn for multi-day server stability

**Commit body**

Adds a main-process job supervisor and long-lived Python worker so max, CZI import, detection, and other headless tools reuse one process per app window instead of spawning hundreds per day. Fixes lifecycle cleanup on quit and removes renderer-side process spawning.

---

## v6.0.11

**What's new**

- Cell Detection keeps the intensity-branch dropdown visible when switching channels (e.g. somata → starters).
- **Merge** plans against the new output folder for that channel, so a first starters run is not blocked by an existing somata predictions leaf.
- Detection outputs go under `05_predictions/{signal branch}/…` (e.g. `starters/`, `somata/`), matching how max projections are organized—not under the model name alone.
- Step 2 progress bar works again and tracks **slices** (e.g. slice 12/54 ≈ 22%), not every log line.

**Commit subject**

Fix Cell Detection branch switching, merge, output paths, and progress

**Commit body**

Intensity-branch selection no longer hides the dataset picker. Merge plans against the intended predictions leaf for the selected channel. Outputs are stored under the signal branch folder. The process progress bar advances per slice.

---

## v6.0.10

**What's new**

- Hidden Align setup banners no longer leave empty bordered boxes on the page.

**Commit subject**

Hide empty Align banner frames when messages are not shown

**Commit body**

Align session-restore and Napari handoff banners now hide their full workspace-block rows, matching the run-mode panel pattern so empty frames do not appear on the setup page.

---

## v6.0.9

**What's new**

- Category tool menus (Atlas alignment, preprocessing, detection, exports) use full-width buttons again instead of narrow left-aligned links.

**Commit subject**

Restore full-width buttons on category tool menus

**Commit body**

Legacy-mode category menu changes in v6.0.7 wrapped tool links without full width; tool buttons again span the menu column like v6.0.6.

---

## v6.0.8

**What's new**

- Finished Align sessions keep your tuned AP positions when you reopen and navigate with **Next**. Mason Jar no longer re-extrapolates over saved tuning on a completed session.
- The Align setup page restores your last **Alignment Method** (Automatic, whole brain, or single hemisphere) from the saved session so reopening matches how you tuned.
- A completed session is no longer deleted when the alignment method dropdown does not match the saved fingerprint.

**Changes**

- Align saves `layout_mode` in the session file and shows a short resume banner on the setup page when the method is restored.

**Commit subject**

Fix Align AP corruption when reopening finished sessions

**Commit body**

Clicking Next on a completed Align session no longer runs AP extrapolation over saved tuning. Completed sessions survive a layout-method fingerprint mismatch, and the Align setup page restores the saved alignment method from the session file.

---

## v6.0.7

**What's new**

Legacy mode (classic Bell Jar `M###/counting/` folders without a `.masonjar` project) now shows a clear agreement dialog before you open a brain folder. The dialog lists which pipeline tools work, which are limited, and which require a project bundle. While in legacy mode, unavailable tools are disabled in the pipeline menus, and the workspace shows a banner where you can review the limitations again.

**Changes**

- Consent required once per caveats version before entering legacy mode.
- Pipeline category menus mark limited tools and disable project-only tools in legacy mode.
- Workspace menu shows a legacy notice and updated category subtitles.

**Commit subject**

Legacy mode shows agreement dialog and marks limited tools

**Commit body**

Before opening a classic Bell Jar folder layout, users must accept a dialog listing supported, limited, and unavailable pipeline tools. Legacy workspace menus disable project-only tools and show a review banner.

---

## v6.0.6

**What's new**

After **re-import from CZI**, if the project has no saved orientation history (older projects oriented before history was recorded), Orient step 5 now shows a clear warning and lets you set rotation manually.

**Fixes**

- Re-import Orient: rotation buttons, **Copy first tile geometry to all**, and **Confirm geometry** work when orientation history is missing.
- When history exists, re-import Orient behavior is unchanged (restored transforms and existing confirm flow).

**Commit subject**

Release v6.0.6 — re-import orient when geometry history is missing

**Commit body**

Warn on Orient step 5 when geometry_history.jsonl is absent after re-import; enable manual rotation and confirm for legacy projects.

---

## v6.0.5

**What's new**

Pre-release build for testing **Update Now** from v6.0.4. Enable **Advanced → Allow pre-release versions**, then **Check again** to receive this build.

**Changes**

- Settings → Updates shows a confirmation banner on v6.0.5 for pre-release update testing.

**Commit subject**

Release v6.0.5 pre-release for Update Now testing

**Commit body**

Pre-release zip to validate one-click Update Now from 6.0.4 stable installs.

---

## v6.0.4

**What's new**

**Update Now** on Settings → Updates stays enabled when an update is available — it is no longer greyed out after a failed or interrupted install attempt.

**Fixes**

- Update lock is written only after the updater starts successfully; orphaned locks from failed runs are cleared when you open Settings → Updates.
- If a download already finished, **Update Now** reuses the staged files instead of downloading again.

**Commit subject**

Fix Update Now staying disabled after failed update

**Commit body**

Stop greying out Update Now when a stale update lock exists; defer lock creation until spawn succeeds; clear orphan locks on status refresh; reuse staged download when retrying.

---

## v6.0.3

**What's new**

Pre-release build for testing **Update Now** from v6.0.2. Enable **Advanced → Allow pre-release versions**, then **Check again** to receive this build.

**Changes**

- Settings → Updates shows a confirmation banner on v6.0.3 for pre-release update testing.

**Commit subject**

Release v6.0.3 pre-release for Update Now testing

**Commit body**

Pre-release zip to validate one-click Update Now from 6.0.2 stable installs.

---

## v6.0.2

**What's new**

Windows in-app updates now use a single **Update Now** button on Settings → Updates — download and install happen in one step.

**Fixes**

- Updater script survives app quit more reliably and waits for Mason Jar to fully exit before replacing files.
- Update log is created before install starts; **Open update log** opens your settings folder if no log exists yet.

**Commit subject**

Fix Windows updater and add Update Now button

**Commit body**

Reliable detached apply via cmd start, CIM process wait, robocopy retries, and pre-flight logging. Settings → Updates uses one Update Now action instead of separate download and install steps.

---

## v6.0.1

**What's new**

Pre-release build for lab testing of **Settings → Updates** from v6.0.0. Enable **Advanced → Allow pre-release versions**, then **Check again** to receive this build; users who leave pre-releases off stay on 6.0.0 until a future stable release.

**Changes**

- Settings → Updates shows a short banner on v6.0.1 confirming the pre-release test build.

**Commit subject**

Release v6.0.1 pre-release for update testing

**Commit body**

Pre-release zip for validating in-app update delivery and pre-release channel gating from 6.0.0 stable installs.

---

## v6.0.0

**What's new**

Mason Jar can now check for updates and install them from **Settings → Updates**. On startup, when a newer release is available, choose **Update** to open the updates page, **Download in browser**, or **Later**.

On **Windows**, download the published zip and use **Install and restart** for a one-click upgrade (Mason Jar quits, replaces its install folder, and relaunches). Your projects and models in `%USERPROFILE%\.masonjar` are not touched.

**Advanced:** enable **Allow pre-release versions** to receive beta builds from GitHub when they are newer than the latest stable release.

**macOS** uses the same Updates page to check versions and open the GitHub release for manual DMG install.

**Note for 5.x users:** download and install v6.0.0 once from GitHub to gain in-app updates going forward.

**Commit subject**

Add in-app Windows updates via Settings

**Commit body**

Settings → Updates checks GitHub releases, downloads the Windows zip, and applies it after quit via a detached updater script. Startup dialog offers Update, Download in browser, or Later. Advanced toggle includes pre-release builds when semver is newer.

---

## v5.0.10

**What's new**

Checking **Set as active max task for this branch** after Sharpen or Top-hat now correctly makes **Cell Detection** default to that sharpened or filtered dataset instead of the original max projection.

**Fixes**

- Cell Detection output folders are separate when you run detect on max vs sharpen inputs with the same tuning parameters (each input dataset gets its own run folder under predictions).
- **Completed tasks** on the workspace menu selects the latest detection run automatically; when you have more than one, a folder button opens the predictions directory so you can browse all runs.

**Commit subject**

Fix sharpen active max propagating to Cell Detection

**Commit body**

The sharpen Done checkbox now sets the active max dataset when checked. Cell Detection honors active max over saved picker state, encodes the input dataset in detection output folder names, and improves Completed tasks for multiple prediction runs.

---

## v5.0.9

**What's new**

After Cell Detection, the summary step now suggests and applies only the **intensity cutoff** (the bimodal brightness split). Confidence, area, and eccentricity are no longer suggested or copied—lab testing showed those recommendations could remove all detections on re-run.

**Changes**

- **Use suggested intensity cutoff** replaces **Apply suggestions** on the summary step.

**Commit subject**

Cell Detection QC suggests intensity cutoff only

**Commit body**

The detection summary step no longer suggests or applies confidence, area, or eccentricity changes. Only the GMM intensity threshold is offered, with a single button to pre-fill Advanced settings.

---

## v5.0.8

**What's new**

Cell Detection is now a three-step wizard: configure parameters, watch progress, then review QC charts and algorithmic suggestions for confidence, area, eccentricity, and intensity cutoff. After each run Mason Jar analyzes your detections for a low- vs high-intensity split and shows recommended tuning values. Use **Apply suggestions** to pre-fill advanced settings and re-run. An optional **Intensity cutoff** in advanced settings drops dim false positives (defaults to off so existing workflows are unchanged).

**Changes**

- `detect.html` redirects to the new Cell Detection wizard; batch detect supports intensity cutoff.
- Per-run QC summary JSON includes an `analysis` block and an intensity split line on QC charts.

**Commit subject**

Cell Detection wizard with QC suggestions and intensity cutoff

**Commit body**

Cell Detection is a three-step wizard with in-app QC charts and per-run parameter suggestions from bimodal intensity analysis. Advanced settings add an optional intensity cutoff filter (default off). Apply suggestions pre-fills tuning values for a quick re-run.

---

## v5.0.7

**What's new**

Detection QC charts now plot **bbox area (px²)** with a line at your area cutoff, replacing the less useful long-axis chart. Each QC histogram also overlays **per-detection brightness** (black dots = all SAHI candidates, blue outlines = detections kept after screening) so you can spot dim false positives vs bright somata when tuning confidence, area, and eccentricity.

**Changes**

- Middle QC file is now `detect_qc_area_px2.png`; summary JSON includes intensity percentiles.
- **Enable additional per-slice QC plots** moved to the Detect page footer, below the flat-output checkbox.

**Commit subject**

Improve detection QC with area chart and intensity dots

**Commit body**

Detection QC replaces the long-axis histogram with area versus the screening cutoff and adds per-bbox intensity dots on all three charts. The optional per-slice QC checkbox sits in the Detect footer next to other output options.

---

## v5.0.6

**What's new**

After **Cell Detection** finishes, Mason Jar saves PNG histograms in the detection output folder so you can tune confidence, size, and eccentricity without guess-and-check. Run-level charts (confidence, bounding-box long axis, eccentricity) are written every time; optional per-slice charts are available from a checkbox on the Detect page and in the batch wizard.

Sharpen and **Top-hat filter** wizards now include a **?** help button next to **Set as active max task for this branch**, explaining what that option does and when to use it before running **Cell Detection** or **Isolate Regions**.

**Changes**

- Detection output includes `detect_qc_confidence.png`, `detect_qc_long_axis_px.png`, `detect_qc_eccentricity.png`, and a JSON summary with counts and thresholds used.
- Re-running Detect into the same output folder replaces prior QC files automatically.
- Batch **Detect** step supports the same per-slice QC option.

**Commit subject**

Add detection QC histograms and active max task help

**Commit body**

Cell Detection now writes run-level QC histograms and an optional per-slice report to help tune confidence, size, and eccentricity. Sharpen and top-hat Done steps add a help popover for Set as active max task so users know when downstream Detect and Isolate Regions will use filtered images.

---

## v5.0.5

**What's new**

After **Re-import sections from CZI** with only some channels selected, Orient step 5 now shows **only the re-imported sections and channels** you need to review.

**Fixes**

- Skipped channels (e.g. DAPI) are greyed out in the display channel menu and no longer load a misleading rotated preview.
- The first re-imported signal channel is selected automatically instead of DAPI.
- Previews show how each re-imported channel will look **after Confirm geometry**, without double-rotating channels that were not re-read.

**Commit subject**

Fix re-import Orient preview for channel-scoped geometry

**Commit body**

Partial re-import Orient step 5 now limits the tile grid to re-imported sections, greys out skipped channels in the menu, and applies preview rotation only to re-imported channels so DAPI and other untouched images are not shown with extra transforms.

---

## v5.0.4

**What's new**

After **Re-import sections from CZI** with only some channels selected (for example signal channels without DAPI), the CZI import wizard Orient step lets you **Confirm geometry** again and applies rotation only to the channels you re-imported.

**Fixes**

- **Confirm geometry** is no longer greyed out after a partial re-import when prior orientation was already applied.
- Geometry apply skips DAPI and other channels you did not re-extract, so counterstain orientation is not double-transformed.
- Banner text on Orient step 5 explains when only re-imported signal channels will be written.

**Commit subject**

Fix partial geometry apply after CZI re-import

**Commit body**

After re-importing selected channels only, Orient step 5 clears stale apply metadata, enables Confirm geometry, and applies saved rotation to scoped channels so DAPI and untouched files stay unchanged.

---

## v5.0.3

**What's new**

Pipeline tools no longer create nested run folders when you re-run a step with **Overwrite all** or change parameters (Detect, Align, Max, Count, and others).

**Fixes**

- Detect outputs stay at `05_predictions/somata/{run}` instead of nesting `somata/old/somata/new`.
- **Overwrite all** reuses the active run folder so you can replace results in place.
- Max dataset picker, Count run pickers, and **Completed tasks** still discover runs the same way.

**Commit subject**

Fix nested run folders when re-running pipeline steps

**Commit body**

Output paths now resolve from the role base folder instead of the active run leaf, so branch and slug are not appended twice. Overwrite mode reuses the current run folder. Dataset pickers and run discovery are unchanged.

---

## v5.0.2

**What's new**

Large sharpen and top-hat runs on NAS or remote desktop now finish cleanly and advance the wizard to **Done** when your slices were processed successfully.

**Fixes**

- Sharpen no longer shows a false **Python exited with code 1** error after `sharpen_done` on large tiled runs.
- Top-hat batch completion uses the same reliability fix as sharpen.

**Commit subject**

Fix Sharpen wizard false failure after successful batch run

**Commit body**

Sharpen and top-hat now signal Done only after outputs and run manifest are written. The wizard treats completed runs as success even when Python reports a late non-zero exit on slow NAS jobs.

---

## v5.0.1

**What's new**

**Re-import sections from CZI** is back in the workspace and preprocess menus. It uses the same import wizard as a full CZI import, so extract, bit depth, max projection, and Orient all follow one familiar path.

**Fixes**

- Re-import preserves saved bit depth and max-projection settings, including 16-bit axon scaling from v5.0.0.
- After re-import, Orient (step 5) restores your saved rotation preview from geometry history, clears stale “already applied” messaging, and asks you to **Confirm geometry** to write files again.

**Commit subject**

Fix CZI re-import and Orient after re-read from CZI

**Commit body**

Re-import sections from CZI again runs through the main CZI import wizard instead of a separate broken page. After re-read, Orient restores saved rotation from geometry history and clears misleading “already applied” banners so you can confirm geometry and re-bake files.

---

## v5.0.0

**What's new**

- Sharpen and Top-hat wizards show a full-resolution filtered preview after you click **Preview filter**, so you can see fine processes and tune radius, amount, and equalize before running the batch.
- After the first preview, pan and zoom on the filtered view and click **Preview filter** again to refine a smaller sub-region.

**Fixes**

- Preview no longer shows a washed-out low-res slice or a solid black overlay rectangle.
- Progress text during preview uses plain ASCII (no garbled characters on Windows).
- Preview PNG matches batch filter output; display min/max sliders apply to the filter view.

**Commit subject**

Sharpen and Top-hat preview show full-resolution filtered ROI for parameter tuning

---

## v4.1.7

**What's new**

- Sharpen filter core restored to match Bell Jar (native bit depth through CLAHE, unsharp mask, and white top-hat; output dtype preserved).
- Sharpen wizard preview fits the slice to the viewport on load.

**Fixes**

- Sharpen: fixed patchy contrast and banding on 16-bit max projections caused by forced 8-bit normalization and per-tile equalize bounds (v4.1.5–4.1.6 regression).
- Large slices still use tiled processing for memory safety; each tile runs the same Bell Jar filter core.
- Sharpen/Top-hat wizard preview no longer auto-shrinks the slice to fit the pane (restores 1:1 zoom on load for tissue ROI work).

**Commit subject**

Sharpen: restore Bell Jar filter core; fix 16-bit preprocess regression

---

## v4.1.6

**What's new**

- Viewer/Editor region search is case-insensitive and limited to the active Hierarchy tier (or CCF level in advanced mode).
- Count Brain rolls up mixed CCF label levels automatically so totals stay consistent when annotations combine areas and layers.
- Viewer/Editor warns when changing content tier after painting, and Isolate Regions setup warns when labels mix CCF tiers or changed since your last run.

**Fixes**

- Viewer/Editor: Search and Area completers no longer hide matches due to case-sensitive filtering.

**Commit subject**

Viewer/Editor search, robust Count rollup, and Intensity mixed-tier warnings

---

## v4.1.5

**What's new**

Sharpen on large 16-bit max-projection slices now produces clean output that matches the wizard preview.

**Fixes**

- Sharpen: fixed severe banding, dark voids, and speckle corruption on large 16-bit TIFFs (tiled path). Input is normalized to 8-bit before filtering, same as Top-hat; output is always 8-bit TIFF.
- Verified Top-hat and other import/preprocess tools do not share the sharpen double-scale bug; shared grayscale loader keeps preprocess filters aligned.

**Commit subject**

Sharpen: fix 16-bit corruption on large tiled max projections

---

## v4.1.4

**What's new**

- Align: Mason Jar minimizes when Napari opens and shows a clear handoff message; Mason Jar restores when you Finish or Cancel.
- Align: Napari layout uses a top toolbar plus Tuning/Options docks on the left; layer list and layer controls on the right.

**Fixes**

- Align: Napari opens maximized and keeps focus on remote desktops (no double-click maximize).
- Image preprocessing: DAPI cleanup and Orient slices moved under **Deprecated & Experimental** (collapsed by default).

**Commit subject**

Align: calmer Napari handoff, layout polish, and deprecated preprocess submenu

---

## v4.1.3

**What's new**

Large full-resolution sharpen slices run reliably on remote desktops and memory-limited machines.

**Fixes**

- Sharpen: large max-projection TIFFs no longer fail with "Python exited with code 1" on memory-limited or remote-desktop machines; processing runs in tiles with progress in the log.
- Sharpen wizard: progress bar no longer jumps to 100% as soon as processing starts.

**Commit subject**

Sharpen: tile large slices to avoid OOM on remote desktops

---

## v4.1.2

**What's new**

Align controls in Napari use one scrollable panel; Previous, Next, and Finish stay visible on short or remote-desktop screens.

**Fixes**

- Align: canceling the tissue-damage marker no longer excludes the whole atlas from registration.
- Align and DAPI cleanup: a prior align failure no longer blocks re-running align or DAPI cleanup on the same slice.
- Align: the Napari window opens within your screen bounds on remote desktops.

**Commit subject**

Align: fix damage-mask cancel, retry blocking, and Napari controls on RDP

---

## v4.1.1

**What's new**

Sharpen batch runs reliably on Windows and network drives — the same sequential processing model as the top-hat filter.

**Fixes**

- Sharpen: fixed batch runs that wrote zero files with `SHARPEN_NO_OUTPUT` and "process pool was terminated abruptly" on Windows/NAS.
- Sharpen: per-slice error messages in the log when a file fails.

**Commit subject**

Sharpen: fix batch failures on Windows by processing slices sequentially

---

## v4.1.0

**What's new**

Align again saves your AP and angle tuning when you close Napari and re-open Align. Forward AP suggestions follow the spacing you set on sections you have already tuned. Mason Jar automatically removes broken alignment session files from your project's DAPI folder when they cannot be loaded.

**Fixes**

- Align: tuning persists across close and re-open (pickle save no longer fails silently).
- Align: AP suggestions use your confirmed sections only, not raw model predictions (fixes wild values like 1100).
- Align: going to a previous section no longer overwrites saved AP values.
- Align: re-open always starts at section 1 with your saved tuning restored.
- Align: incompatible or corrupt `alignment.pkl` / `alignment_session.json` in `00_dapi` are cleared automatically on the next run — no manual delete step.

**Commit subject**

Align: save tuning across sessions and clear broken session files automatically

---

## v4.0.8

**What's new**

Align forward AP suggestions now follow the direction you set on the first sections you tune (posterior sections get higher AP when your first two do). Alignment saves and diagnostic lines appear in the Application log.

**Fixes**

- Align: fixed backwards AP spacing after visiting the first two sections (`adjust_positions` off-by-one).
- Align: spinbox updates no longer race with Next/close saves (`blockSignals` on display refresh).
- Align: `LOG:` lines from Align/Adjust appear in the Application log, not only the progress bar.

**Recovery:** If a prior run saved bad AP values, delete `00_dapi/alignment.pkl` and `alignment_session.json` once, then re-run Align.

**Commit subject**

Align: fix AP spacing direction, persistence races, and log visibility

---

## v4.0.7

**What's new**

Align again opens with sensible predicted AP positions and angles on the first section. If a bad autosave from v4.0.6 is on disk, Mason Jar clears it automatically and re-runs predictions.

**Fixes**

- Align: restored tissue predictor results on startup (v4.0.6 could save AP=0 and zero angles before the viewer opened).
- Align: automatically discards corrupt `predict_complete` autosaves from v4.0.6 and re-runs predictions.
- Align: closing Napari with X still saves your tuning (v4.0.6 behavior preserved).

**Commit subject**

Align hotfix: stop predict_complete autosave from wiping predictions

---

## v4.0.6

**What's new**

Align saves your section tuning when you close Napari with the window **X** or **Cancel** on the Align page. Re-run Align and your AP, angles, and layout choices restore from the saved session in `00_dapi`.

**Fixes**

- Align: spinbox edits are saved immediately before Next/Previous and on window close (no 500 ms debounce gap).
- Align: closing Napari no longer shows a false **Python exited with code 1** error or duplicate log lines.
- Align / Adjust: viewer close with a non-zero exit code is treated as a clean cancel when tuning was saved.

**Commit subject**

Align saves tuning when Napari closes; fix false exit code 1 on window X

---

## v4.0.5

**What's new**

Align and Viewer/Editor no longer leave Mason Jar stuck when you **Cancel** or close the viewer window. Alignment tuning is saved on Cancel as well as when you close Napari. Tissue cleanup **Apply** handles large NAS z-stack bundles without crashing. **Check Orientation Consistency** applies confirmed rotations across the full pipeline, and the orient grid lists all sections.

**Changes**

- Align / Adjust: Cancel requests a graceful save and exit; UI always returns to Run.
- Align: tissue edge-cleanup masks and gap warp strategies for registration.
- Tissue cleanup Apply: streaming TIFF I/O and resume checkpoint for large jobs.
- Orient / geometry repair: full-pipeline apply from repair wizard; slice list includes all sections.

**Commit subject**

Align and Adjust Cancel no longer hang; tissue cleanup and geometry repair fixes

---

## v4.0.4

**What's new**

Closing the Atlas Alignment Napari window without clicking **Finish** no longer leaves Mason Jar stuck on a running job. Your section tuning (AP, angles, layout) is saved automatically, and the Align page returns to **Run** so you can reopen and continue—or click **Finish** when you are ready to warp.

**Changes**

- Align: tuning autosave when you close Napari; saved choices restore on the next run even if the output folder slug changed.
- Align: closing Napari without Finish resets the UI without marking a completed alignment run.

**Commit subject**

Align saves tuning on Napari close and no longer hangs the UI

---

## v4.0.3

**What's new**

Sharpen and Top-hat **Preview filter** no longer turns the tissue completely black. The filter now runs on the same preview image you see in the wizard, pan/zoom clears a stale filter overlay, and display min/max sliders apply to the filtered region.

**Changes**

- Sharpen / Top-hat: preview filter uses the displayed signal preview (not mismatched full-res coordinates).
- Sharpen / Top-hat: filtered ROI overlay respects display levels; pan/zoom resets filter preview until you click Preview filter again.

**Commit subject**

Sharpen and Top-hat preview filter no longer black out the image

---

## v4.0.2

**What's new**

Sharpen and Top-hat filter wizards now show the correct signal-channel preview (not DAPI) when you pick a rabies, somata, or other branch. Filter preview matches the region you pan and zoom, display min/max sliders work on filtered previews, and runs write TIFF outputs instead of leaving an empty sharpen or top-hat folder. The wizard stays on the progress step and reports an error when processing fails.

**Changes**

- Sharpen / Top-hat: branch-aware preview images, scaled filter ROI, composite filtered preview with pan/zoom.
- Sharpen / Top-hat: slice list intersected with source dataset; TIFF-only slice picker.
- Sharpen: reliable TIFF output (uint16 and `.ome.tif` inputs supported).
- Run failure surfaced in wizard UI instead of false success on empty output folders.

**Commit subject**

Sharpen and Top-hat wizards preview and run correctly again

---

## v4.0.1

**What's new**

The Windows download now extracts into a single folder named `masonjar-win32-x64`, so unzipping in Downloads no longer scatters app files into that folder.

**Changes**

- Windows zip layout: all app files live under `masonjar-win32-x64/` at the top level of the archive.

**Commit subject**

Windows zip extracts into masonjar-win32-x64 folder

---

## v4.0.0

**What's new**

Align Sections now handles mixed whole-brain and single-hemisphere series automatically — each section is detected and can be overridden in Napari. Viewer/Editor has a redesigned Paint panel (floatable dock) so region picking and brush tools stay accessible on smaller screens. Cell detection uses your NVIDIA GPU again on Windows after a fix to the bundled PyTorch install.

**Changes**

- **Align:** Automatic per-section layout (whole vs left hemisphere); Section layout override in Napari; layouts flow through to Isolate Regions.
- **Viewer/Editor:** Paint controls in a floatable dock; slim header for section navigation, channel, overlay, and Allow Adjustment.
- **Viewer/Editor:** File index rebuild after large align runs no longer fails on Windows, so DAPI/annotation pairing works again.
- **Tissue edge cleanup:** Apply step no longer hangs; Keep brush to add tissue; mask polarity fix for DAPI counterstain.
- **Cell detection:** Pin CUDA PyTorch on Windows/Linux so detection uses the GPU instead of CPU-only wheels.
- **Pipeline:** Count, collate, batch, CZI import, max/sharpen/tophat slice lists, align warp retries, and related fixes from the 3.3.x line.

**Commit subject**

Mason Jar 4.0 — mixed-section align, Viewer/Editor Paint dock, GPU detection

**Commit body**

Major release combining automatic per-section align layout, Viewer/Editor UI and index fixes, tissue cleanup reliability, CUDA PyTorch for cell detection, and accumulated pipeline hardening from experimental validation on real M457 data.

---

## v3.3.7

**What's new**

Multi-folder CZI imports (two or more source directories with the same `.czi` filenames) now extract and repair all sections correctly.

**Changes**

- Python extract resolves each channel row by full file path, not basename alone, so folder 2 slices are no longer mapped to folder 1’s CZI files.
- Preview repair picks the DAPI channel for each slice from `slice_order`, fixing missing counterstain on later folders.
- CZI wizard shows matched vs expected slice counts (e.g. `45 / 61`) and warns when DAPI and max pairing is incomplete.

**Commit subject**

Fix multi-folder CZI import when source folders share filenames

---

## v3.3.6

**What's new**

Viewer/Editor (Adjust) opens again after a crash on launch in v3.3.5.

**Changes**

- Fix startup crash when the paint-target toolbar initialized before its widgets were created.
- Fix workspace menu error when opening a project with CZI import history.
- Geometry apply recovery: workspace banner when a prior orient/geometry run was interrupted; audit log under `.masonjar/geometry_history.jsonl`.

**Commit subject**

Fix Viewer/Editor launch crash and workspace settings error

---

## v3.3.5

**What's new**

You can re-import selected sections from the original CZI files when a few slices have bad counterstain (e.g. black DAPI) without re-running the full import. Use **Re-import sections from CZI** from the workspace, Image preprocessing menu, or Orient when blank DAPI is detected.

**Changes**

- New wizard: pick sections and channels, confirm overwrite, re-read only those slices from source `.czi` files.
- Blank DAPI preview detection flags nearly black counterstain images and offers a direct link to re-import.
- **Check Orientation Consistency** moved off the preprocess menu; open it from **Orient slices** (renamed from “Check orientation”).

**Commit subject**

Re-import selected CZI sections without full re-import

---

## v3.3.4

**What's new**

Check orientation repair no longer fails on DAPI preview files looking for a z-stack under `original_scans/dapi/`. DAPI stacks from CZI import live at `original_scans/{section}.tif`; when no stack exists, repair transforms the existing DAPI PNGs instead.

**Changes**

- Geometry repair resolves DAPI z-stack paths using the same layout as CZI import (flat under `original_scans/`).
- Fallback transforms `_previews` and `00_dapi` PNGs when no DAPI z-stack is on disk.
- Orientation audit suggests in-place DAPI transform when no z-stack is available.

**Commit subject**

Fix DAPI geometry repair z-stack path

---

## v3.3.3

**What's new**

Geometry repair (**Check orientation** → repair) no longer fails on z-stack TIFFs stored on network drives. v3.3.2 could error with "expected str, bytes or os.PathLike object, not BytesIO" partway through a repair run.

**Changes**

- io_fairshare TIFF read/write patches no longer recurse through themselves when buffering to memory.
- Throttled file writes use the original Path helpers, avoiding infinite recursion on small files.

**Commit subject**

Fix geometry repair TIFF writes on network drives

---

## v3.3.2

**What's new**

Fixes **Check orientation** failing to load slice lists on projects that rely on the CZI import config (v3.3.1 regression).

**Changes**

- Geometry state helpers no longer confuse the import config object with the CZI import module when resolving slice IDs.

**Commit subject**

Fix Check orientation slice list after geometry state regression

---

## v3.3.1

**What's new**

**Check orientation** is always available from Orient and the Image preprocessing menu — you no longer need an interrupted-apply flag to reach the full-series audit.

**Changes**

- Blocks unsafe re-Apply when files were already modified but pending geometry remains (e.g. partial apply on 25 of 71 slices).
- Detects legacy partial-apply crashes (pre-v3.3.0) via mtime signals without progress meta files.
- Large z-stack TIFFs on NAS paths: preflight probes metadata via `TiffFile` and aborts before the transform loop; plane-wise reads avoid io_fairshare `BytesIO` failures.
- Geometry repair executes `derivatives_from_original` — rebuilds previews from `original_scans` z-stacks.
- Repair wizard renamed **Check orientation** with clearer healthy-state and audit-error copy.

**Commit subject**

Check orientation UX, block unsafe re-Apply, large TIFF geometry fix

---

## v3.3.0

**What's new**

Orient and CZI import now detect interrupted geometry applies and offer a **Rebuild geometry** wizard that audits every tissue section, flags cross-channel mismatches, and repairs only what you approve.

**Changes**

- Apply geometry is blocked when a prior run stopped partway; use **Rebuild geometry** from Orient instead of re-Applying blindly.
- Repair wizard runs a full-series orientation audit (progress bar + log), then optional per-slice review before writing files.
- **Finalize only** when files are done but project settings were not reset.
- Batch **Apply geometry** respects the same interrupted-state checks.

**Commit subject**

Geometry repair wizard and interrupted-apply detection

---

## v3.2.5

**What's new**

Fixes a Windows crash during long jobs (e.g. Apply geometry) when network fair-share could not update its registry file.

**Changes**

- Registry heartbeats use Windows-safe writes with retry; failures are logged, not fatal.
- Orient / geometry and other heavy jobs continue even if `%ProgramData%\MasonJar\io-fairshare\registry\` is briefly locked.

**Commit subject**

Fix io-fairshare EPERM crash on Windows registry heartbeats

---

## v3.2.4

**What's new**

Viewer/Editor (Adjust) is easier to use: a compact paint toolbar, always-visible paint target, working refresh after brush strokes, and parcellation in a detachable side panel.

**Changes**

- Brush strokes show correct atlas colors when you finish painting, refresh, or undo.
- Paint-target strip shows region name, color, tier, and adjustment state; brush cursor ring when painting is enabled.
- Parcellation opens in a floatable dock (toggle **Parcellation…**); rollup preview respects exclude list.

**Commit subject**

Viewer/Editor brush overlay fix and compact parcellation dock

---

## v3.2.3

**What's new**

Network sharing settings moved to **Start → Settings → Network**. An admin or first user on a shared server can pick mapped drives or UNC shares with **Select network drives…**; Mason Jar saves normalized drive/share roots for everyone on that machine.

**Changes**

- Settings hub with **Network** page (per-user fair-share toggle and link speed unchanged).
- Shared `nas_path_prefixes` list visible to all RDP users; no manual JSON editing required for typical setup.
- Multi-select folder picker writes machine-wide config under `%ProgramData%\MasonJar\io-fairshare\`.

**Commit subject**

Settings Network page with shared NAS drive picker

---

## v3.2.2

**What's new**

After CZI import, the workspace now shows what was already produced (max projections, DAPI previews) and points you to **Atlas alignment** as the next step instead of re-running Max Projection. Opening an existing project rescans output folders once and fills in missing run selections.

**Changes**

- CZI wizard finish step and workspace banner explain next steps (alignment; counterstain cleanup if alignment is hard).
- Completed tasks discovers CZI max runs at the correct folder depth and lists DAPI/preview counts from import.
- Project open validates stored active runs against disk; clears broken slugs; auto-selects when only one run exists per role.

**Commit subject**

Import handoff UX and legacy run discovery on project open

---

## v3.2.1

**What's new**

Network fair-share now throttles mapped NAS drives (e.g. `Z:\`) when you list them in the shared config, not only UNC paths. Local disk I/O is left unthrottled.

**Changes**

- `nas_path_prefixes` in `%ProgramData%\MasonJar\io-fairshare\config.json` (e.g. `["Z:\\"]`).

**Commit subject**

Fix NAS fair-share for mapped drive letters on Windows

---

## v3.2.0

**What's new**

When several people run Mason Jar on the same Windows compute server, pipeline jobs now **share NAS bandwidth fairly** instead of one instance saturating the network. Each active job gets a slice of the link speed; when fewer jobs are running, each job can use more. Turn it on from the start hub under **Network sharing** (on by default). Link speed can auto-detect or be set manually if your server has multiple NICs.

**Changes**

- Adaptive fair-share for heavy pipeline steps (max, align, detect, CZI extract, batch, etc.).
- Start hub and workspace show active job count and approximate Mbps share.
- Sharpen reduces parallel workers when bandwidth share is small.

**Commit subject**

Share NAS bandwidth fairly when many Mason Jar instances run on one server

**Commit body**

Mason Jar 3.2.0 adds machine-wide adaptive I/O fair-share so pipeline jobs on a shared compute server split NAS bandwidth instead of one instance saturating the NIC. Configure link speed from the start hub Network sharing section.

---

## v3.1.0

**What's new**

Sharpen and Top-hat wizards are faster and easier to tune: pan and zoom stay responsive, display min/max sliders help you see faint tissue, and you click **Preview filter** when you want to see the filtered result. The Viewer/Editor gives images more room with a collapsible parcellation drawer, a background-channel dropdown (including pipeline DAPI when previews are missing), and a search box for paint-brush regions. Bulk parcellation now uses an **Included regions** list with multi-select. Count Brain no longer has the confusing **Save layer info** checkbox — use parcellation instead. Tissue edge cleanup only shows green/red overlays after you start editing a mask.

**Changes**

- Preprocess wizards: manual filter preview, optional auto-refresh after pan, display window sliders.
- Viewer/Editor: QSplitter parcellation drawer, channel combo, region search; bulk parcellation removed from single-section view.
- Parcellation (bulk): include-region dual list; `included_region_ids` in config.
- Count: removed layer-info option from UI and batch.
- Tissue cleanup: mask overlay hidden until edit; auto mask polarity fix.

**Commit subject**

Sharpen and Adjust UX improvements plus parcellation include list for v3.1

**Commit body**

Mason Jar 3.1.0 makes preprocess previews manual and fast, reworks the Adjust layout, switches bulk parcellation to included regions, drops Count layer info, and fixes tissue mask overlay semantics.

---

## v3.0.0

**What's new**

**Semi-manual tissue edge cleanup** (renamed from Tissue edge cleanup) is easier to use: click to place trace points, a fixed preview (no accidental pan/zoom), green overlay for tissue you keep and red for areas that will be removed, and a gentler **Attempt Auto** with adjustable **Edge shrink**. The eraser cleans up small stray pixels after you paint. **Sharpen** and **Top-hat filter** wizards now always show the **Source dataset** picker and a **Back to preprocessing** link. The preprocessing menu lists **DAPI cleanup** before the semi-manual tissue tool.

**Changes**

- Trace + Auto: fixed crash when finishing a trace; clearer click-to-trace instructions.
- Tissue wizard: static image, green/red masks, orphan pixel cleanup after erasing.
- Sharpen / Top-hat: source dataset row always visible; exit to preprocessing menu.

**Commit subject**

Improve tissue cleanup wizard and preprocess navigation for v3

**Commit body**

Renames the tissue edge tool, fixes trace JSON and mask UX, and makes sharpen/top-hat wizards easier to leave. Ships as Mason Jar 3.0.0 with desktop builds for macOS and Windows.

---

## v2.4.11

**What's new**

**Tissue edge cleanup** is a new wizard under Image preprocessing. Open a DAPI preview for each section, use **Attempt Auto** or **Trace edge then Auto** to build a keep mask, touch up with the **Eraser**, then confirm and **Apply**. Mason Jar backs up originals under `.masonjar/tissue_cleanup_backup/` and updates DAPI previews, orient previews, original-scan z-stacks, and max/sharpen/top-hat TIFFs for edited sections only. If you already ran **Align**, run it again after cleanup.

**Changes**

- New wizard: Tissue edge cleanup (4 steps: mask, confirm, apply, summary).
- Shared bundle path list includes sharpen and top-hat outputs when masking a section.

**Commit subject**

Add tissue edge cleanup wizard for section masks

**Commit body**

New preprocess wizard masks stray tissue at scan edges on DAPI previews, then applies keep masks across bundle images with backup. Re-run Align after cleanup if alignment was already done.

---

## v2.4.10

**What's new**

When you rotate a slice in Orient, then flip it, the preview keeps both steps instead of looking like only a flip or only a rotation. The same behavior applies in the CZI import wizard (Orient step) and in **Orient slices** from the start menu, and what you see in the preview matches what gets written when you click **Apply geometry**.

**Changes**

- Orient and CZI import: rotate and flip actions stack in the order you click them.
- CZI import (2.4.7–2.4.9): more reliable mosaic and counterstain reads; wizard reprobe and multi-Z extract fixes on Windows.

**Commit subject**

Orient preview keeps rotate and flip steps together

**Commit body**

Rotating then flipping a slice no longer reset the preview. Orient slices and the CZI wizard now apply geometry clicks in order so the preview matches the files written on disk.
