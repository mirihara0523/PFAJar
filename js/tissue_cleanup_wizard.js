"use strict";

var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var branding = require("./branding");
var project = require("./project");
var fileIndex = require("./file_index");
var cziImport = require("./czi_import");
var tissuePaths = require("../js/bundle_slice_paths");
var canvasMod = require("./tissue_cleanup_canvas");
var projectIndexBusy = require("./project_index_busy");
var perfLog = require("./perf_log");

var VERSION = "1";
var DRAFT_DIR = "tissue_cleanup_draft";
var APPLY_CONFIG = "tissue_cleanup_apply_config.json";

var state = {
	step: 1,
	sliceIds: [],
	currentIndex: 0,
	slices: {},
	running: false,
};

var wizardSteps = document.getElementById("wizardSteps");
var step1 = document.getElementById("step1");
var step2 = document.getElementById("step2");
var step3 = document.getElementById("step3");
var step4 = document.getElementById("step4");
var sliceCounter = document.getElementById("sliceCounter");
var sliceIdLabel = document.getElementById("sliceIdLabel");
var canvasStatus = document.getElementById("canvasStatus");
var confirmHeadline = document.getElementById("confirmHeadline");
var confirmFileCount = document.getElementById("confirmFileCount");
var confirmTableBody = document.getElementById("confirmTableBody");
var confirmGrid = document.getElementById("confirmGrid");
var applyProgress = document.getElementById("applyProgress");
var applyMessage = document.getElementById("applyMessage");
var wizardLog = document.getElementById("wizardLog");
var summaryPanel = document.getElementById("summaryPanel");

var tracePointCountEl = document.getElementById("tracePointCount");
var traceDoneBtn = document.getElementById("traceDoneBtn");

function updateTraceUi(pointCount) {
	if (tracePointCountEl) {
		tracePointCountEl.textContent =
			pointCount > 0 ? pointCount + " point(s) placed" : "";
	}
	if (traceDoneBtn) {
		traceDoneBtn.disabled = pointCount < 2;
	}
}

var canvas = canvasMod.createTissueCleanupCanvas({
	canvas: document.getElementById("tissueCanvas"),
	viewport: document.getElementById("tissueCanvasViewport"),
	onTraceChange: updateTraceUi,
	onOrphansPruned: function (n) {
		if (canvasStatus && n > 0) {
			canvasStatus.textContent =
				"Removed " + n + " stray pixel(s) after erasing.";
		}
	},
	onMaskEdited: function (method) {
		markSliceEdited(method);
		persistCurrentSlice();
	},
});

function initPaneDivider() {
	var workspace = document.querySelector(".tissue-workspace-grid");
	var divider = document.getElementById("tissuePaneDivider");
	if (!workspace || !divider) {
		return;
	}
	var minPercent = 25;
	var maxPercent = 75;
	var currentPercent = 50;
	var dragging = false;

	function applyPercent(percent) {
		currentPercent = Math.max(minPercent, Math.min(maxPercent, percent));
		workspace.style.setProperty("--tissue-left-pane", currentPercent + "%");
		divider.setAttribute("aria-valuemin", String(minPercent));
		divider.setAttribute("aria-valuemax", String(maxPercent));
		divider.setAttribute("aria-valuenow", String(Math.round(currentPercent)));
	}

	function applyClientX(clientX) {
		var rect = workspace.getBoundingClientRect();
		if (!rect.width) {
			return;
		}
		applyPercent(((clientX - rect.left) / rect.width) * 100);
	}

	divider.addEventListener("pointerdown", function (ev) {
		if (ev.button !== 0) {
			return;
		}
		dragging = true;
		divider.classList.add("is-dragging");
		divider.setPointerCapture(ev.pointerId);
		applyClientX(ev.clientX);
		ev.preventDefault();
	});
	divider.addEventListener("pointermove", function (ev) {
		if (dragging) {
			applyClientX(ev.clientX);
		}
	});
	function finishDrag(ev) {
		if (!dragging) {
			return;
		}
		dragging = false;
		divider.classList.remove("is-dragging");
		if (ev && divider.hasPointerCapture(ev.pointerId)) {
			divider.releasePointerCapture(ev.pointerId);
		}
		// Fit once at the final width so repeated pointer movement does not reset
		// the user's zoom/pan state while they are dragging the divider.
		canvas.fitToViewport();
	}
	divider.addEventListener("pointerup", finishDrag);
	divider.addEventListener("pointercancel", finishDrag);
	divider.addEventListener("keydown", function (ev) {
		var next = currentPercent;
		if (ev.key === "ArrowLeft") {
			next -= 2;
		} else if (ev.key === "ArrowRight") {
			next += 2;
		} else if (ev.key === "Home") {
			next = minPercent;
		} else if (ev.key === "End") {
			next = maxPercent;
		} else {
			return;
		}
		applyPercent(next);
		canvas.fitToViewport();
		ev.preventDefault();
	});
	applyPercent(currentPercent);
}

