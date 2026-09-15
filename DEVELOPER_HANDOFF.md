# Mason Jar 7.0.2-MC.1 — Developer Handoff

## What to review

This source tree contains improvements made after the original 7.0.2 source/runtime baseline. The main areas changed are CZI import stability and memory use, dotted filename handling, Seam Correction modes, Adjustment Viewer performance and interaction, and Atlas/Tissue-cleanup UI.

## Source and test runtime

- Source: this folder.
- Matching test runtime: `D:\Claude\masonjar-7.0.2-MC.1-improving-win32-x64`.
- The test runtime is useful for immediate behavior review. It was updated through selective, SHA-256-verified synchronization during development.
- A full Windows release package is available at `out\make\zip\win32\x64\masonjar-win32-x64-7.0.2-MC.1.zip`. Verify its SHA-256 immediately before transfer.

## Important behavior decisions

- Use exact peak scaling for CZI uint16-to-uint8 conversion. Real-data testing showed sampled peak scaling can noticeably brighten output.
- Preserve the entire filename stem, including periods, for all CZI-related file matching.
- Prefer Known-geometry Seam Correction when seamgrid metadata exists; otherwise use Grid-estimated.
- Adjustment Viewer Seam Correction is live. It should not depend on Process-generated correction PNG files.
- Automatic and mandatory update checks are disabled for this custom build. A manual Settings > Updates check targets `mirihara0523-hue/masonjar` only.

## Before distribution

1. Test the packaged executable with a dotted-filename CZI import, DAPI switching, both Seam Correction modes, Adjustment Viewer interaction, and Tissue cleanup.
2. If publishing a subsequent build, choose a new version in `package.json` and run `node scripts\build-release.js --windows-only` from this source root.

## Technical references

- Current detailed status: `STATUS.md` in the coordination folder.
- Focused tests: `scripts\test-czi-*.py`, `scripts\test-adjust-*.py`, and `python\tests\test_adjust_*.py`.
- Full release build: `scripts\build-release.js`.
- Development-only selective deployment: `scripts\sync-improving.ps1`.
