"use strict";

var fs = require("fs");
var path = require("path");
var url = require("url");
var pageInit = require("./page_init");
var project = require("./project");
var pipelineRuns = require("./pipeline_runs");
var cziImport = require("./czi_import");
var importHandoff = require("./import_handoff");
var orientGeometry = require("./orient_geometry");
var geometryState = require("./geometry_state");
var geometryHistory = require("./geometry_history");
var branding = require("./branding");
var reextractPicker = require("./czi_reextract_picker");

var ipc = require("electron").ipcRenderer;

var wizardState = {
	flow: "full",
	step: 1,
	parentDir: "",
	bundleRoot: "",
	projectFilename: "",
	cziSourceDirs: [],
	cziImport: cziImport.buildDefaultCziImport(""),
	importResult: null,
	repairMode: false,
	repairTargets: [],
	reextractedSliceIds: [],
	reextractPreviewCacheBuster: null,
	orientDisplayChannel: cziImport.ORIENT_DISPLAY_DAPI,
};

var reextractState = reextractPicker.createPickerState();

var extractRunning = false;
// True once an extraction has finished successfully this session. Lets setStep()
// re-show "Continue to Orient" when the user navigates BACK to step 4, instead of
// stranding them with only "Cancel wizard".
var extractCompleted = false;
var geometryRunning = false;
var probeInFlight = false;
var extractGotPythonAck = false;
var extractHeartbeatTimer = null;
var extractGapWatchdogTimer = null;
var extractWaitStartedAt = 0;
var extractLastPythonActivityAt = 0;
var extractGapEmitted = false;
var EXTRACT_LOG_MAX_LINES = 2000;
var PROBE_TIMEOUT_MS = 60 * 60 * 1000;
/** Full-folder retries when the shared Python worker dies mid-probe. */
var PROBE_MAX_ATTEMPTS = 3;
var PROBE_LOG_MAX_LINES = 1500;

function sourceDirMatches(a, b) {
	return cziImport.canonicalSourceDir(a) === cziImport.canonicalSourceDir(b);
}

function qs(id) {
	return document.getElementById(id);
}

function yieldToUi() {
	return new Promise(function (resolve) {
		requestAnimationFrame(function () {
			setTimeout(resolve, 0);
		});
	});
}

function updateBundlePathPreview() {
	var parentDir = wizardState.parentDir;
	var nameEl = qs("projectName");
	var pathEl = qs("bundlePath");
	if (!pathEl) {
		return;
	}
	if (!parentDir || !nameEl || !nameEl.value.trim()) {
		pathEl.value = "";
		wizardState.bundleRoot = "";
		wizardState.projectFilename = "";
		return;
	}
	var resolved = project.resolveNewBundlePath(parentDir, nameEl.value);
	pathEl.value = resolved.bundleRoot;
	wizardState.bundleRoot = resolved.bundleRoot;
	wizardState.projectFilename = resolved.projectFilename;
}

function isReextractFlow() {
	return wizardState.flow === "reextract";
}

function reextractGeometryScope() {
	return (
		wizardState.cziImport &&
		wizardState.cziImport.reextract_geometry_scope
	);
}

function isReextractPartialOrient() {
	return isReextractFlow() && !!reextractGeometryScope();
}

function isReextractMissingGeometryHistory() {
	if (!isReextractPartialOrient() || !wizardState.bundleRoot) {
		return false;
	}
	var sliceIds = orientGridSliceIds();
	if (!sliceIds.length) {
		return false;
	}
	return geometryHistory.reextractSliceIdsLackHistory(
		wizardState.bundleRoot,
		sliceIds,
	);
}

function orientGridSliceIds() {
	var scope = reextractGeometryScope();
	if (isReextractPartialOrient() && scope) {
		var scoped = cziImport.sliceIdsInReextractScope(scope);
		if (scoped.length) {
			return scoped;
		}
	}
	if (
		isReextractFlow() &&
		wizardState.reextractedSliceIds &&
		wizardState.reextractedSliceIds.length
	) {
		return wizardState.reextractedSliceIds.slice();
	}
	return cziImport.collectSliceIds(wizardState.cziImport);
}

function shouldApplyCssPreviewForTile(sliceId, geom) {
	if (orientGeometry.isIdentityGeometry(geom)) {
		return false;
	}
	var scope = reextractGeometryScope();
	if (!isReextractPartialOrient() || !scope) {
		return true;
	}
	return cziImport.isDisplayChannelInReextractScope(
		sliceId,
		wizardState.orientDisplayChannel,
		scope,
		wizardState.cziImport,
	);
}

function reextractPanelIdForStep(step) {
	if (step === 1) {
		return "stepRe1";
	}
	if (step === 2) {
		return "stepRe2";
	}
	if (step === 3) {
		return "stepRe3";
	}
	return "step" + step;
}

function fullImportPanelIdForStep(step) {
	return "step" + step;
}

function updateReextractUiVisibility(active) {
	var title = qs("cziWizardTitle");
	var intro = qs("cziWizardIntro");
	if (title) {
		title.textContent = active ? "Re-import sections from CZI" : "Import from Zeiss CZI";
	}
	if (intro) {
		intro.classList.toggle("d-none", active);
	}
	updateOrientStepChrome();
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	var reextractLabels = ["1. Sections", "2. Channels", "3. Confirm", "4. Extract", "5. Orient", "6. Finish"];
	var fullLabels = [
		"1. Location",
		"2. Scan",
		"3. Channels / Renaming",
		"4. Extract",
		"5. Orient",
		"6. Finish",
	];
	for (var i = 0; i < pills.length; i++) {
		var stepNum = Number(pills[i].getAttribute("data-step"));
		if (active && stepNum >= 1 && stepNum <= 6) {
			pills[i].textContent = reextractLabels[stepNum - 1];
		} else if (!active && stepNum >= 1 && stepNum <= 6) {
			pills[i].textContent = fullLabels[stepNum - 1];
		}
	}
}

function setStep(step) {
	wizardState.step = step;
	var panels = document.querySelectorAll(".wizard-panel");
	for (var i = 0; i < panels.length; i++) {
		panels[i].classList.add("d-none");
		panels[i].setAttribute("hidden", "");
	}
	var activeId = isReextractFlow() && step <= 3 ? reextractPanelIdForStep(step) : fullImportPanelIdForStep(step);
	var active = qs(activeId);
	if (active) {
		active.classList.remove("d-none");
		active.removeAttribute("hidden");
	}
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	for (var p = 0; p < pills.length; p++) {
		var pillStep = Number(pills[p].getAttribute("data-step"));
		pills[p].classList.remove("active", "disabled", "wizard-step-done", "wizard-step-revisit");
		if (pillStep === step) {
			pills[p].classList.add("active");
		} else if (pillStep < step) {
			pills[p].classList.add("wizard-step-done");
			if (pillStep === 5 && step === 6 && !geometryRunning) {
				pills[p].classList.add("wizard-step-revisit");
			}
		} else {
			pills[p].classList.add("disabled");
		}
	}
	var parent = qs("parent");
	if (parent) {
		parent.scrollTop = 0;
	}
	window.scrollTo(0, 0);
	updateWizardCancelVisibility();
	updateOrientStepChrome();
	if (step !== 4) {
		setStep4ContinueVisible(false);
	} else if (extractCompleted && !extractRunning) {
		// Returning to step 4 after a completed extraction (e.g. Back from Orient):
		// re-expose the forward path so this step is not a dead-end.
		setStep4ContinueVisible(true);
	}
	if (step === 5) {
		hydrateReextractOrientFromSettings();
		populateOrientDisplayChannelSelect();
		updateOrientApplySummary();
		updateOrientPreviewBanner();
		renderOrientationGrid();
	}
}

function updateWizardCancelVisibility() {
	var footer = qs("wizardCancel");
	if (footer) {
		footer.classList.toggle(
			"d-none",
			(wizardState.step === 4 && extractRunning) ||
				(wizardState.step === 6 && geometryRunning),
		);
	}
}

function setFinishNavDisabled(disabled) {
	var hub = qs("finishHub");
	var openBtn = qs("openWorkspace");
	var reviewBtn = qs("reviewOrientation");
	var handoffPanel = qs("importHandoffPanel");
	if (hub) {
		hub.classList.toggle("d-none", disabled);
	}
	if (openBtn) {
		openBtn.classList.toggle("d-none", disabled);
	}
	if (reviewBtn) {
		reviewBtn.classList.toggle("d-none", disabled);
	}
	if (handoffPanel) {
		handoffPanel.classList.toggle("d-none", disabled);
	}
}

function populateImportHandoffPanel() {
	var panel = qs("importHandoffPanel");
	var list = qs("importHandoffDoneList");
	if (!panel || !list || !wizardState.bundleRoot) {
		return;
	}
	var proj = project.getProject() || {
		settings: { czi_import: wizardState.cziImport },
		processing: project.defaultProcessing(),
	};
	var handoff = importHandoff.getImportHandoffState(wizardState.bundleRoot, proj);
	list.innerHTML = "";
	var items = [];
	if (handoff.maxRunLabel) {
		items.push("Max projection — " + handoff.maxRunLabel);
	}
	if (handoff.dapiCount > 0) {
		items.push("Counterstain (DAPI) — " + handoff.dapiCount + " PNG preview(s) in 00_dapi");
	}
	if (handoff.previewCount > 0) {
		items.push("Orient previews — " + handoff.previewCount + " PNG(s) in _previews");
	}
	if (handoff.geometryAppliedAt) {
		items.push("Orientation applied — " + handoff.geometryAppliedAt);
	}
	for (var i = 0; i < items.length; i++) {
		var li = document.createElement("li");
		li.textContent = items[i];
		list.appendChild(li);
	}
	panel.classList.remove("d-none");
}

function verboseFinishLog(msg) {
	console.log("[CziWizard]", msg);
	var el = qs("finishLog");
	if (el) {
		el.textContent = (el.textContent ? el.textContent + "\n" : "") + msg;
		var lines = el.textContent.split("\n");
		if (lines.length > EXTRACT_LOG_MAX_LINES) {
			el.textContent = lines.slice(lines.length - EXTRACT_LOG_MAX_LINES).join("\n");
		}
		el.scrollTop = el.scrollHeight;
	}
}

function setGeometryActivity(msg, pct) {
	var bar = qs("finishProgress");
	var status = qs("finishStatus");
	if (status && msg) {
		status.textContent = msg;
	}
	if (bar && typeof pct === "number") {
		bar.style.width = String(pct) + "%";
		bar.setAttribute("aria-valuenow", String(pct));
		if (pct >= 100) {
			bar.classList.remove("progress-bar-striped", "progress-bar-animated");
		} else {
			bar.classList.add("progress-bar-striped", "progress-bar-animated");
		}
	}
}

function countNonIdentityGeometry() {
	return orientGeometry.countNonIdentityGeometry(
		wizardState.cziImport.geometry || {},
		cziImport.collectSliceIds(wizardState.cziImport),
	);
}

function geometryCssTransform(geom) {
	return orientGeometry.geometryCssTransform(geom);
}

function confirmLeaveDuringJob() {
	return confirm(
		"A CZI import job is still running. Leave anyway? Progress may be incomplete.",
	);
}

function verboseExtractLog(msg) {
	console.log("[CziWizard]", msg);
	var el = qs("extractLog");
	if (el) {
		el.textContent = (el.textContent ? el.textContent + "\n" : "") + msg;
		// Cap scrollback for long jobs (324+ items with per-plane logs).
		var lines = el.textContent.split("\n");
		if (lines.length > EXTRACT_LOG_MAX_LINES) {
			el.textContent = lines.slice(lines.length - EXTRACT_LOG_MAX_LINES).join("\n");
		}
		el.scrollTop = el.scrollHeight;
	}
}

function verboseProbeLog(msg) {
	console.log("[CziWizard]", msg);
	var el = qs("probeLog");
	if (el) {
		el.textContent = (el.textContent ? el.textContent + "\n" : "") + msg;
		var lines = el.textContent.split("\n");
		if (lines.length > PROBE_LOG_MAX_LINES) {
			el.textContent = lines.slice(lines.length - PROBE_LOG_MAX_LINES).join("\n");
		}
		el.scrollTop = el.scrollHeight;
	}
}

function truncateStatus(text, maxLen) {
	var s = String(text || "").trim();
	if (s.length <= maxLen) {
		return s;
	}
	return s.slice(0, maxLen - 1) + "…";
}

function clearExtractWaitTimers() {
	if (extractHeartbeatTimer) {
		clearInterval(extractHeartbeatTimer);
		extractHeartbeatTimer = null;
	}
	if (extractGapWatchdogTimer) {
		clearInterval(extractGapWatchdogTimer);
		extractGapWatchdogTimer = null;
	}
}

function markPythonActivity() {
	extractLastPythonActivityAt = Date.now();
	extractGapEmitted = false;
	if (!extractGotPythonAck) {
		extractGotPythonAck = true;
		clearExtractWaitTimers();
		setExtractIndeterminate(false);
	}
}