initPaneDivider();

function edgeShrinkPx() {
	var el = document.getElementById("edgeShrinkPx");
	if (!el) {
		return 2;
	}
	// +N shrink (erode), -N grow (dilate), 0 none. Use NaN check so a real 0 is
	// not coerced to the default.
	var v = Number(el.value);
	if (isNaN(v)) {
		v = 2;
	}
	return Math.max(-10, Math.min(10, v));
}

function bundleRoot() {
	return project.isActive() ? project.getBundleRoot() : "";
}

function metaDir() {
	return path.join(bundleRoot(), branding.META_DIR);
}

function draftDir() {
	return path.join(metaDir(), DRAFT_DIR);
}

function draftStatePath() {
	return path.join(draftDir(), "state.json");
}

function maskPathForSlice(sliceId) {
	return path.join(draftDir(), "masks", sliceId + ".png");
}

function fileUrl(absPath) {
	return "file://" + absPath.split(path.sep).join("/");
}

function defaultSliceMeta() {
	return { method: "untouched", edited: false };
}

function loadCziCfg() {
	var root = bundleRoot();
	if (!root) {
		return {};
	}
	var pj = project.readProjectJson(root);
	var czi = (pj.settings && pj.settings.czi_import) || {};
	return czi;
}

function listSliceIds() {
	var root = bundleRoot();
	if (!root) {
		return [];
	}
	var dapiDir = path.join(root, "data/counting/00_dapi");
	if (!fs.existsSync(dapiDir)) {
		return [];
	}
	var ids = [];
	var entries = fs.readdirSync(dapiDir);
	for (var i = 0; i < entries.length; i++) {
		if (/\.png$/i.test(entries[i])) {
			ids.push(entries[i].replace(/\.png$/i, ""));
		}
	}
	ids.sort(function (a, b) {
		return a.localeCompare(b, undefined, { numeric: true });
	});
	var pj = project.readProjectJson(root);
	var index = fileIndex.readFileIndex(root, metaDir());
	var report = fileIndex.computeMatchReport(index, fileIndex.INPUT_MATCH_ROLES);
	return fileIndex.getProcessingSliceIds(root, pj, index, report, {
		stepId: "tissue_cleanup",
	}).filter(function (sid) {
		return ids.indexOf(sid) >= 0;
	});
}

function previewPathForSlice(sliceId) {
	var root = bundleRoot();
	var orient = cziImport.orientDapiPreviewPath(root, sliceId);
	if (fs.existsSync(orient)) {
		return orient;
	}
	var pipeline = cziImport.dapiPreviewPath(root, sliceId);
	if (fs.existsSync(pipeline)) {
		return pipeline;
	}
	return "";
}

function ensureDraftDirs() {
	fs.mkdirSync(path.join(draftDir(), "masks"), { recursive: true });
}

function readDraftState() {
	try {
		var raw = fs.readFileSync(draftStatePath(), "utf8");
		return JSON.parse(raw);
	} catch (_err) {
		return null;
	}
}

function writeDraftState() {
	ensureDraftDirs();
	var payload = {
		version: VERSION,
		slice_order: state.sliceIds,
		current_index: state.currentIndex,
		slices: state.slices,
	};
	fs.writeFileSync(draftStatePath(), JSON.stringify(payload, null, 2));
}

function persistCurrentSlice() {
	var sliceId = state.sliceIds[state.currentIndex];
	if (!sliceId) {
		return;
	}
	ensureDraftDirs();
	canvas.exportMaskPngPath(fs, path, maskPathForSlice(sliceId));
	var meta = state.slices[sliceId] || defaultSliceMeta();
	meta.edited = !canvas.maskIsAllKeep();
	state.slices[sliceId] = meta;
	writeDraftState();
}

