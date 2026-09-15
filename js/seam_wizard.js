"use strict";

// Vertical tile-seam correction wizard (Session-2, 2026-09-03).
// Modeled on sharpen_wizard.js / tophat_wizard.js: thin config object handed
// to preprocess_wizard.wirePreprocessWizard(), which owns slice listing
// (already natural-sorted), ROI preview plumbing, and the 1x2
// Original|Corrected comparison view. Detection is automatic (autocorrelation
// over 40-400px periods); the only exposed control is the ±band (px) used to
// average brightness on each side of a detected seam before computing the
// step to remove. See resources/app/py/seam_correct.py and
// _coordination/seam_prototype.py for the algorithm + validation numbers.
//
// menu_category.js entry: DONE. All shared-file wiring below (main.js IPC
// handlers, preprocess_wizard.js's includeDapiBranch/comparisonPreview/
// onPreviewData opt-ins, pipeline_runs.js's RUN_STEP_CONFIG.seam entry) has
// also landed -- see STATUS.md "Seam 현황" for the history. This page is
// fully wired: menu -> wizard -> preview -> batch, DAPI default branch,
// Original|Corrected comparison preview, and Auto-tune band (2026-09-04,
// grid search over seam_correct.py's residual() metric -- see
// _autotune_band() there and onPreviewData()/syncSeamAutotuneUi() below).

var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var preprocessWizard = require("./preprocess_wizard");
var projectIndexBusy = require("./project_index_busy");

function getToolParams() {
	var bandEl = document.getElementById("seamBand");
	var autotuneEl = document.getElementById("seamAutotune");
	var modeEl = document.getElementById("seamCorrectionMode");
	return {
		band: bandEl ? Number(bandEl.value) : 4,
		autotune: !!(autotuneEl && autotuneEl.checked),
		seamMode: modeEl ? modeEl.value : "auto",
	};
}

// Manual Band (px) is meaningless while auto-tune is searching for it --
// mirrors basic_wizard.js's syncAutotuneUi() disabling smoothFlat/smoothDark.
function syncSeamAutotuneUi() {
	var bandEl = document.getElementById("seamBand");
	var autotuneEl = document.getElementById("seamAutotune");
	if (bandEl) {
		bandEl.disabled = !!(autotuneEl && autotuneEl.checked);
	}
}

function syncSeamModeAvailability() {
	var modeEl = document.getElementById("seamCorrectionMode");
	var state = window.masonjarCziState || {};
	if (!modeEl) return;
	var known = modeEl.querySelector('option[value="known_geometry"]');
	var available = state.knownGeometryAvailable !== false;
	if (known) { known.disabled = !available; known.textContent = available ? "Known-geometry" : "Known-geometry (unavailable)"; }
	if (!available && modeEl.value === "known_geometry") modeEl.value = "grid_estimated";
}

// Populates #seamBand + the result note once a preview response with
// autotuned:true comes back -- same shape as basic_wizard.js's own
// `if (payload.autotuned) {...}` handling of smoothFlat/smoothDark, wired
// through preprocess_wizard.js's opts.onPreviewData hook (2026-09-04) since
// seam uses the shared wirePreprocessWizard() preview-result handler rather
// than a bespoke one like basic_wizard.js has.
// Which correction path actually ran (2026-09-xx, user request: "적용된
// 방법이 명시됐으면 좋겠어 -- known-geometry인지 grid 추정인지"). Always
// updates on any successful preview (unlike the auto-tune block below,
// which only fires when data.autotuned) since the mode is meaningful
// whether or not auto-tune was even checked -- known-geometry doesn't use
// auto-tune at all (see seam_correct.py's run_preview():
// seam_known_geometry_autotune_skipped). data.seamMode/n_seams_vertical/
// n_seams_horizontal are new PREVIEW_JSON fields (seam_correct.py,
// known-geometry implementation) alongside the pre-existing band/autotuned/
// autotune_residual ones.
function updateSeamModeDisplay(data) {
	var modeEl = document.getElementById("seamModeResult");
	if (!modeEl) {
		return;
	}
	if (!data || !data.seamMode) {
		modeEl.textContent = "";
		modeEl.className = "small mb-2";
		return;
	}
	var select = document.getElementById("seamCorrectionMode");
	if (data.seamMode === "known_geometry") {
		var vCount = data.n_seams_vertical != null ? data.n_seams_vertical : 0;
		var hCount = data.n_seams_horizontal != null ? data.n_seams_horizontal : 0;
		modeEl.textContent =
			"Mode: Known-geometry (CZI tile grid) \u2014 " +
			vCount +
			" vertical + " +
			hCount +
			" horizontal seam(s)";
		modeEl.className = "small text-success mb-2";
	} else {
		var nSeams = data.n_seams != null ? data.n_seams : null;
		var nRecovered = data.n_seams_recovered != null ? data.n_seams_recovered : 0;
		var text = "Mode: Grid-estimated (autocorrelation, vertical only)";
		if (nSeams != null) {
			text += " — " + nSeams + " seam(s)";
		}
		// 2026-09-09: a seam that the periodic-comb guess missed but a
		// full-height-consistency recovery pass found separately (see
		// seam_correct.py correct()/_validated_seam_positions() docstring) --
		// surfaced here so a repeat of the (3)/(4) case (STATUS.md) is
		// visible in the UI instead of only in logs.
		if (nRecovered > 0) {
			text += " (" + nRecovered + " recovered — period estimate looked off for this slice)";
		}
		modeEl.textContent = text;
		modeEl.className = "small text-muted mb-2";
	}
}