function startExtractWaitFeedback() {
	extractGotPythonAck = false;
	extractWaitStartedAt = Date.now();
	extractLastPythonActivityAt = Date.now();
	extractGapEmitted = false;
	clearExtractWaitTimers();
	setExtractIndeterminate(true);
	verboseExtractLog("Waiting for Python worker…");
	if (extractHeartbeatTimer) {
		clearInterval(extractHeartbeatTimer);
	}
	extractHeartbeatTimer = setInterval(function () {
		if (extractGotPythonAck || !extractRunning) {
			clearExtractWaitTimers();
			return;
		}
		var elapsed = Math.round((Date.now() - extractWaitStartedAt) / 1000);
		verboseExtractLog("Still waiting for Python worker… (" + elapsed + "s)");
	}, 1500);
	extractGapWatchdogTimer = setInterval(function () {
		if (!extractRunning) {
			return;
		}
		var gap = Date.now() - extractLastPythonActivityAt;
		if (gap >= 2000 && !extractGapEmitted) {
			extractGapEmitted = true;
			verboseExtractLog("(local) waiting for Python output…");
			var status = qs("extractStatus");
			if (status) {
				status.textContent = truncateStatus("Waiting for Python output…", 120);
			}
		}
	}, 500);
}

function mapExtractDisplayPct(rawPct, message) {
	var text = String(message || "");
	if (rawPct >= 100 || /complete/i.test(text)) {
		return 100;
	}
	if (/^Ready —/i.test(text)) {
		return 20;
	}
	if (/max project/i.test(text)) {
		return 92 + Math.min(7, Math.round((rawPct / 100) * 7));
	}
	if (/Extracting\s+\S+\s+scene/i.test(text)) {
		if (rawPct >= 22 && rawPct <= 92) {
			return rawPct;
		}
		return 22 + Math.round(Math.min(100, Math.max(0, rawPct)) * 0.70);
	}
	if (rawPct >= 3 && rawPct <= 22) {
		return rawPct;
	}
	return Math.min(22, Math.max(0, rawPct));
}

function formatExtractStatus(rawPct, message) {
	var text = String(message || "").trim();
	if (rawPct >= 100 || /complete/i.test(text)) {
		return "Extract complete";
	}
	if (/^Ready —/i.test(text)) {
		return text;
	}
	if (/max project/i.test(text)) {
		return "Max projecting signal channels…";
	}
	var prefer =
		/Importing|Reading Z|Writing|Opening|still loading|Loading |Libraries ready|Arguments OK|Starting extract/i;
	if (prefer.test(text)) {
		return truncateStatus(text, 120);
	}
	var m = text.match(/Extracting\s+(\S+)\s+scene\s+(\d+)\s+ch\s+(\d+)/i);
	if (m) {
		return "Extracting " + m[1] + " scene " + m[2] + " channel " + m[3] + "…";
	}
	return truncateStatus(text || "Extracting…", 120);
}

function setExtractIndeterminate(indeterminate) {
	var bar = qs("extractProgress");
	if (!bar) {
		return;
	}
	if (indeterminate) {
		bar.style.width = "100%";
		bar.classList.add("progress-bar-striped", "progress-bar-animated");
		bar.removeAttribute("aria-valuenow");
	} else {
		bar.setAttribute("aria-valuemin", "0");
		bar.setAttribute("aria-valuemax", "100");
	}
}

function setExtractActivity(msg, pct) {
	var bar = qs("extractProgress");
	var status = qs("extractStatus");
	if (status && msg) {
		status.textContent = msg;
	}
	if (bar && typeof pct === "number") {
		setExtractIndeterminate(false);
		bar.style.width = String(pct) + "%";
		bar.setAttribute("aria-valuenow", String(pct));
		if (pct >= 100) {
			bar.classList.remove("progress-bar-striped", "progress-bar-animated");
		} else {
			bar.classList.add("progress-bar-striped", "progress-bar-animated");
		}
	}
}

function setExtractNavDisabled(disabled) {
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	for (var i = 0; i < pills.length; i++) {
		if (disabled) {
			pills[i].style.pointerEvents = "none";
		} else {
			pills[i].style.pointerEvents = "";
		}
	}
	if (!disabled) {
		setStep(wizardState.step);
	}
}

function setStep4ContinueVisible(visible) {
	var btn = qs("step4Continue");
	if (btn) {
		btn.classList.toggle("d-none", !visible);
	}
}

function countKeptChannels(cfg) {
	var channels = cfg.channels || [];
	var n = 0;
	for (var i = 0; i < channels.length; i++) {
		if (channels[i].keep && channels[i].role !== cziImport.ROLE_UNUSED) {
			n++;
		}
	}
	return n;
}

function projectStemForImport() {
	var nameEl = qs("projectName");
	var raw = (nameEl && nameEl.value.trim()) || path.basename(wizardState.bundleRoot || "");
	return cziImport.sanitizeSliceStem(raw.replace(/\.masonjar$/i, ""));
}

function applySliceNumberingDefault() {
	if ((wizardState.cziSourceDirs || []).length > 1) {
		wizardState.cziImport.slice_numbering = cziImport.SLICE_NUMBERING_RENAME;
	} else if (!wizardState.cziImport.slice_numbering) {
		wizardState.cziImport.slice_numbering = cziImport.SLICE_NUMBERING_PRESERVE;
	}
}

function refreshSliceOrder() {
	applySliceNumberingDefault();
	cziImport.buildSliceOrder(wizardState.cziImport, projectStemForImport());
}

function sortedCziFilesForDisplay() {
	var files = (wizardState.cziImport.files || []).slice();
	var imp = wizardState.cziImport;
	files.sort(function (a, b) {
		var scanA = a.scan_index != null ? a.scan_index : 0;
		var scanB = b.scan_index != null ? b.scan_index : 0;
		if (scanA !== scanB) {
			return scanA - scanB;
		}
		return cziImport.naturalCompare(
			{
				basename: a.basename,
				path: a.path,
				scan_index: a.scan_index,
				scene_index: 0,
			},
			{
				basename: b.basename,
				path: b.path,
				scan_index: b.scan_index,
				scene_index: 0,
			},
			imp,
		);
	});
	return files;
}

function countFilesForSourceDir(dir) {
	var target = cziImport.canonicalSourceDir(dir);
	return (wizardState.cziImport.files || []).filter(function (f) {
		return cziImport.canonicalSourceDir(f.source_dir) === target && !f.error;
	}).length;
}

function resyncScanIndices() {
	var dirs = (wizardState.cziSourceDirs || []).map(function (d) {
		return cziImport.canonicalSourceDir(d);
	});
	wizardState.cziSourceDirs = dirs;
	wizardState.cziImport.source_dirs = dirs.slice();
	var files = wizardState.cziImport.files || [];
	for (var i = 0; i < files.length; i++) {
		var canon = cziImport.canonicalSourceDir(files[i].source_dir);
		files[i].source_dir = canon;
		var idx = dirs.indexOf(canon);
		files[i].scan_index = idx >= 0 ? idx : 0;
	}
}

function updateProbeStatusSummary() {
	var status = qs("probeStatus");
	var nextBtn = qs("step2Next");
	var dirs = wizardState.cziSourceDirs || [];
	var fileCount = (wizardState.cziImport.files || []).filter(function (f) {
		return !f.error;
	}).length;
	if (status && !probeInFlight) {
		if (!dirs.length) {
			status.textContent = "";
		} else if (fileCount) {
			status.textContent =
				fileCount + " file(s) from " + dirs.length + " folder(s) probed.";
		}
	}
	if (nextBtn) {
		nextBtn.disabled = !fileCount;
	}
}

function refreshAfterProbeIncremental(resetSectionDefault) {
	applySliceNumberingDefault();
	resyncScanIndices();
	refreshSectionIdentifierAfterProbe(!!resetSectionDefault);
	refreshSliceOrder();
	renderSourceDirList();
	renderCziFileTable(sortedCziFilesForDisplay());
	updateProbeStatusSummary();
}

function moveSourceDir(idx, delta) {
	var dirs = wizardState.cziSourceDirs || [];
	var newIdx = idx + delta;
	if (newIdx < 0 || newIdx >= dirs.length) {
		return;
	}
	var tmp = dirs[idx];
	dirs[idx] = dirs[newIdx];
	dirs[newIdx] = tmp;
	resyncScanIndices();
	refreshSliceOrder();
	renderSourceDirList();
	renderCziFileTable(sortedCziFilesForDisplay());
}

function onSectionIdentifierChange(value) {
	wizardState.cziImport.section_identifier = value || null;
	refreshSliceOrder();
	renderSectionIdentifierSelect();
	if (wizardState.step === 3) {
		renderRenamingTable();
	}
	renderCziFileTable(sortedCziFilesForDisplay());
}

function renderSectionIdentifierSelect() {
	var selects = document.querySelectorAll(".section-identifier-select");
	if (!selects.length) {
		return;
	}
	var files = wizardState.cziImport.files || [];
	var candidates = wizardState.sectionIdentifierCandidates || [];
	var current =
		wizardState.cziImport.section_identifier != null
			? String(wizardState.cziImport.section_identifier)
			: "";
	for (var s = 0; s < selects.length; s++) {
		var sel = selects[s];
		sel.innerHTML = "";
		for (var c = 0; c < candidates.length; c++) {
			var cand = candidates[c];
			var opt = document.createElement("option");
			opt.value = cand.prefix != null ? String(cand.prefix) : "";
			opt.textContent = cand.label;
			sel.appendChild(opt);
		}
		if (
			current &&
			!candidates.some(function (cand) {
				return String(cand.prefix || "") === current;
			})
		) {
			var custom = document.createElement("option");
			custom.value = current;
			custom.textContent = current + " (saved)";
			sel.appendChild(custom);
		}
		sel.value = current;
		sel.disabled = !files.length;
	}
}

function refreshSectionIdentifierAfterProbe(resetDefault) {
	var files = wizardState.cziImport.files || [];
	var candidates = cziImport.detectSectionIdentifierCandidates(files);
	wizardState.sectionIdentifierCandidates = candidates;
	if (
		resetDefault ||
		wizardState.cziImport.section_identifier == null ||
		wizardState.cziImport.section_identifier === undefined
	) {
		wizardState.cziImport.section_identifier = cziImport.defaultSectionIdentifier(
			candidates,
			files.length,
		);
	}
	renderSectionIdentifierSelect();
}

function bindSectionIdentifierSelects() {
	var selects = document.querySelectorAll(".section-identifier-select");
	for (var i = 0; i < selects.length; i++) {
		if (selects[i].getAttribute("data-bound") === "1") {
			continue;
		}
		selects[i].setAttribute("data-bound", "1");
		selects[i].addEventListener("change", function (ev) {
			onSectionIdentifierChange(ev.target.value);
		});
	}
}

function syncSliceNumberingRadios() {
	var numbering = wizardState.cziImport.slice_numbering || cziImport.SLICE_NUMBERING_PRESERVE;
	var preserve = qs("sliceNumberingPreserve");
	var rename = qs("sliceNumberingRename");
	if (preserve) {
		preserve.checked = numbering === cziImport.SLICE_NUMBERING_PRESERVE;
	}
	if (rename) {
		rename.checked = numbering === cziImport.SLICE_NUMBERING_RENAME;
	}
}

function setProbeControlsBusy(busy) {
	var addBtn = qs("addCziDir");
	var reprobeBtn = qs("reprobeAllCziDirs");
	if (addBtn) {
		addBtn.disabled = !!busy;
	}
	if (reprobeBtn) {
		reprobeBtn.disabled = !!busy;
	}
}

function renderSourceDirList() {
	var list = qs("cziSourceDirList");
	if (!list) {
		return;
	}
	list.innerHTML = "";
	var dirs = wizardState.cziSourceDirs || [];
	if (!dirs.length) {
		var empty = document.createElement("li");
		empty.className = "list-group-item text-muted small";
		empty.textContent = "No folders added yet.";
		list.appendChild(empty);
		return;
	}
	for (var i = 0; i < dirs.length; i++) {
		var dir = dirs[i];
		var fileCount = countFilesForSourceDir(dir);
		var countLabel = fileCount ? " — " + fileCount + " file(s)" : "";
		var li = document.createElement("li");
		li.className = "list-group-item d-flex justify-content-between align-items-start gap-2";
		var label =
			'<div class="flex-grow-1 text-start">' +
			'<strong>' +
			(i + 1) +
			".</strong> " +
			'<span class="small text-break">' +
			dir +
			countLabel +
			"</span></div>";
		var controls =
			'<div class="btn-group btn-group-sm flex-shrink-0" role="group">' +
			'<button type="button" class="btn btn-outline-secondary" data-move-dir-up="' +
			i +
			'"' +
			(i === 0 ? " disabled" : "") +
			'>↑</button>' +
			'<button type="button" class="btn btn-outline-secondary" data-move-dir-down="' +
			i +
			'"' +
			(i === dirs.length - 1 ? " disabled" : "") +
			'>↓</button>' +
			'<button type="button" class="btn btn-outline-danger" data-remove-dir="' +
			i +
			'">Remove</button>' +
			"</div>";
		li.innerHTML = label + controls;
		list.appendChild(li);
	}
	list.querySelectorAll("[data-move-dir-up]").forEach(function (btn) {
		btn.addEventListener("click", function (ev) {
			moveSourceDir(Number(ev.target.getAttribute("data-move-dir-up")), -1);
		});
	});
	list.querySelectorAll("[data-move-dir-down]").forEach(function (btn) {
		btn.addEventListener("click", function (ev) {
			moveSourceDir(Number(ev.target.getAttribute("data-move-dir-down")), 1);
		});
	});
	list.querySelectorAll("[data-remove-dir]").forEach(function (btn) {
		btn.addEventListener("click", function (ev) {
			var idx = Number(ev.target.getAttribute("data-remove-dir"));
			wizardState.cziSourceDirs.splice(idx, 1);
			wizardState.cziImport.source_dirs = wizardState.cziSourceDirs.slice();
			wizardState.cziImport.files = (wizardState.cziImport.files || []).filter(function (f) {
				return wizardState.cziSourceDirs.some(function (d) {
					return sourceDirMatches(f.source_dir, d);
				});
			});
			renderSourceDirList();
			if (wizardState.cziSourceDirs.length) {
				resyncScanIndices();
				refreshAfterProbeIncremental(false);
			} else {
				wizardState.cziImport.section_identifier = null;
				wizardState.sectionIdentifierCandidates = [];
				renderSectionIdentifierSelect();
				renderCziFileTable([]);
				renderMosaicWarnings([]);
				renderMosaicInfo([]);
				var nextBtn = qs("step2Next");
				if (nextBtn) {
					nextBtn.disabled = true;
				}
				var status = qs("probeStatus");
				if (status) {
					status.textContent = "";
				}
			}
		});
	});
}