function setStep(n) {
	state.step = n;
	if (step1) {
		step1.classList.toggle("d-none", n !== 1);
	}
	if (step2) {
		step2.classList.toggle("d-none", n !== 2);
	}
	if (step3) {
		step3.classList.toggle("d-none", n !== 3);
	}
	if (step4) {
		step4.classList.toggle("d-none", n !== 4);
	}
	if (wizardSteps) {
		var pills = wizardSteps.querySelectorAll("[data-step]");
		for (var i = 0; i < pills.length; i++) {
			var pill = pills[i];
			var sn = Number(pill.getAttribute("data-step"));
			pill.classList.remove("active", "disabled");
			if (sn === n) {
				pill.classList.add("active");
			} else if (sn < n) {
				pill.classList.remove("disabled");
			} else {
				pill.classList.add("disabled");
			}
		}
	}
}

function currentSliceId() {
	return state.sliceIds[state.currentIndex] || "";
}

function updateSliceUi() {
	var total = state.sliceIds.length;
	var idx = state.currentIndex;
	if (sliceCounter) {
		sliceCounter.textContent =
			"Section " + (total ? idx + 1 : 0) + " / " + total;
	}
	if (sliceIdLabel) {
		sliceIdLabel.textContent = currentSliceId();
	}
}

function appendLog(line) {
	if (!wizardLog) {
		return;
	}
	wizardLog.textContent += line + "\n";
	wizardLog.scrollTop = wizardLog.scrollHeight;
}

function markSliceEdited(method) {
	var sliceId = currentSliceId();
	if (!sliceId) {
		return;
	}
	var meta = state.slices[sliceId] || defaultSliceMeta();
	if (meta.method === "untouched") {
		meta.method = method;
	} else if (meta.method !== method) {
		meta.method = "mixed";
	}
	meta.edited = !canvas.maskIsAllKeep();
	state.slices[sliceId] = meta;
}

async function loadCurrentSlice() {
	var sliceId = currentSliceId();
	if (!sliceId) {
		if (canvasStatus) {
			canvasStatus.textContent = "No sections with DAPI previews.";
		}
		return;
	}
	var preview = previewPathForSlice(sliceId);
	if (!preview) {
		if (canvasStatus) {
			canvasStatus.textContent = "No preview for " + sliceId;
		}
		return;
	}
	canvas.setMode("idle");
	if (canvasStatus) {
		canvasStatus.textContent = "Loading " + path.basename(preview) + "…";
	}
	await canvas.loadImageUrl(fileUrl(preview) + "?t=" + Date.now());
	await canvas.loadMaskFromFile(fs, path, maskPathForSlice(sliceId));
	if (!state.slices[sliceId]) {
		state.slices[sliceId] = defaultSliceMeta();
	}
	var meta = state.slices[sliceId];
	var hasMaskFile = fs.existsSync(maskPathForSlice(sliceId));
	canvas.setSliceUntouched(meta.method === "untouched");
	canvas.setMaskVisible(hasMaskFile && meta.method !== "untouched");
	if (canvasStatus) {
		canvasStatus.textContent =
			meta.method === "untouched" && !hasMaskFile
				? "Green = keep; red = remove → black on Apply (overlay appears after you edit the mask)."
				: preview;
	}
	updateSliceUi();
	writeDraftState();
}

function goSlice(delta) {
	persistCurrentSlice();
	state.currentIndex = Math.max(
		0,
		Math.min(state.sliceIds.length - 1, state.currentIndex + delta),
	);
	loadCurrentSlice().catch(function (err) {
		console.error(err);
	});
}

function resetCurrentSlice() {
	var sliceId = currentSliceId();
	if (!sliceId) {
		return;
	}
	canvas.pushUndo();
	canvas.resetMaskAllKeep();
	try {
		fs.unlinkSync(maskPathForSlice(sliceId));
	} catch (_err) {}
	state.slices[sliceId] = defaultSliceMeta();
	var preview = previewPathForSlice(sliceId);
	if (preview) {
		canvas.loadImageUrl(fileUrl(preview) + "?r=" + Date.now()).catch(console.error);
	}
	writeDraftState();
}

function maskOutputPath() {
	return path.join(draftDir(), "_auto_mask.png");
}

function runAutoMask() {
	var sliceId = currentSliceId();
	var preview = previewPathForSlice(sliceId);
	if (!preview || state.running) {
		return;
	}
	state.running = true;
	if (canvasStatus) {
		canvasStatus.textContent = "Running auto tissue mask…";
	}
	ipc.send("runTissueCleanupAuto", [
		preview,
		maskOutputPath(),
		edgeShrinkPx(),
	]);
}