// Keep the status note synchronized before the asynchronous preview returns.
// The result callback will replace this provisional text with detected seam
// counts and the mode actually used by Python.
function updateSeamModeSelectionDisplay() {
	var resultEl = document.getElementById("seamModeResult");
	var select = document.getElementById("seamCorrectionMode");
	if (!resultEl || !select) return;
	var label = select.value === "auto" ? "Auto (Known-geometry preferred)" : select.value === "grid_estimated" ? "Grid-estimated" : "Known-geometry";
	resultEl.textContent = "Selected mode: " + label + " (preview pending)";
	resultEl.className = "small text-muted mb-2";
}

function onPreviewData(data) {
	updateSeamModeDisplay(data);
	if (!data || !data.autotuned) {
		return;
	}
	var bandEl = document.getElementById("seamBand");
	var resultEl = document.getElementById("seamAutotuneResult");
	if (data.band != null && bandEl) {
		bandEl.value = String(data.band);
	}
	if (resultEl) {
		resultEl.textContent =
			data.band != null
				? "Auto-tuned \u2014 band " +
				  data.band +
				  "px" +
				  (data.autotune_residual != null
						? " (residual " + data.autotune_residual + ")"
						: "")
				: "Auto-tune ran but returned no value (see log).";
	}
}

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	preprocessWizard.wirePreprocessWizard({
		stepId: "seam",
		sourceStorageKey: "masonjar.seam.sourceDataset",
		configFileName: "seam_run_config.json",
		// Seam correction is most often run on the DAPI counterstain (the
		// channel where tile-boundary seams are usually most visible), so
		// default the signal-branch picker to DAPI. Applied in
		// preprocess_wizard.js 2026-09-04 (was queued when this comment was
		// first written; kept the note for context, no longer a no-op).
		includeDapiBranch: true,
		// "Also seam-correct these channels" checkboxes (2026-09-05): when the
		// main Signal branch dropdown is on DAPI (the default), the wizard
		// also lists every other signal branch as an opt-in checkbox next to
		// Source dataset. Checked branches are seam-corrected in the same
		// batch run, each into its own branch/seam/<slug> output -- see
		// preprocess_wizard.js's refreshExtraChannels()/buildExtraChannelConfigs()
		// and seam_correct.py's run_batch() "extra_channels" handling. DAPI
		// remains the only channel processed unless the user opts more in.
		extraChannelsCheckboxes: true,
		// Preview shows Original (left) beside Corrected (right) as a 1x2
		// grid instead of a single toggled image -- easier to judge whether
		// the offset correction actually removed a seam. Same comparison
		// renderer BaSiC uses, generalized into preprocess_wizard.js
		// 2026-09-04 (opt-in, sharpen/tophat unaffected).
		comparisonPreview: true,
		// "Preview correction" is a checkbox here, not a button (2026-09-09,
		// user request). Checking it fires a preview immediately (the
		// existing #previewFilterBtn "click" wiring in preprocess_wizard.js
		// doesn't care whether the element is a <button> or a checkbox --
		// both dispatch "click"). This opt-in additionally makes
		// preprocess_wizard.js's onSliceChange() re-run the preview
		// automatically on every subsequent slice change for as long as the
		// checkbox stays checked, instead of requiring a manual click each
		// time. sharpen/tophat/basic don't set this -- their
		// #previewFilterBtn stays a plain button, unaffected.
		autoPreviewOnSliceChange: true,
		runIpc: "runSeam",
		previewIpc: "runSeamPreview",
		previewResultIpc: "seamPreviewResult",
		resultIpc: "seamResult",
		killRunIpc: "killSeam",
		killPreviewIpc: "killSeamPreview",
		getToolParams: getToolParams,
		// "Auto-tune band" checkbox (2026-09-04): grid-searches candidate
		// Band (px) values in seam_correct.py and reports the one with the
		// lowest residual seam signal, mirroring BaSiC's "Auto-tune
		// smoothness" UX. No external library equivalent exists for this
		// (unlike BaSiC/BaSiCPy), so the search itself lives in Python --
		// see _autotune_band() in seam_correct.py.
		onPreviewData: onPreviewData,
		buildSlugContext: function (base, params) {
			return {
				sortedStems: base.sortedStems,
				subsetCount: base.subsetCount,
				sourceKind: base.sourceKind,
				sourceRunRel: base.sourceRunRel,
				band: params.band,
			};
		},
	});
	var seamAutotuneEl = document.getElementById("seamAutotune");
	if (seamAutotuneEl) {
		seamAutotuneEl.addEventListener("change", syncSeamAutotuneUi);
	}
	var seamModeEl = document.getElementById("seamCorrectionMode");
	if (seamModeEl) {
		seamModeEl.addEventListener("change", function () {
			updateSeamModeSelectionDisplay();
			// Changing the algorithm invalidates the corrected bitmap. When
			// live preview is enabled, request a fresh preview immediately.
			var previewToggle = document.getElementById("previewFilterBtn");
			if (previewToggle && previewToggle.checked) {
				previewToggle.dispatchEvent(new Event("click"));
			}
		});
	}
	syncSeamAutotuneUi();
	syncSeamModeAvailability();
	updateSeamModeSelectionDisplay();
});