function renderRenamingTable() {
	var tbody = qs("renamingTableBody");
	if (!tbody) {
		return;
	}
	tbody.innerHTML = "";
	var order = wizardState.cziImport.slice_order || [];
	var readOnly = wizardState.cziImport.slice_numbering === cziImport.SLICE_NUMBERING_PRESERVE;
	for (var i = 0; i < order.length; i++) {
		var entry = order[i];
		var tr = document.createElement("tr");
		var input =
			'<input type="text" class="form-control form-control-sm slice-id-input" data-ordinal="' +
			entry.ordinal +
			'" value="' +
			String(entry.sliceId || "").replace(/"/g, "&quot;") +
			'"' +
			(readOnly ? " readonly" : "") +
			" />";
		tr.innerHTML =
			"<td>" +
			entry.ordinal +
			"</td><td>" +
			(entry.basename || "—") +
			"</td><td>" +
			entry.scene_index +
			"</td><td>" +
			(entry.originalSliceId || "—") +
			"</td><td>" +
			input +
			"</td><td class=\"small text-break\">" +
			(entry.source_dir || "—") +
			"</td>";
		tbody.appendChild(tr);
	}
	tbody.querySelectorAll(".slice-id-input").forEach(function (inp) {
		inp.addEventListener("change", function (ev) {
			var ordinal = Number(ev.target.getAttribute("data-ordinal"));
			var orderList = wizardState.cziImport.slice_order || [];
			for (var j = 0; j < orderList.length; j++) {
				if (orderList[j].ordinal === ordinal) {
					orderList[j].sliceId = ev.target.value.trim();
					break;
				}
			}
			cziImport.syncScenesFromSliceOrder(wizardState.cziImport);
			updateStep3Validation();
		});
	});
}

function primarySignalLabel(roleKey) {
	if (roleKey === cziImport.ROLE_SIGNAL_SOMATA) {
		return "Somata / rabies";
	}
	if (roleKey === cziImport.ROLE_SIGNAL_NUCLEI) {
		return "Nuclei";
	}
	if (roleKey === cziImport.ROLE_SIGNAL_AXONS) {
		return "Axons";
	}
	if (String(roleKey).indexOf("other:") === 0) {
		return "Other: " + roleKey.slice(6);
	}
	return roleKey;
}

function renderPrimarySignalSelect() {
	var sel = qs("primarySignalRole");
	if (!sel) {
		return;
	}
	var keys = cziImport.collectKeptSignalRoleKeys(wizardState.cziImport);
	if (!keys.length) {
		keys = [cziImport.ROLE_SIGNAL_SOMATA];
	}
	var current = wizardState.cziImport.primary_signal_role || cziImport.ROLE_SIGNAL_SOMATA;
	if (keys.indexOf(current) < 0) {
		current = keys[0];
		wizardState.cziImport.primary_signal_role = current;
	}
	sel.innerHTML = "";
	for (var i = 0; i < keys.length; i++) {
		sel.innerHTML +=
			'<option value="' +
			keys[i] +
			'"' +
			(keys[i] === current ? " selected" : "") +
			">" +
			primarySignalLabel(keys[i]) +
			"</option>";
	}
}

function renderMosaicInfo(files) {
	var box = qs("mosaicInfoBox");
	if (!box) {
		return;
	}
	var infos = cziImport.collectMosaicInfo(files);
	if (!infos.length) {
		box.classList.add("d-none");
		box.innerHTML = "";
		return;
	}
	var MOSAIC_INFO_CAP = 5;
	var html =
		"<strong>Mosaic files detected:</strong> These CZIs contain mosaic tile structure. " +
		"This is normal for ZEN-stitched exports.<ul class=\"mb-0 mt-2\">";
	for (var i = 0; i < Math.min(infos.length, MOSAIC_INFO_CAP); i++) {
		var info = infos[i];
		var label = info.basename ? "<code>" + info.basename + "</code>: " : "";
		html += "<li>" + label + info.message + "</li>";
	}
	if (infos.length > MOSAIC_INFO_CAP) {
		html +=
			'<li class="text-muted">… and ' +
			(infos.length - MOSAIC_INFO_CAP) +
			" more mosaic file(s).</li>";
	}
	html += "</ul>";
	box.innerHTML = html;
	box.classList.remove("d-none");
}

function renderMosaicWarnings(files) {
	var box = qs("mosaicWarningBox");
	if (!box) {
		return;
	}
	var warnings = cziImport.collectMosaicWarnings(files);
	if (!warnings.length) {
		box.classList.add("d-none");
		box.innerHTML = "";
		return;
	}
	var html =
		"<strong>Mosaic stitch check:</strong> One or more files may be unstitched tile sets. " +
		"Stitch mosaics in ZEN before export when possible. Import will continue but geometry may be wrong.<ul class=\"mb-0 mt-2\">";
	for (var i = 0; i < warnings.length; i++) {
		var w = warnings[i];
		var label = w.basename ? "<code>" + w.basename + "</code>: " : "";
		html += "<li>" + label + w.message + "</li>";
	}
	html += "</ul>";
	box.innerHTML = html;
	box.classList.remove("d-none");
}

function renderChannelProbeWarnings(files) {
	var box = qs("channelProbeWarningBox");
	if (!box) {
		return;
	}
	var warnings = cziImport.collectChannelProbeWarnings(files);
	if (!warnings.length) {
		box.classList.add("d-none");
		box.innerHTML = "";
		return;
	}
	var html =
		"<strong>Channel read check:</strong> Sparse-Z counterstain (one focal plane) is normal. " +
		"Failed sample reads may still fail at import unless fallbacks succeed.<ul class=\"mb-0 mt-2\">";
	for (var i = 0; i < warnings.length; i++) {
		var w = warnings[i];
		var label = w.basename ? "<code>" + w.basename + "</code>: " : "";
		var cls = w.isError ? "text-danger" : "text-muted";
		html += '<li class="' + cls + '">' + label + w.message + "</li>";
	}
	html += "</ul>";
	box.innerHTML = html;
	box.classList.remove("d-none");
}

function mosaicTableLabel(f) {
	if (f.error) {
		return "—";
	}
	if (f.is_mosaic !== true) {
		return f.is_mosaic === false ? "No" : "—";
	}
	var tiles = f.m_tile_count != null ? f.m_tile_count : f.has_m_dim ? "2+" : "?";
	var label = "Yes";
	if (tiles !== "?" && tiles !== 1) {
		label += " (" + tiles + " tiles)";
	}
	if (f.likely_unstitched || f.mosaic_stitch_status === "suspect") {
		label += ' <span class="text-warning">unstitched?</span>';
	}
	return label;
}

function sourceFolderLabel(fileEntry) {
	var dirs = wizardState.cziSourceDirs || [];
	var scanIdx = fileEntry.scan_index != null ? fileEntry.scan_index : dirs.indexOf(fileEntry.source_dir);
	if (scanIdx >= 0) {
		return "Folder " + (scanIdx + 1);
	}
	if (fileEntry.source_dir) {
		return path.basename(fileEntry.source_dir) || fileEntry.source_dir;
	}
	return "—";
}

function renderCziFileTable(files) {
	var tbody = qs("cziFileTableBody");
	if (!tbody) {
		return;
	}
	renderMosaicInfo(files);
	renderMosaicWarnings(files);
	renderChannelProbeWarnings(files);
	tbody.innerHTML = "";
	var showFolderCol = (wizardState.cziSourceDirs || []).length > 1;
	for (var i = 0; i < files.length; i++) {
		var f = files[i];
		var tr = document.createElement("tr");
		if (f.likely_unstitched || f.mosaic_stitch_status === "suspect") {
			tr.classList.add("table-warning");
		}
		var err = f.error ? ' <span class="text-danger">' + f.error + "</span>" : "";
		var mosaicLabel = mosaicTableLabel(f);
		var folderCell = showFolderCol
			? "<td class=\"small\">" + sourceFolderLabel(f) + "</td>"
			: "";
		tr.innerHTML =
			folderCell +
			"<td>" +
			(f.basename || path.basename(f.path || "")) +
			err +
			"</td><td>" +
			(f.scene_count != null ? f.scene_count : "—") +
			"</td><td>" +
			(f.channel_count != null ? f.channel_count : "—") +
			"</td><td>" +
			(f.z_count != null ? f.z_count : "—") +
			"</td><td>" +
			mosaicLabel +
			"</td>";
		tbody.appendChild(tr);
	}
	var thead = document.querySelector("#cziFileTable thead tr");
	if (thead) {
		var folderTh = thead.querySelector("[data-col-source-folder]");
		if (showFolderCol) {
			if (!folderTh) {
				var th = document.createElement("th");
				th.setAttribute("data-col-source-folder", "1");
				th.textContent = "Source folder";
				thead.insertBefore(th, thead.firstChild);
			}
		} else if (folderTh) {
			folderTh.remove();
		}
	}
}

function roleOptionsHtml(selectedRole) {
	var html = "";
	var opts = cziImport.CHANNEL_ROLE_OPTIONS;
	for (var o = 0; o < opts.length; o++) {
		html +=
			'<option value="' +
			opts[o].value +
			'"' +
			(selectedRole === opts[o].value ? " selected" : "") +
			">" +
			opts[o].label +
			"</option>";
	}
	return html;
}

function validateStep3() {
	var channels = wizardState.cziImport.channels || [];
	var kept = channels.filter(function (ch) {
		return ch.keep;
	});
	if (!kept.length) {
		return "Keep at least one channel.";
	}
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		if (ch.role === cziImport.ROLE_OTHER && ch.keep && !cziImport.sanitizeOtherName(ch.other_name)) {
			return "Enter a valid name for all Other signal channels (letters, numbers, _ and -).";
		}
	}
	var sliceErr = cziImport.validateSliceOrder(wizardState.cziImport);
	if (sliceErr) {
		return sliceErr;
	}
	var signalKeys = cziImport.collectKeptSignalRoleKeys(wizardState.cziImport);
	if (
		signalKeys.length &&
		signalKeys.indexOf(wizardState.cziImport.primary_signal_role) < 0
	) {
		return "Choose a primary signal from kept signal channels.";
	}
	return "";
}

function updateStep3Validation() {
	var msg = validateStep3();
	var el = qs("step3Validation");
	var nextBtn = qs("step3Next");
	if (el) {
		if (msg) {
			el.textContent = msg;
			el.classList.remove("d-none");
		} else {
			el.textContent = "";
			el.classList.add("d-none");
		}
	}
	if (nextBtn) {
		nextBtn.disabled = !!msg;
	}
}

function syncKeepAllCheckbox() {
	var master = qs("keepAllChannels");
	if (!master) {
		return;
	}
	var channels = wizardState.cziImport.channels || [];
	if (!channels.length) {
		master.checked = false;
		master.indeterminate = false;
		return;
	}
	var kept = channels.filter(function (ch) {
		return ch.keep;
	}).length;
	master.checked = kept === channels.length;
	master.indeterminate = kept > 0 && kept < channels.length;
}

function renderGlobalChannelBar() {
	var indices = cziImport.collectChannelIndices(wizardState.cziImport);
	var indexSel = qs("globalChannelIndex");
	var roleSel = qs("globalChannelRole");
	var otherWrap = qs("otherNameGlobalWrap");
	var otherInput = qs("otherNameGlobal");
	var hint = qs("globalChannelHint");
	if (!indexSel || !roleSel) {
		return;
	}
	var selectedIndex =
		indexSel.value !== "" && indices.indexOf(Number(indexSel.value)) >= 0
			? Number(indexSel.value)
			: indices.length
				? indices[0]
				: 0;
	indexSel.innerHTML = "";
	for (var i = 0; i < indices.length; i++) {
		indexSel.innerHTML +=
			'<option value="' +
			indices[i] +
			'"' +
			(indices[i] === selectedIndex ? " selected" : "") +
			">Ch " +
			indices[i] +
			"</option>";
	}
	roleSel.innerHTML = roleOptionsHtml(cziImport.ROLE_SIGNAL_SOMATA);
	var defaults = (wizardState.cziImport.channel_defaults || {})[String(selectedIndex)] || {};
	roleSel.value = defaults.role || cziImport.ROLE_SIGNAL_SOMATA;
	if (otherInput) {
		otherInput.value = defaults.other_name || "";
	}
	if (otherWrap) {
		if (roleSel.value === cziImport.ROLE_OTHER) {
			otherWrap.classList.remove("d-none");
		} else {
			otherWrap.classList.add("d-none");
		}
	}
	if (hint) {
		hint.textContent = indices.length
			? "Set role for channel index " +
				selectedIndex +
				" and apply to every file row with that index."
			: "No channels probed yet.";
	}
	var applyBtn = qs("applyChannelDefaults");
	if (applyBtn) {
		applyBtn.textContent = "Apply to all Ch " + selectedIndex;
	}
}