// Run one section's auto mask, writing the keep-mask straight to its draft mask
// path, and resolve when the result arrives. A one-shot listener is used so the
// batch loop can await each section sequentially.
function runAutoOneSlice(preview, maskOut, shrink) {
	return new Promise(function (resolve) {
		var settled = false;
		function onResult(_ev, payload) {
			if (settled) {
				return;
			}
			settled = true;
			ipc.removeListener("tissueCleanupAutoResult", onResult);
			resolve(!!(payload && payload.ok));
		}
		ipc.on("tissueCleanupAutoResult", onResult);
		ipc.send("runTissueCleanupAuto", [preview, maskOut, shrink]);
	});
}

// Attempt Auto across every section. Sequential so progress is reportable and
// so we do not launch dozens of Python processes at once. Auto overwrites each
// section's mask; the user still reviews/edits each afterwards.
async function runAutoMaskAll() {
	if (state.running) {
		return;
	}
	var ids = state.sliceIds.slice();
	if (!ids.length) {
		return;
	}
	if (
		!confirm(
			"Run Attempt Auto on all " +
				ids.length +
				" section(s)? This overwrites each section's current mask with the auto result.",
		)
	) {
		return;
	}
	state.running = true;
	state.batchRunning = true;
	ensureDraftDirs();
	var shrink = edgeShrinkPx();
	var okCount = 0;
	var failCount = 0;
	var skipped = 0;
	for (var i = 0; i < ids.length; i++) {
		var sid = ids[i];
		if (canvasStatus) {
			canvasStatus.textContent =
				"Auto-masking " + (i + 1) + " / " + ids.length + " (" + sid + ")…";
		}
		var preview = previewPathForSlice(sid);
		if (!preview) {
			skipped += 1;
			continue;
		}
		var ok = await runAutoOneSlice(preview, maskPathForSlice(sid), shrink);
		if (ok) {
			var meta = state.slices[sid] || defaultSliceMeta();
			meta.method = "auto";
			meta.edited = true;
			state.slices[sid] = meta;
			okCount += 1;
		} else {
			failCount += 1;
		}
		writeDraftState();
	}
	state.batchRunning = false;
	state.running = false;
	if (canvasStatus) {
		canvasStatus.textContent =
			"Auto mask complete — " +
			okCount +
			" ok, " +
			failCount +
			" failed, " +
			skipped +
			" no-preview. Review each section, then Confirm.";
	}
	loadCurrentSlice().catch(function (err) {
		console.error(err);
	});
}

function runGuidedMask(strokePoints) {
	var sliceId = currentSliceId();
	var preview = previewPathForSlice(sliceId);
	if (!preview || !strokePoints.length || state.running) {
		return;
	}
	if (strokePoints.length < 2) {
		if (canvasStatus) {
			canvasStatus.textContent = "Place at least two trace points.";
		}
		return;
	}
	var strokePath = path.join(draftDir(), "_stroke.json");
	var jsonPts =
		typeof strokePoints[0] === "number" || Array.isArray(strokePoints[0])
			? strokePoints
			: canvas.getTracePointsForJson();
	fs.writeFileSync(strokePath, JSON.stringify(jsonPts));
	state.running = true;
	if (canvasStatus) {
		canvasStatus.textContent = "Running trace-guided mask…";
	}
	ipc.send("runTissueCleanupGuided", [
		preview,
		maskOutputPath(),
		strokePath,
		edgeShrinkPx(),
	]);
}

function confirmStatusClass(status) {
	if (status === "will apply") {
		return "will-apply";
	}
	if (status === "no preview") {
		return "no-preview";
	}
	return "unchanged";
}

function loadImg(src) {
	return new Promise(function (resolve, reject) {
		var im = new Image();
		im.onload = function () {
			resolve(im);
		};
		im.onerror = reject;
		im.src = src;
	});
}

