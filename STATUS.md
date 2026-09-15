# Mason Jar — Current Coordination Status

Updated: 2026-09-13

## Project locations

- Source: `D:\Claude\masonjar-7.0.2-MC.1-improving`
- Test runtime: `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64`
- Coordination records: `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64_coordination`
- Original reference source: `D:\Claude\masonjar-7.0.2`
- Original reference runtime: `D:\Claude\masonjar-win32-x64`

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