function applyGlobalChannelDefaults() {
	var indexSel = qs("globalChannelIndex");
	var roleSel = qs("globalChannelRole");
	var otherInput = qs("otherNameGlobal");
	if (!indexSel || !roleSel) {
		return;
	}
	var channelIndex = Number(indexSel.value);
	var role = roleSel.value;
	var otherName = "";
	if (role === cziImport.ROLE_OTHER && otherInput) {
		otherName = cziImport.sanitizeOtherName(otherInput.value) || otherInput.value.trim();
		otherInput.value = otherName;
	}
	cziImport.applyChannelDefaults(wizardState.cziImport, channelIndex, {
		role: role,
		other_name: otherName,
	});
	renderChannelTable();
}

function syncAxonBitDepthUi() {
	var row = qs("axonBitDepthRow");
	var sel = qs("axonBitDepth");
	if (!row || !sel) {
		return;
	}
	var channels = wizardState.cziImport.channels || [];
	var axonKept = channels.some(function (ch) {
		return ch.keep && ch.role === cziImport.ROLE_SIGNAL_AXONS;
	});
	if (axonKept) {
		row.classList.remove("d-none");
	} else {
		row.classList.add("d-none");
	}
	if (!wizardState.cziImport.bit_depth_by_role) {
		wizardState.cziImport.bit_depth_by_role = {};
	}
	var depth = Number(wizardState.cziImport.bit_depth_by_role.signal_axons) || 8;
	sel.value = depth === 16 ? "16" : "8";
}

function bindAxonBitDepth() {
	var sel = qs("axonBitDepth");
	if (!sel) {
		return;
	}
	sel.addEventListener("change", function () {
		if (!wizardState.cziImport.bit_depth_by_role) {
			wizardState.cziImport.bit_depth_by_role = {};
		}
		wizardState.cziImport.bit_depth_by_role.signal_axons = Number(sel.value) || 8;
	});
}

function renderChannelTable() {
	var tbody = qs("channelTableBody");
	if (!tbody) {
		return;
	}
	tbody.innerHTML = "";
	var channels = wizardState.cziImport.channels || [];
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		var tr = document.createElement("tr");
		var roleCell = '<select class="form-select form-select-sm channel-role" data-idx="' + i + '">';
		roleCell += roleOptionsHtml(ch.role);
		roleCell += "</select>";
		if (ch.role === cziImport.ROLE_OTHER) {
			var invalidOther =
				ch.keep && !cziImport.sanitizeOtherName(ch.other_name);
			roleCell +=
				'<input type="text" class="form-control form-control-sm mt-1 channel-other-name' +
				(invalidOther ? " is-invalid" : "") +
				'" data-idx="' +
				i +
				'" maxlength="32" placeholder="custom_name" value="' +
				(ch.other_name || "").replace(/"/g, "&quot;") +
				'" />';
		}
		tr.innerHTML =
			"<td>" +
			(ch.file ? path.basename(ch.file) : "—") +
			"</td><td>" +
			ch.index +
			"</td><td>" +
			(ch.label || "—") +
			"</td><td>" +
			roleCell +
			'</td><td><input type="checkbox" class="form-check-input channel-keep" data-idx="' +
			i +
			'"' +
			(ch.keep ? " checked" : "") +
			" /></td>";
		tbody.appendChild(tr);
	}

	tbody.querySelectorAll(".channel-role").forEach(function (sel) {
		sel.addEventListener("change", function (ev) {
			var idx = Number(ev.target.getAttribute("data-idx"));
			wizardState.cziImport.channels[idx].role = ev.target.value;
			if (ev.target.value !== cziImport.ROLE_OTHER) {
				delete wizardState.cziImport.channels[idx].other_name;
			} else if (!wizardState.cziImport.channels[idx].other_name) {
				wizardState.cziImport.channels[idx].other_name = "";
			}
			renderChannelTable();
		});
	});
	tbody.querySelectorAll(".channel-other-name").forEach(function (inp) {
		inp.addEventListener("blur", function (ev) {
			var idx = Number(ev.target.getAttribute("data-idx"));
			var sanitized = cziImport.sanitizeOtherName(ev.target.value);
			if (sanitized) {
				ev.target.value = sanitized;
				wizardState.cziImport.channels[idx].other_name = sanitized;
			} else {
				wizardState.cziImport.channels[idx].other_name = String(ev.target.value || "").trim();
			}
			renderChannelTable();
		});
		inp.addEventListener("input", function (ev) {
			var idx = Number(ev.target.getAttribute("data-idx"));
			wizardState.cziImport.channels[idx].other_name = ev.target.value;
			updateStep3Validation();
		});
	});
	tbody.querySelectorAll(".channel-keep").forEach(function (cb) {
		cb.addEventListener("change", function (ev) {
			var idx = Number(ev.target.getAttribute("data-idx"));
			wizardState.cziImport.channels[idx].keep = ev.target.checked;
			renderChannelTable();
		});
	});

	renderGlobalChannelBar();
	renderPrimarySignalSelect();
	syncSliceNumberingRadios();
	syncKeepAllCheckbox();
	syncAxonBitDepthUi();
	updateStep3Validation();
}

function renderStep3Panel() {
	renderSectionIdentifierSelect();
	refreshSliceOrder();
	renderRenamingTable();
	renderChannelTable();
}

function defaultGeometry() {
	return orientGeometry.defaultGeometry();
}

function ensureGeometryMap() {
	wizardState.cziImport.geometry = orientGeometry.ensureGeometryMap(
		wizardState.cziImport.geometry,
		cziImport.collectSliceIds(wizardState.cziImport),
	);
}

function updateOrientStepChrome() {
	var devInfo = qs("orientStepDevInfo");
	var reextractBanner = qs("orientReextractBanner");
	var missingHistoryBanner = qs("orientMissingHistoryBanner");
	var applyAllBtn = qs("orientApplyAll");
	var showReextractOrient =
		isReextractFlow() &&
		wizardState.reextractedSliceIds &&
		wizardState.reextractedSliceIds.length > 0;
	var missingHistory = showReextractOrient && isReextractMissingGeometryHistory();
	if (devInfo) {
		devInfo.classList.toggle("d-none", isReextractFlow());
	}
	if (reextractBanner) {
		reextractBanner.classList.toggle("d-none", !showReextractOrient || missingHistory);
	}
	if (missingHistoryBanner) {
		missingHistoryBanner.classList.toggle("d-none", !missingHistory);
	}
	if (applyAllBtn) {
		applyAllBtn.disabled =
			isReextractPartialOrient() && !isReextractMissingGeometryHistory();
	}
}

function hydrateReextractOrientFromSettings() {
	if (!isReextractFlow() || !wizardState.cziImport) {
		return;
	}
	var scope = wizardState.cziImport.reextract_geometry_scope;
	if (!scope || typeof scope !== "object") {
		return;
	}
	var sliceIds = Object.keys(scope);
	if (!sliceIds.length) {
		return;
	}
	wizardState.reextractedSliceIds = sliceIds.slice();
	updateOrientStepChrome();
}

function clearReextractOrientState() {
	wizardState.reextractedSliceIds = [];
	wizardState.reextractPreviewCacheBuster = null;
	if (wizardState.cziImport) {
		delete wizardState.cziImport.reextract_geometry_scope;
	}
	updateOrientStepChrome();
}

function previewUrlCacheBuster() {
	if (wizardState.reextractPreviewCacheBuster) {
		return String(wizardState.reextractPreviewCacheBuster);
	}
	var appliedAt =
		wizardState.cziImport && wizardState.cziImport.geometry_applied_at;
	if (appliedAt) {
		return encodeURIComponent(String(appliedAt));
	}
	return "";
}

function fileUrlForPath(filePath, cacheBuster) {
	if (!filePath) {
		return "";
	}
	var href;
	try {
		href = url.pathToFileURL(path.resolve(filePath)).href;
	} catch (e) {
		href = url.pathToFileURL(filePath).href;
	}
	var bust = cacheBuster != null ? cacheBuster : previewUrlCacheBuster();
	if (bust) {
		href += (href.indexOf("?") >= 0 ? "&" : "?") + "v=" + bust;
	}
	return href;
}

function updateOrientApplySummary() {
	var el = qs("orientApplySummary");
	if (!el) {
		return;
	}
	if (wizardState.reextractedSliceIds && wizardState.reextractedSliceIds.length) {
		el.textContent = "";
		el.classList.add("d-none");
		return;
	}
	var appliedAt =
		wizardState.cziImport && wizardState.cziImport.geometry_applied_at;
	var text = orientGeometry.orientPostApplySummaryText(
		appliedAt,
		wizardState.cziImport && wizardState.cziImport.geometry_applied_files_total,
	);
	if (text) {
		el.textContent = text;
		el.classList.remove("d-none");
	} else {
		el.textContent = "";
		el.classList.add("d-none");
	}
}

function previewPathForSlice(sliceId) {
	return cziImport.resolveOrientPreviewPath(
		wizardState.bundleRoot,
		wizardState.cziImport,
		wizardState.importResult,
		sliceId,
		wizardState.orientDisplayChannel,
	);
}

function populateOrientDisplayChannelSelect() {
	var select = qs("orientDisplayChannel");
	if (!select) {
		return;
	}
	var scope = reextractGeometryScope();
	var channels;
	if (isReextractPartialOrient() && scope) {
		channels = cziImport.listOrientDisplayChannelsForReextract(
			wizardState.bundleRoot,
			wizardState.cziImport,
			scope,
		);
	} else {
		channels = cziImport.listOrientDisplayChannels(
			wizardState.bundleRoot,
			wizardState.cziImport,
		).map(function (ch) {
			return { key: ch.key, label: ch.label, enabled: true };
		});
	}
	select.innerHTML = "";
	for (var i = 0; i < channels.length; i++) {
		var opt = document.createElement("option");
		opt.value = channels[i].key;
		opt.textContent = channels[i].label;
		if (channels[i].enabled === false) {
			opt.disabled = true;
		}
		select.appendChild(opt);
	}
	var hasEnabledCurrent = channels.some(function (ch) {
		return ch.key === wizardState.orientDisplayChannel && ch.enabled !== false;
	});
	if (!hasEnabledCurrent) {
		var firstEnabled = null;
		for (var j = 0; j < channels.length; j++) {
			if (channels[j].enabled !== false) {
				firstEnabled = channels[j].key;
				break;
			}
		}
		if (firstEnabled) {
			wizardState.orientDisplayChannel = firstEnabled;
		} else if (isReextractPartialOrient()) {
			wizardState.orientDisplayChannel = cziImport.ORIENT_DISPLAY_DAPI;
		} else {
			wizardState.orientDisplayChannel = cziImport.ORIENT_DISPLAY_DAPI;
		}
	}
	select.value = wizardState.orientDisplayChannel;
}

function updateOrientPreviewBanner() {
	var health = cziImport.assessOrientPreviewHealth(
		wizardState.bundleRoot,
		wizardState.cziImport,
	);
	var banner = qs("orientPreviewBanner");
	var geomBanner = qs("orientGeometryBanner");
	var repairBtn = qs("orientRepairPreviews");
	var step5Next = qs("step5Next");
	var pending = countNonIdentityGeometry();
	var sliceIds = orientGridSliceIds();
	var geoState = geometryState.assessGeometryApplyState(
		wizardState.bundleRoot,
		wizardState.cziImport,
		{ sliceIds: sliceIds, previewHealth: health },
	);
	var pendingForApply = geoState.partialReextractApply
		? geoState.pendingInScope
		: pending;
	var msg = cziImport.orientPreviewBannerText(health);
	if (banner) {
		if (msg) {
			banner.textContent = msg;
			banner.classList.remove("d-none");
		} else {
			banner.textContent = "";
			banner.classList.add("d-none");
		}
	}
	var geoMsg = geometryState.geometryStateBannerText(geoState, health);
	if (geomBanner) {
		if (geoMsg && (geoState.policyState === "interrupted" || geoState.policyState === "reextract_partial")) {
			if (geoState.policyState === "interrupted") {
				geomBanner.innerHTML =
					geoMsg +
					' <a href="./geometry_repair_wizard.html">Check Orientation Consistency</a>';
			} else {
				geomBanner.textContent = geoMsg;
			}
			geomBanner.classList.remove("d-none");
		} else {
			geomBanner.textContent = "";
			geomBanner.classList.add("d-none");
		}
	}
	if (repairBtn) {
		repairBtn.classList.toggle("d-none", !health.needsRepair);
	}
	if (step5Next && !geometryRunning) {
		step5Next.disabled =
			!health.canApply || !geoState.allowApply || pendingForApply === 0;
	}
	var step5Hint = qs("step5ApplyHint");
	if (step5Hint && !geometryRunning) {
		if (!health.canApply) {
			step5Hint.textContent = "";
		} else if (geoState.policyState === "interrupted") {
			step5Hint.innerHTML =
				'Geometry apply is blocked. Open <a href="./geometry_repair_wizard.html">Check Orientation Consistency</a> to audit and repair.';
		} else if (isReextractMissingGeometryHistory()) {
			if (pendingForApply === 0) {
				step5Hint.textContent =
					"Use the rotation buttons below (or Copy first tile geometry to all), then Confirm geometry.";
			} else {
				step5Hint.textContent =
					"CSS preview — not yet written to files. Confirm geometry writes transforms to disk.";
			}
		} else if (geoState.policyState === "reextract_partial") {
			step5Hint.textContent =
				"Preview shows how each re-imported channel will look after Confirm geometry. Skipped channels are greyed out in the menu.";
		} else if (pendingForApply === 0) {
			step5Hint.textContent = wizardState.cziImport.geometry_applied_at
				? "No pending changes. Review on-disk previews or adjust a slice before confirming again."
				: "No pending geometry changes.";
		} else {
			step5Hint.textContent =
				"CSS preview — not yet written to files. Confirm geometry writes transforms to disk.";
		}
	}
	updateOrientApplySummary();
	return health;
}