// Composite the keep/remove mask over the preview at thumbnail size, using the
// same green(keep)/red(remove) tint as the editor. Returns a PNG data URL.
async function compositeMaskThumb(previewPath, maskPath, maxW) {
	var img = await loadImg(fileUrl(previewPath) + "?t=" + Date.now());
	var w = img.naturalWidth || img.width || 1;
	var h = img.naturalHeight || img.height || 1;
	var scale = Math.min(1, (maxW || 128) / w);
	var cw = Math.max(1, Math.round(w * scale));
	var ch = Math.max(1, Math.round(h * scale));
	var c = document.createElement("canvas");
	c.width = cw;
	c.height = ch;
	var cx = c.getContext("2d");
	cx.drawImage(img, 0, 0, cw, ch);
	var mask = null;
	try {
		mask = await loadImg(fileUrl(maskPath) + "?t=" + Date.now());
	} catch (_e) {
		mask = null;
	}
	if (mask) {
		var mc = document.createElement("canvas");
		mc.width = cw;
		mc.height = ch;
		var mctx = mc.getContext("2d");
		mctx.drawImage(mask, 0, 0, cw, ch);
		var id = mctx.getImageData(0, 0, cw, ch);
		var d = id.data;
		for (var i = 0; i < d.length; i += 4) {
			if (d[i] >= 128) {
				d[i] = 40;
				d[i + 1] = 200;
				d[i + 2] = 80;
				d[i + 3] = 115;
			} else {
				d[i] = 255;
				d[i + 1] = 40;
				d[i + 2] = 40;
				d[i + 3] = 140;
			}
		}
		mctx.putImageData(id, 0, 0);
		cx.drawImage(mc, 0, 0);
	}
	return c.toDataURL("image/png");
}

function buildConfirmTile(sliceId, index, status, method, fileCount, preview) {
	var tile = document.createElement("div");
	tile.className = "confirm-tile " + confirmStatusClass(status);
	tile.title = "Click to edit " + sliceId;

	var media;
	if (preview) {
		var img = document.createElement("img");
		img.loading = "lazy";
		img.alt = sliceId;
		img.src = fileUrl(preview);
		media = img;
		// Edited sections: overlay the keep(green)/remove(red) mask like the editor.
		if (status === "will apply") {
			compositeMaskThumb(preview, maskPathForSlice(sliceId), 128)
				.then(function (url) {
					img.src = url;
				})
				.catch(function () {
					// keep the raw preview on failure
				});
		}
	} else {
		media = document.createElement("div");
		media.className = "confirm-noimg";
		media.textContent = "no preview";
	}
	tile.appendChild(media);

	var idDiv = document.createElement("div");
	idDiv.className = "confirm-id";
	idDiv.textContent = sliceId;
	tile.appendChild(idDiv);
	var methodDiv = document.createElement("div");
	methodDiv.className = "confirm-method";
	methodDiv.textContent = method;
	tile.appendChild(methodDiv);

	var badgeText = status;
	if (status === "will apply" && fileCount != null) {
		badgeText = "Will apply · " + fileCount + " files";
	}
	var badge = document.createElement("span");
	badge.className = "confirm-badge";
	badge.textContent = badgeText;
	tile.appendChild(badge);

	tile.addEventListener("click", function () {
		state.currentIndex = index;
		setStep(1);
		loadCurrentSlice().catch(function (err) {
			console.error(err);
		});
	});
	return tile;
}

function buildConfirmTable() {
	if (!confirmGrid) {
		return { edited: 0, files: 0 };
	}
	confirmGrid.innerHTML = "";
	var cfg = loadCziCfg();
	var root = bundleRoot();
	var edited = 0;
	var files = 0;
	// Perf accumulators — pathsForSlice / previewPathForSlice each do many NAS
	// stat/readdir calls, which dominate this step on network bundles.
	var _pfsMs = 0;
	var _pfsCalls = 0;
	var _prevMs = 0;
	var frag = document.createDocumentFragment();
	for (var i = 0; i < state.sliceIds.length; i++) {
		var sliceId = state.sliceIds[i];
		var meta = state.slices[sliceId] || defaultSliceMeta();
		var maskPath = maskPathForSlice(sliceId);
		var unchanged = !fs.existsSync(maskPath) || meta.method === "untouched";
		var willApply = meta.edited && !unchanged;
		// Compute the affected-file count once per slice (was previously computed
		// twice — for the running total and the table cell — doubling NAS work).
		var fileCount = null;
		if (willApply) {
			var _t = perfLog.now();
			fileCount = tissuePaths.pathsForSlice(root, sliceId, cfg).length;
			_pfsMs += perfLog.now() - _t;
			_pfsCalls += 1;
			edited += 1;
			files += fileCount;
		}
		var _tp = perfLog.now();
		var preview = previewPathForSlice(sliceId);
		_prevMs += perfLog.now() - _tp;
		var status = unchanged ? "unchanged" : meta.edited ? "will apply" : "unchanged";
		if (!preview) {
			status = "no preview";
		}
		frag.appendChild(
			buildConfirmTile(sliceId, i, status, meta.method, fileCount, preview),
		);
	}
	confirmGrid.appendChild(frag);
	perfLog.perfLog("tissueConfirm.pathsForSlice", _pfsMs);
	perfLog.perfLog("tissueConfirm.previewPaths", _prevMs);
	perfLog.perfLog("tissueConfirm.pathsForSlice_calls", _pfsCalls);
	return { edited: edited, files: files, total: state.sliceIds.length };
}