async function runOrientPreviewRepair() {
	var health = updateOrientPreviewBanner();
	cziImport.ensureOrientDapiPreviewsFromPipeline(wizardState.bundleRoot);
	wizardState.repairMode = "previews";
	wizardState.repairTargets = cziImport.buildRepairTargetsFromAudit(
		health.audit,
		wizardState.cziImport,
	);
	try {
		await runExtract({ repairOnly: true });
		// runExtract shows step 4 while the job runs; return to Orient afterward.
		setStep(5);
	} finally {
		wizardState.repairMode = false;
		wizardState.repairTargets = [];
	}
}

function renderOrientationGrid() {
	ensureGeometryMap();
	var grid = qs("orientGrid");
	if (!grid) {
		return;
	}
	grid.innerHTML = "";
	var ids = orientGridSliceIds();
	for (var i = 0; i < ids.length; i++) {
		var sliceId = ids[i];
		var geom = wizardState.cziImport.geometry[sliceId];
		var tile = document.createElement("div");
		tile.className = "czi-orient-tile";
		tile.setAttribute("data-slice-id", sliceId);
		var imgPath = previewPathForSlice(sliceId);
		var imgSrc = fileUrlForPath(imgPath);
		var titleEl = document.createElement("strong");
		titleEl.textContent = sliceId;
		tile.appendChild(titleEl);
		if (imgSrc) {
			var frame = document.createElement("div");
			frame.className = "czi-orient-preview-frame";
			var viewport = document.createElement("div");
			viewport.className = "czi-orient-tile-viewport";
			if (shouldApplyCssPreviewForTile(sliceId, geom)) {
				viewport.style.transform = geometryCssTransform(geom);
				viewport.style.transformOrigin = "center center";
			}
			var img = document.createElement("img");
			img.src = imgSrc;
			img.alt = sliceId;
			img.title = imgPath;
			img.onerror = function () {
				var msg = document.createElement("p");
				msg.className = "small text-muted";
				msg.textContent = "No preview";
				msg.title = imgPath;
				if (viewport.parentNode) {
					viewport.parentNode.replaceChild(msg, viewport);
				}
			};
			viewport.appendChild(img);
			frame.appendChild(viewport);
			tile.appendChild(frame);
			var hint = document.createElement("p");
			hint.className = "czi-orient-preview-hint";
			hint.textContent = orientGeometry.reimportTileHintText(
				sliceId,
				wizardState.reextractedSliceIds,
				geom,
				wizardState.cziImport.geometry_applied_at,
			);
			tile.appendChild(hint);
		} else {
			var noPrev = document.createElement("p");
			noPrev.className = "small text-muted";
			noPrev.textContent = "No preview";
			tile.appendChild(noPrev);
		}
		var btnGroup = document.createElement("div");
		btnGroup.className = "btn-group btn-group-sm mt-1 w-100";
		btnGroup.setAttribute("role", "group");
		btnGroup.innerHTML =
			'<button type="button" class="btn btn-outline-secondary" data-geo="rot90" data-slice="' +
			sliceId +
			'">↻90°</button>' +
			'<button type="button" class="btn btn-outline-secondary" data-geo="flipX" data-slice="' +
			sliceId +
			'">↔</button>' +
			'<button type="button" class="btn btn-outline-secondary" data-geo="flipY" data-slice="' +
			sliceId +
			'">↕</button>';
		tile.appendChild(btnGroup);
		var status = document.createElement("p");
		status.className = "small text-muted mb-0 mt-1";
		status.setAttribute("data-geo-status", "1");
		status.textContent = orientGeometry.geometryStatusText(geom);
		tile.appendChild(status);
		grid.appendChild(tile);
	}

	orientGeometry.wireOrientationGridClicks(
		grid,
		function () {
			return wizardState.cziImport.geometry;
		},
		function (sliceId) {
			var ids = orientGridSliceIds();
			var copyAll = qs("orientApplyAll");
			if (copyAll && copyAll.checked && ids.length && sliceId === ids[0]) {
				orientGeometry.copyFirstTileGeometryToAll(
					wizardState.cziImport.geometry,
					ids,
					grid,
					isReextractPartialOrient()
						? function (targetSliceId, geom) {
								return shouldApplyCssPreviewForTile(targetSliceId, geom);
							}
						: null,
				);
			}
			updateOrientStepChrome();
			updateOrientPreviewBanner();
			persistCziSettings();
		},
		isReextractPartialOrient()
			? function (sliceId, geom) {
					return shouldApplyCssPreviewForTile(sliceId, geom);
				}
			: null,
	);
}

function finalizeGeometryAfterApply(payload) {
	var ids = cziImport.collectSliceIds(wizardState.cziImport);
	var orphans = cziImport.findGeometryKeysWithoutPreviewFiles(
		wizardState.bundleRoot,
		wizardState.cziImport.geometry,
		ids,
	);
	if (orphans.length) {
		verboseFinishLog(
			"WARNING: geometry for slice(s) without DAPI/_previews files: " + orphans.join(", "),
		);
	}
	geometryState.finalizeGeometryAfterApply(wizardState.bundleRoot, wizardState.cziImport, {
		sliceIds: ids,
		payload: payload,
		applySource: "czi_wizard",
		omitConfigKeys: wizardState.repairMode ? null : ["repair_mode", "repair_targets"],
	});
	persistCziSettings();
	if (isReextractFlow()) {
		clearReextractOrientState();
	}
	updateOrientApplySummary();
	renderOrientationGrid();
}

function writeImportConfig() {
	var axonSel = qs("axonBitDepth");
	if (axonSel && !isReextractFlow()) {
		if (!wizardState.cziImport.bit_depth_by_role) {
			wizardState.cziImport.bit_depth_by_role = {};
		}
		wizardState.cziImport.bit_depth_by_role.signal_axons = Number(axonSel.value) || 8;
	}
	var extra = {};
	var isRepair = wizardState.repairMode === "previews" || wizardState.repairMode === "reextract";
	if (wizardState.repairMode === "previews") {
		extra.repair_mode = "previews";
		extra.repair_targets = wizardState.repairTargets || [];
	} else if (wizardState.repairMode === "reextract") {
		var proj = project.getProject();
		var reextractPayload = cziImport.buildReextractConfig(
			wizardState.cziImport,
			wizardState.repairTargets || [],
			proj,
		);
		extra.repair_mode = "reextract";
		extra.repair_targets = reextractPayload.repair_targets;
		if (reextractPayload.max_runs) {
			extra.max_runs = reextractPayload.max_runs;
		}
	}
	if (
		wizardState.cziImport.reextract_geometry_scope &&
		!isRepair
	) {
		extra.reextract_geometry_scope = wizardState.cziImport.reextract_geometry_scope;
		extra.apply_source = "czi_reextract";
	}
	var cfgPath = geometryState.writeCziImportConfig(
		wizardState.bundleRoot,
		wizardState.cziImport,
		extra,
		isRepair ? {} : { omitKeys: ["repair_mode", "repair_targets"] },
	);
	return cfgPath;
}

function persistCziSettings() {
	var proj = project.getProject();
	if (!proj) {
		return;
	}
	if (!proj.settings) {
		proj.settings = {};
	}
	proj.settings.czi_import = wizardState.cziImport;
	if (wizardState.importResult && wizardState.importResult.max_runs) {
		proj.settings.czi_import.max_runs = wizardState.importResult.max_runs;
	}
	proj.settings.czi_import.config_fingerprint = cziImport.cziImportFingerprint(
		wizardState.cziImport,
	);
	proj.settings.czi_import.preview_format_version =
		(wizardState.importResult && wizardState.importResult.preview_format_version) ||
		cziImport.PREVIEW_FORMAT_VERSION;
	proj.sources = proj.sources || {};
	proj.sources.original_scans =
		(wizardState.cziSourceDirs && wizardState.cziSourceDirs[0]) ||
		wizardState.cziImport.source_dir ||
		"";
	project.saveProjectJson();
}

function setActiveMaxRuns() {
	var result = wizardState.importResult || {};
	var maxRuns = result.max_runs || {};
	var primaryRel = cziImport.primaryMaxRunRel(wizardState.cziImport, result);
	if (primaryRel) {
		pipelineRuns.setActiveRunRelForRole("max", primaryRel);
	}
	var keys = Object.keys(maxRuns);
	for (var i = 0; i < keys.length; i++) {
		if (maxRuns[keys[i]] && keys[i] !== wizardState.cziImport.primary_signal_role) {
			/* only one active max leaf — primary wins */
		}
	}
}

/** After extract/repair: set active max run and rebuild file index (DAPI + max). */
async function syncProjectIndexAfterExtract() {
	if (!wizardState.bundleRoot) {
		return { matchedCount: 0, expected: 0, dapiOrphans: 0, maxOrphans: 0 };
	}
	setActiveMaxRuns();
	await project.refreshProjectIndex(wizardState.bundleRoot);
	var index = project.readProjectFileIndex(wizardState.bundleRoot);
	var report = project.computeMatchReport(index, ["dapi", "max"]);
	var matchedCount = (report.matchedSliceIds || []).length;
	var expected = cziImport.collectSliceIds(wizardState.cziImport || {}).length;
	var dapiOrphans = (report.orphansByRole && report.orphansByRole.dapi) || [];
	var maxOrphans = (report.orphansByRole && report.orphansByRole.max) || [];
	verboseExtractLog(
		"File index updated: " +
			matchedCount +
			" / " +
			expected +
			" slice(s) matched between DAPI and max.",
	);
	if (expected > 0 && matchedCount < expected) {
		verboseExtractLog(
			"WARNING: " +
				(expected - matchedCount) +
				" slice(s) missing DAPI+max pairing — multi-folder extract may be incomplete. Re-run extract or use Re-import sections from CZI.",
		);
		if (dapiOrphans.length) {
			verboseExtractLog(
				"  DAPI-only (no max): " +
					dapiOrphans.slice(0, 8).join(", ") +
					(dapiOrphans.length > 8 ? " …" : ""),
			);
		}
		if (maxOrphans.length) {
			verboseExtractLog(
				"  Max-only (no DAPI): " +
					maxOrphans.slice(0, 8).join(", ") +
					(maxOrphans.length > 8 ? " …" : ""),
			);
		}
	}
	return {
		matchedCount: matchedCount,
		expected: expected,
		dapiOrphans: dapiOrphans.length,
		maxOrphans: maxOrphans.length,
		orphansByRole: report.orphansByRole,
	};
}

function updateExtractIndexNote(info) {
	var detail = qs("extractDetail");
	if (!detail) {
		return;
	}
	var matchedCount = typeof info === "number" ? info : info.matchedCount || 0;
	var expected =
		typeof info === "object" && info.expected != null ? info.expected : matchedCount;
	var note =
		"File index updated — " +
		matchedCount +
		" / " +
		expected +
		" slice(s) matched between DAPI and max. " +
		"Refreshes again after you confirm geometry (finish step). " +
		"Detailed lines also appear in the application log window.";
	if (expected > 0 && matchedCount < expected) {
		note +=
			" Warning: " +
			(expected - matchedCount) +
			" slice(s) lack both DAPI and max — re-run extract or Re-import sections from CZI.";
	}
	detail.textContent = note;
}

function isWorkerExitedProbeError(err) {
	var msg = String((err && err.message) || err || "").toLowerCase();
	return msg.indexOf("worker exited") !== -1;
}