function writeApplyConfig() {
	var root = bundleRoot();
	var cfg = loadCziCfg();
	var slices = {};
	for (var i = 0; i < state.sliceIds.length; i++) {
		var sliceId = state.sliceIds[i];
		var meta = state.slices[sliceId] || defaultSliceMeta();
		if (!meta.edited) {
			continue;
		}
		var mp = maskPathForSlice(sliceId);
		if (!fs.existsSync(mp)) {
			continue;
		}
		slices[sliceId] = { mask_path: mp, method: meta.method };
	}
	var payload = {
		bundle_root: root,
		czi_config: path.join(metaDir(), "czi_import_config.json"),
		slices: slices,
		dry_run: false,
	};
	var out = path.join(metaDir(), APPLY_CONFIG);
	fs.writeFileSync(out, JSON.stringify(payload, null, 2));
	return out;
}

function runApply() {
	if (state.running) {
		return;
	}
	state.running = true;
	var _t0 = perfLog.now();
	var configPath = perfLog.perfSection("tissueApply.writeConfig", function () {
		return writeApplyConfig();
	});
	if (wizardLog) {
		wizardLog.textContent = "";
	}
	appendLog("[TissueCleanup] Applying masks…");
	perfLog.perfSection("tissueApply.setStep", function () {
		setStep(3);
	});
	var cancelBtn = document.getElementById("applyCancel");
	if (cancelBtn) {
		cancelBtn.classList.remove("d-none");
	}
	perfLog.perfLog("tissueApply.transitionTotal", perfLog.now() - _t0);
	ipc.send("runTissueCleanupApply", [bundleRoot(), configPath]);
}

function finishApply(result) {
	state.running = false;
	var cancelBtn = document.getElementById("applyCancel");
	if (cancelBtn) {
		cancelBtn.classList.add("d-none");
	}
	if (result && result.ok) {
		var root = bundleRoot();
		var pj = project.readProjectJson(root);
		pj.processing = pj.processing || project.defaultProcessing();
		pj.processing.tissue_cleanup = {
			applied_at: new Date().toISOString(),
			version: VERSION,
			slices: result.slices || {},
		};
		project.saveProjectJson(root, pj);
		project.refreshProjectIndex(root).catch(function (err) {
			console.warn("[TissueCleanup] refreshProjectIndex:", err);
		});
		try {
			fs.rmSync(draftDir(), { recursive: true, force: true });
		} catch (_err) {}
	}
	setStep(4);
	if (summaryPanel) {
		if (result && result.ok) {
			summaryPanel.innerHTML =
				"<p class=\"text-success fw-semibold\">Tissue edge cleanup applied.</p>" +
				"<p>Files updated: <strong>" +
				String(result.applied_files || 0) +
				"</strong>. Slices: <strong>" +
				String(result.slices_applied || 0) +
				"</strong>.</p>";
		} else {
			summaryPanel.innerHTML =
				"<p class=\"text-danger fw-semibold\">Apply failed.</p><pre class=\"small\">" +
				(result && result.error ? result.error : "Unknown error") +
				"</pre>";
		}
	}
}

function init() {
	var root = bundleRoot();
	if (!root) {
		alert("Open a project bundle first.");
		window.location.href = "./workspace_menu.html";
		return;
	}
	state.sliceIds = listSliceIds();
	for (var i = 0; i < state.sliceIds.length; i++) {
		var sid = state.sliceIds[i];
		if (!state.slices[sid]) {
			state.slices[sid] = defaultSliceMeta();
		}
	}
	var draft = readDraftState();
	var proc = project.readProjectJson(root).processing || {};
	if (draft && !proc.tissue_cleanup) {
		state.sliceIds = draft.slice_order || state.sliceIds;
		state.currentIndex = draft.current_index || 0;
		state.slices = draft.slices || state.slices;
	}
	if (!state.sliceIds.length) {
		alert("No DAPI PNG sections found under 00_dapi.");
	}
	updateSliceUi();
	loadCurrentSlice().catch(console.error);
}