function probeSingleDirAttempt(dir, scanIndex, folderProgress) {
	var status = qs("probeStatus");
	return new Promise(function (resolve, reject) {
		var settled = false;
		var timeoutId = setTimeout(function () {
			if (settled) {
				return;
			}
			settled = true;
			cleanup();
			reject(
				new Error(
					"Probe timed out after " +
						Math.round(PROBE_TIMEOUT_MS / 60000) +
						" minutes for " +
						dir,
				),
			);
		}, PROBE_TIMEOUT_MS);

		function cleanup() {
			clearTimeout(timeoutId);
			ipc.removeListener("updateLoad", onLoadProgress);
			ipc.removeListener("cziJobLog", onJobLog);
			ipc.removeListener("cziProbeResult", onResult);
		}

		function onLoadProgress(ev, data) {
			if (!status) {
				return;
			}
			var message = data && data[1] ? String(data[1]) : "";
			if (folderProgress && folderProgress.total) {
				status.textContent =
					"Folder " +
					folderProgress.index +
					"/" +
					folderProgress.total +
					": " +
					message;
			} else if (message) {
				status.textContent = message;
			}
			if (message) {
				verboseProbeLog(message);
			}
		}

		function onJobLog(ev, line) {
			if (line) {
				verboseProbeLog(String(line));
			}
		}

		function onResult(ev, payload) {
			if (settled) {
				return;
			}
			settled = true;
			cleanup();
			if (!payload || payload.ok === false) {
				reject(new Error((payload && payload.error) || "Probe failed for " + dir));
				return;
			}
			cziImport.mergeProbeDirIntoImport(wizardState.cziImport, payload, dir, scanIndex);
			resyncScanIndices();
			resolve(payload);
		}

		ipc.on("updateLoad", onLoadProgress);
		ipc.on("cziJobLog", onJobLog);
		ipc.once("cziProbeResult", onResult);
		verboseProbeLog("Starting probe: " + dir);
		ipc.send("runCziProbe", [dir]);
	});
}

async function probeSingleDir(dir, scanIndex, folderProgress) {
	dir = cziImport.canonicalSourceDir(dir);
	var lastErr = null;
	for (var attempt = 1; attempt <= PROBE_MAX_ATTEMPTS; attempt++) {
		try {
			return await probeSingleDirAttempt(dir, scanIndex, folderProgress);
		} catch (err) {
			lastErr = err;
			if (!isWorkerExitedProbeError(err) || attempt >= PROBE_MAX_ATTEMPTS) {
				throw err;
			}
			var nextAttempt = attempt + 1;
			verboseProbeLog(
				"Probe worker exited; retrying (" +
					nextAttempt +
					"/" +
					PROBE_MAX_ATTEMPTS +
					")…",
			);
			var status = qs("probeStatus");
			if (status) {
				status.textContent =
					"Probe worker exited; retrying (" +
					nextAttempt +
					"/" +
					PROBE_MAX_ATTEMPTS +
					")…";
			}
		}
	}
	throw lastErr;
}

function warnZeroFilesAfterProbe(dir, folderNum, status) {
	var fileCount = countFilesForSourceDir(dir);
	if (fileCount !== 0) {
		if (status) {
			status.classList.remove("text-warning");
		}
		return fileCount;
	}
	var warnMsg =
		"Probe finished but 0 files matched folder " +
		folderNum +
		" — path mismatch? (" +
		dir +
		")";
	if (status) {
		status.textContent = warnMsg;
		status.classList.add("text-warning");
	}
	verboseProbeLog("WARNING: " + warnMsg);
	return 0;
}

async function probeIncrementalNewDir(dir) {
	if (probeInFlight) {
		return;
	}
	dir = cziImport.canonicalSourceDir(dir);
	probeInFlight = true;
	setProbeControlsBusy(true);
	var scanIndex = wizardState.cziSourceDirs.indexOf(dir);
	if (scanIndex < 0) {
		scanIndex = wizardState.cziSourceDirs.length - 1;
	}
	var status = qs("probeStatus");
	var nextBtn = qs("step2Next");
	if (nextBtn) {
		nextBtn.disabled = true;
	}
	var folderNum = scanIndex + 1;
	var totalFolders = wizardState.cziSourceDirs.length;
	if (status) {
		status.textContent =
			"Probing folder " + folderNum + "/" + totalFolders + ": " + dir;
		status.classList.remove("text-warning");
	}
	try {
		await probeSingleDir(dir, scanIndex, { index: folderNum, total: totalFolders });
		var fileCount = warnZeroFilesAfterProbe(dir, folderNum, status);
		refreshAfterProbeIncremental(scanIndex === 0);
		if (status && fileCount > 0) {
			status.textContent =
				"Folder " +
				folderNum +
				" done — " +
				fileCount +
				" file(s)" +
				(folderNum < totalFolders
					? "; add or probe remaining folders."
					: "; " +
						(wizardState.cziImport.files || []).filter(function (f) {
							return !f.error;
						}).length +
						" file(s) total.");
		}
	} catch (err) {
		alert(
			"Probe failed for folder " +
				folderNum +
				":\n" +
				dir +
				"\n\n" +
				String(err.message || err),
		);
		throw err;
	} finally {
		probeInFlight = false;
		setProbeControlsBusy(false);
		updateProbeStatusSummary();
	}
}

async function runProbeAll() {
	if (probeInFlight) {
		alert("A probe is already running. Wait for it to finish or cancel from the application log.");
		return;
	}
	probeInFlight = true;
	setProbeControlsBusy(true);
	var dirs = (wizardState.cziSourceDirs || []).map(function (d) {
		return cziImport.canonicalSourceDir(d);
	});
	wizardState.cziSourceDirs = dirs;
	var status = qs("probeStatus");
	var nextBtn = qs("step2Next");
	if (nextBtn) {
		nextBtn.disabled = true;
	}
	try {
		if (!dirs.length) {
			if (status) {
				status.textContent = "";
			}
			wizardState.cziImport.section_identifier = null;
			wizardState.sectionIdentifierCandidates = [];
			renderSectionIdentifierSelect();
			renderCziFileTable([]);
			return;
		}
		wizardState.cziImport.source_dirs = dirs.slice();
		wizardState.cziImport.files = (wizardState.cziImport.files || []).filter(function (f) {
			var fd = cziImport.canonicalSourceDir(f.source_dir);
			return dirs.indexOf(fd) >= 0;
		});
		if (status) {
			status.textContent = "Probing " + dirs.length + " folder(s)…";
			status.classList.remove("text-warning");
		}
		for (var i = 0; i < dirs.length; i++) {
			if (status) {
				status.textContent = "Probing folder " + (i + 1) + "/" + dirs.length + ": " + dirs[i];
			}
			await probeSingleDir(dirs[i], i, { index: i + 1, total: dirs.length });
			warnZeroFilesAfterProbe(dirs[i], i + 1, status);
			resyncScanIndices();
			refreshAfterProbeIncremental(false);
			if (status && i < dirs.length - 1) {
				var doneCount = countFilesForSourceDir(dirs[i]);
				status.textContent =
					"Folder " +
					(i + 1) +
					" done — " +
					doneCount +
					" file(s); probing folder " +
					(i + 2) +
					"/" +
					dirs.length +
					"…";
			}
		}
		refreshSectionIdentifierAfterProbe(true);
		refreshSliceOrder();
		renderSourceDirList();
		renderCziFileTable(sortedCziFilesForDisplay());
		if (status) {
			status.textContent =
				(wizardState.cziImport.files || []).filter(function (f) {
					return !f.error;
				}).length +
				" file(s) from " +
				dirs.length +
				" folder(s) probed.";
		}
		if (nextBtn) {
			nextBtn.disabled = !(wizardState.cziImport.files && wizardState.cziImport.files.length);
		}
	} catch (err) {
		alert("Re-probe failed:\n" + String(err.message || err));
		throw err;
	} finally {
		probeInFlight = false;
		setProbeControlsBusy(false);
	}
}

function hydrateWizardFromSavedCziImport(saved) {
	wizardState.cziImport = JSON.parse(JSON.stringify(saved));
	wizardState.cziSourceDirs = (saved.source_dirs || []).slice();
	if (!wizardState.cziSourceDirs.length && saved.source_dir) {
		wizardState.cziSourceDirs = [saved.source_dir];
	}
	wizardState.cziSourceDirs = wizardState.cziSourceDirs.map(function (d) {
		return cziImport.canonicalSourceDir(d);
	});
	resyncScanIndices();
	wizardState.importResult = {
		max_runs: saved.max_runs || {},
		primary_signal_role: saved.primary_signal_role,
		preview_format_version: saved.preview_format_version,
	};
	if (saved.files && saved.files.length) {
		wizardState.sectionIdentifierCandidates = cziImport.detectSectionIdentifierCandidates(
			saved.files,
		);
	}
}

async function tryResumeCziImportAfterStep1() {
	if (!wizardState.bundleRoot || !fs.existsSync(wizardState.bundleRoot)) {
		return false;
	}
	project.openProject(wizardState.bundleRoot);
	var proj = project.getProject();
	var saved = proj && proj.settings && proj.settings.czi_import;
	if (!saved || !saved.files || !saved.files.length) {
		return false;
	}
	hydrateWizardFromSavedCziImport(saved);
	var expectedFp = cziImport.cziImportFingerprint(saved);
	if (saved.config_fingerprint && saved.config_fingerprint !== expectedFp) {
		verboseExtractLog("Saved CZI import fingerprint mismatch — full wizard");
		return false;
	}
	var audit = cziImport.auditCziImportCompletion(wizardState.bundleRoot, saved, {
		importResult: wizardState.importResult,
	});
	if (!audit.extractComplete) {
		return false;
	}
	if (audit.canSkipToOrient) {
		verboseExtractLog("Resuming — extract already complete.");
		await syncProjectIndexAfterExtract();
		// If geometry was already applied (oriented) and nothing is pending,
		// land on the "what's next" step — otherwise step 5's Apply button is
		// disabled (pending === 0) and there is no way forward. The step 5 pill
		// stays available to review or re-orient.
		var alreadyOriented =
			!!(wizardState.cziImport && wizardState.cziImport.geometry_applied_at) &&
			countNonIdentityGeometry() === 0;
		setStep(alreadyOriented ? 6 : 5);
		return true;
	}
	if (audit.needsPreviewRepair) {
		wizardState.repairMode = "previews";
		wizardState.repairTargets = cziImport.buildRepairTargetsFromAudit(audit, saved);
		verboseExtractLog(
			"Repairing " + wizardState.repairTargets.length + " preview(s) from existing z-stacks…",
		);
		try {
			await runExtract({ repairOnly: true });
			await syncProjectIndexAfterExtract();
			setStep(5);
			return true;
		} catch (err) {
			wizardState.repairMode = false;
			wizardState.repairTargets = [];
			throw err;
		}
	}
	return false;
}

async function ensureBundleCreated() {
	if (fs.existsSync(wizardState.bundleRoot)) {
		project.openProject(wizardState.bundleRoot);
		return;
	}
	fs.mkdirSync(wizardState.bundleRoot, { recursive: true });
	var name = (qs("projectName") && qs("projectName").value) || path.basename(wizardState.bundleRoot);
	project.createProject({
		bundleRoot: wizardState.bundleRoot,
		name: name,
		projectFilename: wizardState.projectFilename,
		settings: { czi_import: wizardState.cziImport },
	});
	project.openProject(wizardState.bundleRoot);
}

/**
 * Prevent accidental full re-extract that overwrites an already-complete import.
 * Returns a skip payload, delegates to preview repair, or null to proceed.
 */
function guardAgainstAccidentalFullExtract(options) {
	options = options || {};
	if (options.repairOnly || options.forceFullExtract || options.mode === "reextract") {
		return null;
	}
	if (!wizardState.bundleRoot || !fs.existsSync(wizardState.bundleRoot)) {
		return null;
	}
	var audit = cziImport.auditCziImportCompletion(wizardState.bundleRoot, wizardState.cziImport, {
		importResult: wizardState.importResult,
	});
	if (audit.canSkipToOrient) {
		verboseExtractLog(
			"Extract already complete on disk — skipping full re-import and opening Orient.",
		);
		setStep(5);
		return { ok: true, skipped: true, reason: "already_complete" };
	}
	if (audit.extractComplete && audit.needsPreviewRepair) {
		verboseExtractLog(
			"Extract already complete; running preview repair instead of full re-import.",
		);
		wizardState.repairMode = "previews";
		wizardState.repairTargets = cziImport.buildRepairTargetsFromAudit(
			audit,
			wizardState.cziImport,
		);
		return { redirectRepair: true };
	}
	if (audit.extractComplete) {
		var msg =
			"This project already has a completed CZI extract.\n\n" +
			"Running Extract again will overwrite z-stacks, previews, and max projections.\n\n" +
			"Click OK only if you intend to re-import everything. Click Cancel to keep existing files.";
		if (!confirm(msg)) {
			verboseExtractLog("Full re-import cancelled — existing extract left unchanged.");
			throw new Error("Extract cancelled — existing import left unchanged.");
		}
		verboseExtractLog("User confirmed full re-import over existing extract outputs.");
	}
	return null;
}