ipc.on("tissueCleanupAutoResult", function (_ev, payload) {
	// During a batch (Attempt Auto for all sections) the batch loop owns the
	// result handling and canvas state; skip the single-slice handler.
	if (state.batchRunning) {
		return;
	}
	state.running = false;
	if (!payload || !payload.ok) {
		if (canvasStatus) {
			canvasStatus.textContent =
				(payload && payload.error) || "Auto mask failed";
		}
		return;
	}
	canvas.setMaskVisible(true);
	canvas.setSliceUntouched(false);
	if (payload.maskBase64) {
		canvas.loadMaskFromBase64(payload.maskBase64).then(function () {
			markSliceEdited("auto");
			persistCurrentSlice();
		});
	} else if (payload.maskPath && fs.existsSync(payload.maskPath)) {
		canvas.loadMaskFromFile(fs, path, payload.maskPath).then(function () {
			markSliceEdited("auto");
			persistCurrentSlice();
		});
	}
	if (canvasStatus) {
		canvasStatus.textContent =
			"Green = keep; red = remove (becomes black on Apply). Use Eraser to paint red remove regions.";
	}
});

ipc.on("tissueCleanupGuidedResult", function (_ev, payload) {
	state.running = false;
	canvas.clearTrace();
	canvas.setMode("idle");
	updateTraceUi(0);
	var traceAutoBtn = document.getElementById("traceAutoBtn");
	if (traceDoneBtn) {
		traceDoneBtn.classList.add("d-none");
		traceDoneBtn.disabled = true;
	}
	if (traceAutoBtn) {
		traceAutoBtn.classList.remove("d-none");
	}
	if (!payload || !payload.ok) {
		if (canvasStatus) {
			canvasStatus.textContent =
				(payload && payload.error) || "Guided mask failed";
		}
		return;
	}
	var done = payload.maskBase64
		? canvas.loadMaskFromBase64(payload.maskBase64)
		: canvas.loadMaskFromFile(fs, path, payload.maskPath);
	canvas.setMaskVisible(true);
	canvas.setSliceUntouched(false);
	Promise.resolve(done).then(function () {
		markSliceEdited("trace_auto");
		persistCurrentSlice();
		if (canvasStatus) {
			canvasStatus.textContent =
				"Green = keep; red = remove (becomes black on Apply). Trace-guided mask applied.";
		}
	});
});

ipc.on("tissueCleanupApplyResult", function (_ev, payload) {
	finishApply(payload || { ok: false, error: "No result" });
});

ipc.on("updateLoad", function (_ev, data) {
	if (state.step !== 3) {
		return;
	}
	var pct = data[0];
	var msg = data[1] || "";
	if (applyProgress) {
		applyProgress.style.width = String(pct) + "%";
	}
	if (applyMessage) {
		applyMessage.textContent = msg;
	}
	appendLog(msg);
});

document.getElementById("prevSliceBtn").addEventListener("click", function () {
	goSlice(-1);
});
document.getElementById("nextSliceBtn").addEventListener("click", function () {
	goSlice(1);
});
document.getElementById("attemptAutoBtn").addEventListener("click", runAutoMask);
var attemptAutoAllBtn = document.getElementById("attemptAutoAllBtn");
if (attemptAutoAllBtn) {
	attemptAutoAllBtn.addEventListener("click", runAutoMaskAll);
}
document.getElementById("traceAutoBtn").addEventListener("click", function () {
	canvas.clearTrace();
	canvas.setMode("trace");
	document.getElementById("traceAutoBtn").classList.add("d-none");
	if (traceDoneBtn) {
		traceDoneBtn.classList.remove("d-none");
		traceDoneBtn.disabled = true;
	}
	updateTraceUi(0);
	if (canvasStatus) {
		canvasStatus.textContent =
			"Click on the image to place points along the tissue edge. Click Done tracing when finished.";
	}
});
if (traceDoneBtn) {
	traceDoneBtn.addEventListener("click", function () {
		runGuidedMask(canvas.getTracePointsForJson());
	});
}
function syncBrushButtons() {
	var eraserBtn = document.getElementById("eraserBtn");
	var keepBtn = document.getElementById("keepBrushBtn");
	if (eraserBtn) {
		eraserBtn.classList.toggle("active", canvas.state.mode === "erase");
	}
	if (keepBtn) {
		keepBtn.classList.toggle("active", canvas.state.mode === "keep");
	}
}
document.getElementById("eraserBtn").addEventListener("click", function () {
	var next = canvas.state.mode === "erase" ? "idle" : "erase";
	canvas.setMode(next);
	syncBrushButtons();
	if (canvasStatus && next === "erase") {
		canvasStatus.textContent =
			"Paint over areas to REMOVE (shown in red; replaced with black on Apply). Stray pixels are cleaned when you release the mouse.";
	}
});
var keepBrushBtn = document.getElementById("keepBrushBtn");
if (keepBrushBtn) {
	keepBrushBtn.addEventListener("click", function () {
		var next = canvas.state.mode === "keep" ? "idle" : "keep";
		canvas.setMode(next);
		syncBrushButtons();
		if (canvasStatus && next === "keep") {
			canvasStatus.textContent =
				"Paint to KEEP tissue (shown in green). Use this to recover real tissue trimmed too aggressively at the edges.";
		}
	});
}
var eraserSizeInput = document.getElementById("eraserSize");
function commitEraserSize(input) {
	var value = Number(input.value);
	if (isNaN(value)) {
		value = 16;
	}
	value = Math.max(5, Math.min(50, value));
	input.value = String(value);
	canvas.setEraserSize(value);
}
eraserSizeInput.addEventListener("input", function (ev) {
	// Do not rewrite the field while the user is typing. For example, clamping
	// the first digit of "35" from 3 to 5 made the completed entry become 55.
	var value = Number(ev.target.value);
	if (!isNaN(value) && value >= 5 && value <= 50) {
		canvas.setEraserSize(value);
	}
});
eraserSizeInput.addEventListener("change", function (ev) {
	commitEraserSize(ev.target);
});
eraserSizeInput.addEventListener("blur", function (ev) {
	commitEraserSize(ev.target);
});

// Wheel over the numeric value changes it by one step. Restrict the handler to
// the input itself so the right-side controls panel remains normally scrollable.
function initWheelAdjuster(inputId) {
	var input = document.getElementById(inputId);
	if (!input) {
		return;
	}
	var mn = Number(input.min);
	var mx = Number(input.max);
	var stp = Number(input.step) || 1;
	function clampVal(v) {
		if (!isNaN(mn)) {
			v = Math.max(mn, v);
		}
		if (!isNaN(mx)) {
			v = Math.min(mx, v);
		}
		return v;
	}
	input.addEventListener("wheel", function (ev) {
		if (ev.ctrlKey || ev.metaKey || !ev.deltaY) {
			return;
		}
		var current = Number(input.value);
		if (isNaN(current)) {
			current = !isNaN(mn) ? mn : 0;
		}
		var v = clampVal(current + (ev.deltaY < 0 ? stp : -stp));
		if (String(v) !== String(input.value)) {
			input.value = String(v);
			input.dispatchEvent(new Event("input", { bubbles: true }));
		}
		ev.preventDefault();
	}, { passive: false });
}
initWheelAdjuster("eraserSize");
initWheelAdjuster("edgeShrinkPx");

document.getElementById("resetSliceBtn").addEventListener("click", resetCurrentSlice);
document.getElementById("undoBtn").addEventListener("click", function () {
	if (canvas.undo()) {
		markSliceEdited("eraser");
	}
});
document.getElementById("step1Next").addEventListener("click", function () {
	var _t0 = perfLog.now();
	perfLog.perfSection("tissueConfirm.persistCurrentSlice", function () {
		persistCurrentSlice();
	});
	var stats = perfLog.perfSection("tissueConfirm.buildTable", function () {
		return buildConfirmTable();
	});
	if (confirmHeadline) {
		confirmHeadline.textContent =
			"You edited " +
			stats.edited +
			" of " +
			stats.total +
			" sections.";
	}
	if (confirmFileCount) {
		confirmFileCount.textContent =
			"Apply will modify an estimated " +
			stats.files +
			" files across DAPI previews, orient previews, z-stacks, and max/sharpen/top-hat TIFFs.";
	}
	perfLog.perfSection("tissueConfirm.setStep", function () {
		setStep(2);
	});
	perfLog.perfLog("tissueConfirm.total", perfLog.now() - _t0);
});
document.getElementById("step2Back").addEventListener("click", function () {
	setStep(1);
});
document.getElementById("step2Apply").addEventListener("click", runApply);
document.getElementById("applyCancel").addEventListener("click", function () {
	ipc.send("killTissueCleanup");
});

window.addEventListener("resize", function () {
	canvas.fitToViewport();
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	init();
});