async function runExtract(options) {
	options = options || {};
	var guard = guardAgainstAccidentalFullExtract(options);
	if (guard && guard.skipped) {
		return guard;
	}
	if (guard && guard.redirectRepair) {
		return runExtract({ repairOnly: true });
	}
	extractRunning = true;
	extractCompleted = false;
	if (!options.repairOnly) {
		wizardState.repairMode = false;
		wizardState.repairTargets = [];
	} else if (options.mode === "reextract") {
		wizardState.repairMode = "reextract";
	} else {
		wizardState.repairMode = "previews";
	}
	setStep(4);
	var cancelBtn = qs("cancelExtract");
	var logEl = qs("extractLog");
	if (logEl) {
		logEl.textContent = "";
	}
	if (cancelBtn) {
		cancelBtn.classList.remove("d-none");
	}
	setStep4ContinueVisible(false);
	setExtractNavDisabled(true);
	updateWizardCancelVisibility();
	var startLabel = "Starting CZI extraction…";
	if (wizardState.repairMode === "previews") {
		startLabel = "Starting preview repair…";
	} else if (wizardState.repairMode === "reextract") {
		startLabel = "Starting CZI re-import…";
	}
	setExtractActivity("Preparing bundle and config…", 2);
	verboseExtractLog(startLabel);

	await ensureBundleCreated();
	writeImportConfig();
	persistCziSettings();

	var cfg = wizardState.cziImport;
	var workEstimate = cziImport.countExtractWorkItems(cfg);
	var kept = countKeptChannels(cfg);
	verboseExtractLog("Bundle: " + wizardState.bundleRoot);
	verboseExtractLog(
		"CZI source: " +
			((wizardState.cziSourceDirs || []).join("; ") ||
				cfg.source_dirs && cfg.source_dirs.join("; ") ||
				cfg.source_dir ||
				"(none)"),
	);
	verboseExtractLog(
		kept +
			" channel(s) marked to keep; estimated " +
			(workEstimate || "?") +
			" extraction item(s)",
	);
	setExtractActivity("Writing import config…", 4);

	return new Promise(function (resolve, reject) {
		function onJobLog(ev, msg) {
			markPythonActivity();
			verboseExtractLog(msg);
			var status = qs("extractStatus");
			if (status && msg) {
				status.textContent = truncateStatus(msg, 120);
			}
		}
		function onProgress(ev, data) {
			markPythonActivity();
			var rawPct = data[0];
			var message = data[1];
			if (/^Ready —/i.test(String(message || ""))) {
				markPythonActivity();
			}
			var displayPct = mapExtractDisplayPct(rawPct, message);
			setExtractActivity(formatExtractStatus(rawPct, message), displayPct);
		}
		function finishExtractNav(showContinue) {
			extractRunning = false;
			clearExtractWaitTimers();
			setExtractNavDisabled(false);
			updateWizardCancelVisibility();
			if (cancelBtn) {
				cancelBtn.classList.add("d-none");
			}
			if (showContinue) {
				extractCompleted = true;
				if (wizardState.step === 4) {
					setStep4ContinueVisible(true);
				}
			}
		}
		function onResult(ev, payload) {
			ipc.removeListener("updateLoad", onProgress);
			ipc.removeListener("cziJobLog", onJobLog);
			ipc.removeListener("cziImportResult", onResult);
			if (!payload || payload.ok === false) {
				finishExtractNav(false);
				var errMsg = (payload && payload.error) || "Import failed";
				setExtractActivity(
					/^cancelled$/i.test(String(errMsg))
						? "Extraction cancelled."
						: "Extract failed: " + errMsg,
					0,
				);
				verboseExtractLog("ERROR: " + errMsg);
				reject(new Error(errMsg));
				return;
			}
			finishExtractNav(true);
			wizardState.importResult = payload;
			if (payload.max_runs) {
				wizardState.cziImport.max_runs = payload.max_runs;
			}
			wizardState.cziImport.preview_format_version =
				payload.preview_format_version || cziImport.PREVIEW_FORMAT_VERSION;
			var wasReextract = wizardState.flow === "reextract";
			var reextractTargets = wasReextract
				? (wizardState.repairTargets || []).slice()
				: [];
			if (wasReextract && reextractTargets.length) {
				var sliceIdSet = {};
				for (var rt = 0; rt < reextractTargets.length; rt++) {
					var sid = reextractTargets[rt].slice_id;
					if (sid) {
						sliceIdSet[sid] = true;
					}
				}
				wizardState.reextractedSliceIds = Object.keys(sliceIdSet);
				wizardState.cziImport.reextract_geometry_scope =
					cziImport.buildReextractGeometryScope(reextractTargets);
				var reconcileResult = geometryHistory.reconcileGeometryAfterReextract(
					wizardState.bundleRoot,
					wizardState.cziImport,
					wizardState.reextractedSliceIds,
				);
				wizardState.reextractPreviewCacheBuster = Date.now();
				if (reconcileResult.restored.length) {
					verboseExtractLog(
						"Restored pending geometry for " +
							reconcileResult.restored.length +
							" re-imported section(s) from history.",
					);
				}
				if (reconcileResult.missing.length) {
					verboseExtractLog(
						"No geometry history for re-imported section(s): " +
							reconcileResult.missing.join(", "),
					);
					if (reconcileResult.restored.length === 0) {
						verboseExtractLog(
							"Orient step: set rotation manually, then Confirm geometry.",
						);
					}
				}
			}
			wizardState.repairMode = false;
			wizardState.repairTargets = [];
			persistCziSettings();
			var primary = payload.primary_signal_role || wizardState.cziImport.primary_signal_role;
			var summary =
				"Extract complete — primary signal: " +
				(primary || "(none)") +
				"; max runs: " +
				Object.keys(payload.max_runs || {}).length;
			setExtractActivity(summary, 100);
			verboseExtractLog(summary);
			if (payload.max_runs) {
				var keys = Object.keys(payload.max_runs);
				for (var k = 0; k < keys.length; k++) {
					verboseExtractLog("  " + keys[k] + " → " + payload.max_runs[keys[k]]);
				}
			}
			syncProjectIndexAfterExtract()
				.then(function (info) {
					updateExtractIndexNote(info);
					resolve(payload);
				})
				.catch(function (indexErr) {
					verboseExtractLog(
						"File index update failed (continuing): " + String(indexErr.message || indexErr),
					);
					resolve(payload);
				});
		}
		ipc.on("updateLoad", onProgress);
		ipc.on("cziJobLog", onJobLog);
		ipc.once("cziImportResult", onResult);
		var cfgPath = writeImportConfig();
		verboseExtractLog("Config: " + cfgPath);
		setExtractActivity("Starting Python extract…", 5);
		startExtractWaitFeedback();
		ipc.send("runCziImport", [
			String(wizardState.bundleRoot || "").trim(),
			String(cfgPath || "").trim(),
		]);
	});
}

async function runApplyGeometry() {
	var pending = countNonIdentityGeometry();
	if (pending === 0) {
		throw new Error("No pending geometry changes to apply.");
	}
	var sliceIds = cziImport.collectSliceIds(wizardState.cziImport);
	var geoState = geometryState.assessGeometryApplyState(
		wizardState.bundleRoot,
		wizardState.cziImport,
		{ sliceIds: sliceIds },
	);
	if (geoState.policyState === "interrupted" && !geoState.partialReextractApply) {
		throw new Error(
			"Geometry apply is blocked — open Check Orientation Consistency to audit and repair inconsistent files.",
		);
	}
	if (
		pending > 0 &&
		wizardState.cziImport.geometry_applied_at &&
		geoState.policyState === "healthy"
	) {
		if (
			!confirm(
				"Geometry was already applied to files. Apply again will rotate/flip current on-disk images. Continue?",
			)
		) {
			throw new Error("Apply cancelled.");
		}
	}
	setStep(6);
	geometryRunning = true;
	updateWizardCancelVisibility();
	setFinishNavDisabled(true);
	var logEl = qs("finishLog");
	if (logEl) {
		logEl.textContent = "";
	}
	setGeometryActivity("Applying geometry to z-stacks, max slices, and previews…", 2);
	verboseFinishLog("Bundle: " + wizardState.bundleRoot);
	verboseFinishLog(
		"Slices with rotation/flip: " + countNonIdentityGeometry() + " of " + cziImport.collectSliceIds(wizardState.cziImport).length,
	);
	writeImportConfig();
	persistCziSettings();

	return new Promise(function (resolve, reject) {
		var fileTotal = 0;
		var fileDone = 0;
		var longJobWarned = false;
		var LONG_JOB_FILE_THRESHOLD = 40;

		function onProgress(ev, data) {
			var rawPct = Number(data[0]) || 0;
			var message = String(data[1] || "");
			var match = message.match(/\[(\d+)\/(\d+)\]/);
			if (match) {
				fileDone = Number(match[1]);
				fileTotal = Number(match[2]);
				rawPct = Math.min(99, Math.round((fileDone / fileTotal) * 100));
			}
			setGeometryActivity(message || "Applying geometry…", rawPct);
		}
		function onJobLog(ev, line) {
			var msg = String(line || "").trim();
			if (!msg) {
				return;
			}
			if (/^\d+$/.test(msg) && fileTotal === 0) {
				fileTotal = Number(msg);
				verboseFinishLog("Transforming " + fileTotal + " file(s)…");
				if (fileTotal >= LONG_JOB_FILE_THRESHOLD && !longJobWarned) {
					longJobWarned = true;
					verboseFinishLog(
						"Large geometry job (" +
							fileTotal +
							" files) — may take several minutes. See Application log.",
					);
					setGeometryActivity(
						"Large job: transforming " + fileTotal + " files…",
						5,
					);
				}
				return;
			}
			verboseFinishLog(msg.replace(/^LOG:\s*/i, ""));
		}
		function onResult(ev, payload) {
			ipc.removeListener("updateLoad", onProgress);
			ipc.removeListener("cziJobLog", onJobLog);
			ipc.removeListener("applyGeometryResult", onResult);
			geometryRunning = false;
			updateWizardCancelVisibility();
			geometryState.persistLastApplyResult(
				wizardState.bundleRoot,
				wizardState.cziImport,
				payload || {},
			);
			if (!payload || payload.ok === false) {
				var errMsg = (payload && payload.error) || "Geometry apply failed";
				if (payload && payload.failed && payload.failed.length) {
					errMsg += ": " + payload.failed.slice(0, 3).join("; ");
				}
				setGeometryActivity("Geometry apply failed: " + errMsg, 0);
				verboseFinishLog("ERROR: " + errMsg);
				setFinishNavDisabled(false);
				reject(new Error(errMsg));
				return;
			}
			finalizeGeometryAfterApply(payload);
			finishWizard(payload)
				.then(function () {
					resolve(payload);
				})
				.catch(function (finishErr) {
					reject(finishErr);
				});
		}
		ipc.on("updateLoad", onProgress);
		ipc.on("cziJobLog", onJobLog);
		ipc.once("applyGeometryResult", onResult);
		var cfgPath = cziImport.importConfigPath(wizardState.bundleRoot);
		verboseFinishLog("Config: " + cfgPath);
		setGeometryActivity("Starting Python geometry apply…", 5);
		ipc.send("runApplyGeometry", [
			String(wizardState.bundleRoot || "").trim(),
			String(cfgPath || "").trim(),
		]);
	});
}

async function finishWizard(geometryPayload) {
	setActiveMaxRuns();
	await project.refreshProjectIndex(wizardState.bundleRoot);
	var status = qs("finishStatus");
	var report = project.computeMatchReport(
		project.readProjectFileIndex(wizardState.bundleRoot),
		["dapi", "max"],
	);
	var matched = (report.matchedSliceIds || []).length;
	var changed = geometryPayload && typeof geometryPayload.changed === "number"
		? geometryPayload.changed
		: 0;
	var filesTotal =
		geometryPayload && typeof geometryPayload.files_total === "number"
			? geometryPayload.files_total
			: changed;
	var elapsedSec =
		geometryPayload && typeof geometryPayload.elapsed_sec === "number"
			? geometryPayload.elapsed_sec
			: null;
	var bytesTotal =
		geometryPayload && typeof geometryPayload.bytes_total === "number"
			? geometryPayload.bytes_total
			: null;
	var geomSlices = countNonIdentityGeometry();
	var appliedAt = wizardState.cziImport.geometry_applied_at;
	var importResult = wizardState.importResult || {};
	var maxRuns = importResult.max_runs || {};
	var summary =
		"Import complete — " +
		changed +
		"/" +
		filesTotal +
		" file(s) transformed; " +
		(appliedAt ? "geometry applied" : geomSlices + " slice(s) with pending geometry") +
		"; " +
		matched +
		" DAPI/max slice(s) matched.";
	if (elapsedSec != null) {
		summary += " (" + elapsedSec + "s)";
	}
	setGeometryActivity(summary, 100);
	verboseFinishLog(summary);
	verboseFinishLog("Bundle: " + wizardState.bundleRoot);
	if (bytesTotal != null && bytesTotal > 0) {
		verboseFinishLog(
			"Bytes written: ~" + Math.round(bytesTotal / (1024 * 1024)) + " MB",
		);
	}
	if (wizardState.cziImport.preview_format_version) {
		verboseFinishLog(
			"Preview format v" + wizardState.cziImport.preview_format_version + " (PNG low-res previews)",
		);
	}
	if (wizardState.repairMode === "previews" || (wizardState.repairTargets && wizardState.repairTargets.length && wizardState.flow !== "reextract")) {
		verboseFinishLog("Note: preview repair was used earlier in this wizard session.");
	}
	if (wizardState.flow === "reextract") {
		verboseFinishLog("Note: selective CZI re-import was used in this session.");
	}
	var maxKeys = Object.keys(maxRuns);
	if (maxKeys.length) {
		verboseFinishLog("Primary max run(s):");
		for (var k = 0; k < maxKeys.length; k++) {
			verboseFinishLog("  " + maxKeys[k] + " → " + maxRuns[maxKeys[k]]);
		}
	}
	if (status) {
		status.textContent = summary;
	}
	populateImportHandoffPanel();
	setFinishNavDisabled(false);
}

function bindStep1() {
	var nameEl = qs("projectName");
	if (nameEl) {
		nameEl.addEventListener("input", updateBundlePathPreview);
	}
	qs("chooseParent").addEventListener("click", function () {
		ipc.once("returnPath", function (event, response) {
			if (response[0]) {
				wizardState.parentDir = response[0];
				qs("parentDir").value = response[0];
				updateBundlePathPreview();
			}
		});
		ipc.send("openDialog", { tag: "cziParent", defaultPath: wizardState.parentDir });
	});
	qs("step1Next").addEventListener("click", function () {
		if (!wizardState.bundleRoot) {
			alert("Choose parent folder and project name.");
			return;
		}
		tryResumeCziImportAfterStep1()
			.then(function (resumed) {
				if (!resumed) {
					setStep(2);
				}
			})
			.catch(function (err) {
				console.error("[CziWizard] resume", err);
				alert(String(err.message || err));
				setStep(2);
			});
	});
}

function bindStep2() {
	bindSectionIdentifierSelects();
	qs("addCziDir").addEventListener("click", function () {
		if (probeInFlight) {
			return;
		}
		ipc.once("returnPath", function (event, response) {
			if (!response[0]) {
				return;
			}
			var dir = cziImport.canonicalSourceDir(response[0]);
			if (
				wizardState.cziSourceDirs.some(function (d) {
					return sourceDirMatches(d, dir);
				})
			) {
				alert("That folder is already in the list.");
				return;
			}
			wizardState.cziSourceDirs.push(dir);
			wizardState.cziImport.source_dirs = wizardState.cziSourceDirs.slice();
			renderSourceDirList();
			probeIncrementalNewDir(dir).catch(function (err) {
				console.error("[CziWizard] probe", err);
			});
		});
		ipc.send("openDialog", {
			tag: "cziSource",
			defaultPath:
				(wizardState.cziSourceDirs.length &&
					wizardState.cziSourceDirs[wizardState.cziSourceDirs.length - 1]) ||
				wizardState.parentDir,
		});
	});
	var reprobeBtn = qs("reprobeAllCziDirs");
	if (reprobeBtn) {
		reprobeBtn.addEventListener("click", function () {
			if (!wizardState.cziSourceDirs.length) {
				return;
			}
			runProbeAll().catch(function (err) {
				alert(String(err.message || err));
			});
		});
	}
	renderSourceDirList();
	qs("step2Back").addEventListener("click", function () {
		setStep(1);
	});
	qs("step2Next").addEventListener("click", function () {
		setStep(3);
		yieldToUi().then(function () {
			renderStep3Panel();
		});
	});
}

function bindStep3() {
	bindSectionIdentifierSelects();
	var preserveRadio = qs("sliceNumberingPreserve");
	var renameRadio = qs("sliceNumberingRename");
	function onSliceNumberingChange() {
		wizardState.cziImport.slice_numbering = preserveRadio && preserveRadio.checked
			? cziImport.SLICE_NUMBERING_PRESERVE
			: cziImport.SLICE_NUMBERING_RENAME;
		renderStep3Panel();
	}
	if (preserveRadio) {
		preserveRadio.addEventListener("change", onSliceNumberingChange);
	}
	if (renameRadio) {
		renameRadio.addEventListener("change", onSliceNumberingChange);
	}
	var primarySel = qs("primarySignalRole");
	if (primarySel) {
		primarySel.addEventListener("change", function () {
			wizardState.cziImport.primary_signal_role = primarySel.value;
			updateStep3Validation();
		});
	}
	var globalRole = qs("globalChannelRole");
	if (globalRole) {
		globalRole.addEventListener("change", function () {
			var otherWrap = qs("otherNameGlobalWrap");
			if (otherWrap) {
				if (globalRole.value === cziImport.ROLE_OTHER) {
					otherWrap.classList.remove("d-none");
				} else {
					otherWrap.classList.add("d-none");
				}
			}
		});
	}
	var globalIndex = qs("globalChannelIndex");
	if (globalIndex) {
		globalIndex.addEventListener("change", function () {
			renderGlobalChannelBar();
		});
	}
	var applyBtn = qs("applyChannelDefaults");
	if (applyBtn) {
		applyBtn.addEventListener("click", function () {
			applyGlobalChannelDefaults();
		});
	}
	var keepAll = qs("keepAllChannels");
	if (keepAll) {
		keepAll.addEventListener("change", function () {
			var checked = keepAll.checked;
			var channels = wizardState.cziImport.channels || [];
			for (var i = 0; i < channels.length; i++) {
				channels[i].keep = checked;
			}
			renderChannelTable();
		});
	}
	qs("step3Back").addEventListener("click", function () {
		setStep(2);
	});
	qs("step3Next").addEventListener("click", function () {
		var err = validateStep3();
		if (err) {
			updateStep3Validation();
			return;
		}
		runExtract()
			.then(function () {
				setStep(5);
			})
			.catch(function (err) {
				console.error("[CziWizard]", err);
				if (
					err &&
					(/Extract cancelled/.test(String(err.message || err)) ||
						/^cancelled$/i.test(String(err.message || err)))
				) {
					return;
				}
				if (err && err.stack) {
					verboseExtractLog(err.stack);
				}
				alert(String(err.message || err));
			});
	});
}

function bindStep4() {
	var cancelBtn = qs("cancelExtract");
	if (cancelBtn) {
		cancelBtn.addEventListener("click", function () {
			verboseExtractLog("Cancelling extraction…");
			setExtractActivity("Cancelling extraction…", null);
			ipc.send("killCziImport");
		});
	}
	var continueBtn = qs("step4Continue");
	if (continueBtn) {
		continueBtn.addEventListener("click", function () {
			if (extractRunning) {
				return;
			}
			setStep4ContinueVisible(false);
			setStep(5);
		});
	}
}

function bindStep5() {
	qs("step5Back").addEventListener("click", function () {
		setStep(4);
	});
	var displaySelect = qs("orientDisplayChannel");
	if (displaySelect) {
		displaySelect.addEventListener("change", function (ev) {
			wizardState.orientDisplayChannel = ev.target.value;
			updateOrientPreviewBanner();
			renderOrientationGrid();
		});
	}
	var repairBtn = qs("orientRepairPreviews");
	if (repairBtn) {
		repairBtn.addEventListener("click", function () {
			runOrientPreviewRepair().catch(function (err) {
				alert(String(err.message || err));
			});
		});
	}
	qs("orientApplyAll").addEventListener("change", function (ev) {
		if (!ev.target.checked) {
			return;
		}
		var ids =
			isReextractFlow() && orientGridSliceIds().length
				? orientGridSliceIds()
				: cziImport.collectSliceIds(wizardState.cziImport);
		orientGeometry.copyFirstTileGeometryToAll(
			wizardState.cziImport.geometry,
			ids,
			qs("orientGrid"),
			isReextractPartialOrient()
				? function (sliceId, geom) {
					return shouldApplyCssPreviewForTile(sliceId, geom);
				}
				: null,
		);
		updateOrientStepChrome();
		updateOrientPreviewBanner();
		persistCziSettings();
	});
	qs("step5Next").addEventListener("click", function () {
		runApplyGeometry().catch(function (err) {
			alert(String(err.message || err));
		});
	});
}

function bindWizardStepPills() {
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	for (var i = 0; i < pills.length; i++) {
		pills[i].addEventListener("click", function (ev) {
			var pillStep = Number(ev.currentTarget.getAttribute("data-step"));
			if (pillStep === 5 && wizardState.step === 6 && !geometryRunning) {
				ev.preventDefault();
				setStep(5);
			}
		});
	}
}

function bindStep6() {
	var reviewBtn = qs("reviewOrientation");
	if (reviewBtn) {
		reviewBtn.addEventListener("click", function () {
			if (!geometryRunning) {
				setStep(5);
			}
		});
	}
}

function bindWizardNavigationGuards() {
	var cancel = qs("wizardCancel");
	if (cancel) {
		cancel.addEventListener("click", function (ev) {
			if ((extractRunning || geometryRunning) && !confirmLeaveDuringJob()) {
				ev.preventDefault();
			}
		});
	}
	var hub = qs("finishHub");
	if (hub) {
		hub.addEventListener("click", function (ev) {
			if (geometryRunning && !confirmLeaveDuringJob()) {
				ev.preventDefault();
			}
		});
	}
	window.addEventListener("beforeunload", function (ev) {
		if (extractRunning || geometryRunning) {
			ev.preventDefault();
			ev.returnValue = "";
		}
	});
}

function bindReextractSteps() {
	var showBlank = qs("reextractShowBlankOnly");
	if (showBlank) {
		showBlank.addEventListener("change", function () {
			reextractPicker.renderSliceList(reextractState, qs);
		});
	}
	var selectAll = qs("reextractSelectAllSlices");
	if (selectAll) {
		selectAll.addEventListener("click", function () {
			for (var i = 0; i < reextractState.sliceIds.length; i++) {
				var sid = reextractState.sliceIds[i];
				if (showBlank && showBlank.checked && !reextractState.blankSliceIds[sid]) {
					continue;
				}
				reextractState.selectedSliceIds[sid] = true;
			}
			reextractPicker.renderSliceList(reextractState, qs);
		});
	}
	var clearAll = qs("reextractClearAllSlices");
	if (clearAll) {
		clearAll.addEventListener("click", function () {
			reextractState.selectedSliceIds = {};
			reextractPicker.renderSliceList(reextractState, qs);
		});
	}
	var step1Next = qs("reextractStep1Next");
	if (step1Next) {
		step1Next.addEventListener("click", function () {
			if (!reextractPicker.selectedSliceIdList(reextractState).length) {
				alert("Select at least one section.");
				return;
			}
			reextractPicker.renderChannelList(reextractState, qs);
			setStep(2);
		});
	}
	var step2Back = qs("reextractStep2Back");
	if (step2Back) {
		step2Back.addEventListener("click", function () {
			setStep(1);
		});
	}
	var step2Next = qs("reextractStep2Next");
	if (step2Next) {
		step2Next.addEventListener("click", function () {
			if (!reextractPicker.selectedRoleKeyList(reextractState).length) {
				var val = qs("reextractChannelValidation");
				if (val) {
					val.textContent = "Select at least one channel.";
					val.classList.remove("d-none");
				}
				return;
			}
			var valHide = qs("reextractChannelValidation");
			if (valHide) {
				valHide.classList.add("d-none");
			}
			reextractPicker.renderConfirmStep(reextractState, qs);
			setStep(3);
		});
	}
	var step3Back = qs("reextractStep3Back");
	if (step3Back) {
		step3Back.addEventListener("click", function () {
			setStep(2);
		});
	}
	var confirmBox = qs("reextractConfirmOverwrite");
	if (confirmBox) {
		confirmBox.addEventListener("change", function () {
			reextractPicker.renderConfirmStep(reextractState, qs);
		});
	}
	var step3Run = qs("reextractStep3Run");
	if (step3Run) {
		step3Run.addEventListener("click", function () {
			var info = reextractPicker.renderConfirmStep(reextractState, qs);
			if (!info.validation.ok) {
				alert("Source CZI file(s) missing on disk.");
				return;
			}
			wizardState.repairTargets = reextractPicker.buildTargets(reextractState);
			runExtract({ repairOnly: true, mode: "reextract" })
				.then(function () {
					setStep(5);
				})
				.catch(function (err) {
					console.error("[CziWizard] reextract", err);
					alert(String(err.message || err));
				});
		});
	}
}

async function startReextractFlow(params) {
	project.tryRestoreActiveProject();
	if (!project.isActive()) {
		alert("Open a CZI-imported project before re-importing sections.");
		window.location.replace("./menu.html");
		return false;
	}
	wizardState.flow = "reextract";
	wizardState.bundleRoot = project.getBundleRoot();
	project.openProject(wizardState.bundleRoot);
	var proj = project.getProject() || project.readProjectJson(wizardState.bundleRoot);
	var saved = proj && proj.settings && proj.settings.czi_import;
	if (!saved || !saved.files || !saved.files.length) {
		alert(
			"This project has no CZI import settings. Use Import from Zeiss CZI for new projects.",
		);
		window.location.replace("./workspace_menu.html");
		return false;
	}
	hydrateWizardFromSavedCziImport(saved);
	cziImport.ensureOrientDapiPreviewsFromPipeline(wizardState.bundleRoot);
	updateReextractUiVisibility(true);
	var preselected = reextractPicker.parsePreselectedSlices(
		params ? params.toString() : window.location.search,
	);
	reextractPicker.initPicker(reextractState, wizardState.bundleRoot, saved, proj, preselected);
	bindReextractSteps();
	setStep(1);
	try {
		await reextractPicker.scanBlankPreviewsAsync(reextractState, qs);
	} catch (err) {
		console.error("[CziWizard] reextract blank scan", err);
		reextractPicker.renderSliceList(reextractState, qs);
	}
	return true;
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	bindStep1();
	bindStep2();
	bindStep3();
	bindStep4();
	bindStep5();
	bindStep6();
	bindAxonBitDepth();
	bindWizardStepPills();
	bindWizardNavigationGuards();
	var params = new URLSearchParams(window.location.search);
	if (params.get("flow") === "reextract") {
		startReextractFlow(params);
	} else {
		setStep(1);
	}
});
