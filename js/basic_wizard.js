"use strict";

var fs = require("fs");
var path = require("path");
var { ipcRenderer } = require("electron");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRuns = require("./pipeline_runs");
var maxDatasets = require("./max_datasets");
var projectIndexBusy = require("./project_index_busy");
var preprocessWizard = require("./preprocess_wizard");

var META = ".masonjar";
var CONFIG_NAME = "basic_run_config.json";
var PROGRESS_NAME = "basic_apply_progress.json";
var DEFAULT_VIEW_W = 512;
var DEFAULT_VIEW_H = 512;
var PREVIEW_READY_HINT =
	"Pan/zoom the image, adjust display levels, then click Preview correction.";

var state = {
	step: 0,
	channels: [],
	visited: {},
	paramsByChannel: {},
	running: false,
	previewBusy: false,
	lastOutputs: [],
	scale: 1,
	panX: 0,
	panY: 0,
	viewW: DEFAULT_VIEW_W,
	viewH: DEFAULT_VIEW_H,
	baseNaturalW: 0,
	baseNaturalH: 0,
	fullNaturalW: 0,
	fullNaturalH: 0,
	showingFiltered: false,
	lastFilterRoi: null,
	lastFullResFilterRoi: null,
	sourceAbs: "",
	baseAbs: "",
	displayMin: 0,
	displayMax: 255,
};

var baseBitmap = null;
var filteredBitmap = null;
var pendingPreviewAfterDims = false;
var lastPreviewProgressPct = 0;

function bundleRoot() {
	return project.isActive() ? project.getBundleRoot() : "";
}

function projectRoles() {
	var proj = project.getProject();
	return (proj && proj.roles) || pipelineRuns.CANONICAL_ROLES;
}

function metaDir() {
	return path.join(bundleRoot(), META);
}

function ensureMeta() {
	var d = metaDir();
	if (!fs.existsSync(d)) {
		fs.mkdirSync(d, { recursive: true });
	}
	return d;
}

function defaultParams() {
	return {
		get_darkfield: true,
		smoothness_flatfield: 1.0,
		smoothness_darkfield: 1.0,
		working_size: 128,
		sort_intensity: false,
		autotune: false,
	};
}

function readParamsFromUi() {
	return {
		get_darkfield: !!(document.getElementById("getDarkfield") || {}).checked,
		smoothness_flatfield: Number(
			(document.getElementById("smoothFlat") || {}).value || 1
		),
		smoothness_darkfield: Number(
			(document.getElementById("smoothDark") || {}).value || 1
		),
		working_size: Number(
			(document.getElementById("workingSize") || {}).value || 128
		),
		sort_intensity: !!(document.getElementById("sortIntensity") || {}).checked,
		autotune: !!(document.getElementById("autoTune") || {}).checked,
	};
}

// Disable manual smoothness inputs while auto-tune is on (BaSiC overrides them).
function syncAutotuneUi() {
	var on = !!(document.getElementById("autoTune") || {}).checked;
	["smoothFlat", "smoothDark"].forEach(function (id) {
		var el = document.getElementById(id);
		if (el) el.disabled = on;
	});
}

function writeParamsToUi(params) {
	params = params || defaultParams();
	var gd = document.getElementById("getDarkfield");
	var sf = document.getElementById("smoothFlat");
	var sd = document.getElementById("smoothDark");
	var ws = document.getElementById("workingSize");
	var si = document.getElementById("sortIntensity");
	var at = document.getElementById("autoTune");
	if (gd) gd.checked = !!params.get_darkfield;
	if (sf) sf.value = String(params.smoothness_flatfield);
	if (sd) sd.value = String(params.smoothness_darkfield);
	if (ws) ws.value = String(params.working_size);
	if (si) si.checked = !!params.sort_intensity;
	if (at) at.checked = !!params.autotune;
	syncAutotuneUi();
}

function currentChannelId() {
	var sel = document.getElementById("channelSelect");
	return sel ? String(sel.value || "") : "";
}

function saveCurrentChannelParams() {
	var id = currentChannelId();
	if (!id) return;
	state.paramsByChannel[id] = readParamsFromUi();
	state.visited[id] = true;
	updateVisitHelp();
}

function loadChannelParams(id) {
	writeParamsToUi(state.paramsByChannel[id] || defaultParams());
}

function setStep(n) {
	state.step = n;
	["step0", "step1", "step2", "finishPanel"].forEach(function (id, idx) {
		var el = document.getElementById(id === "finishPanel" ? id : id);
		if (!el) return;
		if (id === "finishPanel") {
			el.classList.toggle("d-none", n !== 3);
		} else {
			var stepNum = Number(id.replace("step", ""));
			el.classList.toggle("d-none", stepNum !== n);
		}
	});
	document.querySelectorAll("#wizardSteps .nav-link").forEach(function (pill) {
		var s = Number(pill.getAttribute("data-step"));
		pill.classList.toggle("active", s === n);
		pill.classList.toggle("disabled", s > n && !(n === 3 && s === 3));
		if (s <= n) pill.classList.remove("disabled");
	});
}

function setBusy(busy) {
	state.running = !!busy;
	[
		"processStart",
		"step1Next",
		"step1BackAttr",
		"step2Back",
		"previewFilterBtn",
		"channelSelect",
		"attrProceed",
	].forEach(function (id) {
		var el = document.getElementById(id);
		if (el) el.disabled = !!busy;
	});
	var cancel = document.getElementById("step2Cancel");
	if (cancel) cancel.classList.toggle("d-none", !busy);
}

function appendLog(line) {
	var log = document.getElementById("wizardLog");
	if (!log) return;
	log.textContent += line + "\n";
	log.scrollTop = log.scrollHeight;
}

// Delegates to the shared pipelineRuns.listImageSliceFiles() (2026-09-04
// consolidation -- was a private tif/tiff/png/jpg/jpeg scan+sort copy here;
// preprocess_wizard.js's DAPI-source listing needed the exact same filter
// and now shares this one implementation instead of keeping its own).
// Preserves this function's original contract exactly: array of absolute
// path STRINGS (fillSlices()/buildOutputAbsForSignal() below both index
// into it that way), same case-insensitive natural sort as before.
function listImageFiles(dir) {
	return pipelineRuns.listImageSliceFiles(dir).map(function (f) {
		return f.abs;
	});
}

function sliceIdFromPath(p) {
	var base = path.basename(p);
	var ome = base.toLowerCase().indexOf(".ome.");
	if (ome > 0) return base.slice(0, ome);
	return path.parse(base).name;
}

function discoverChannels() {
	var root = bundleRoot();
	var channels = [];
	var roles = projectRoles();
	var dapiAbs = pipelineRuns.resolveRoleBaseAbsForBundle(root, roles, "dapi");
	if (dapiAbs && fs.existsSync(dapiAbs) && listImageFiles(dapiAbs).length) {
		channels.push({
			id: "dapi",
			role: "dapi",
			label: "DAPI (counterstain)",
			source_abs: dapiAbs,
			output_abs: path.join(root, "data", "counting", "00_dapi_basic"),
			preview_suffix: "dapi",
			enabled: true,
		});
	}
	var branches = maxDatasets.listSignalBranches(root) || [];
	branches.forEach(function (branch) {
		var datasets = maxDatasets.listDatasetsForBranch(root, branch) || [];
		var prefer =
			maxDatasets.defaultDatasetForBranch(root, branch, { preferKind: "max" }) ||
			datasets[0];
		if (!prefer) return;
		channels.push({
			id: "signal:" + branch,
			role: "signal",
			label: "Signal — " + branch,
			signal_branch: branch,
			source_abs: prefer.abs,
			source_run_rel: prefer.rel,
			source_kind: prefer.kind,
			preview_suffix: branch,
			enabled: true,
		});
	});
	state.channels = channels;
	channels.forEach(function (ch) {
		if (!state.paramsByChannel[ch.id]) {
			state.paramsByChannel[ch.id] = defaultParams();
		}
	});
	return channels;
}

function fillChannelSelect() {
	var sel = document.getElementById("channelSelect");
	if (!sel) return;
	sel.innerHTML = "";
	state.channels.forEach(function (ch) {
		var opt = document.createElement("option");
		opt.value = ch.id;
		opt.textContent = ch.label;
		sel.appendChild(opt);
	});
	if (state.channels.length) {
		onChannelChanged();
	}
}

function currentChannel() {
	var id = currentChannelId();
	for (var i = 0; i < state.channels.length; i++) {
		if (state.channels[i].id === id) return state.channels[i];
	}
	return null;
}

function onChannelChanged() {
	saveCurrentChannelParams();
	var ch = currentChannel();
	var branchRow = document.getElementById("signalBranchRow");
	var sourceRow = document.getElementById("sourceDatasetRow");
	if (branchRow) branchRow.classList.toggle("d-none", !ch || ch.role !== "signal");
	if (sourceRow) sourceRow.classList.toggle("d-none", !ch || ch.role !== "signal");
	if (ch && ch.role === "signal") {
		fillSignalDatasets(ch);
	}
	loadChannelParams(ch ? ch.id : "");
	fillSlices();
	loadPreviewImage();
	updateVisitHelp();
}

function fillSignalDatasets(ch) {
	var root = bundleRoot();
	var branchSel = document.getElementById("signalBranchSelect");
	var sourceSel = document.getElementById("sourceDatasetSelect");
	if (!branchSel || !sourceSel) return;
	var branches = maxDatasets.listSignalBranches(root) || [];
	branchSel.innerHTML = "";
	branches.forEach(function (b) {
		var name = b.branch || b;
		var opt = document.createElement("option");
		opt.value = name;
		opt.textContent = name;
		if (name === ch.signal_branch) opt.selected = true;
		branchSel.appendChild(opt);
	});
	refreshSourceDatasets();
}

function refreshSourceDatasets() {
	var root = bundleRoot();
	var branchSel = document.getElementById("signalBranchSelect");
	var sourceSel = document.getElementById("sourceDatasetSelect");
	var ch = currentChannel();
	if (!branchSel || !sourceSel || !ch) return;
	var branch = branchSel.value;
	ch.signal_branch = branch;
	ch.preview_suffix = branch;
	ch.id = "signal:" + branch;
	var datasets = maxDatasets.listDatasetsForBranch(root, branch) || [];
	sourceSel.innerHTML = "";
	datasets.forEach(function (ds) {
		var opt = document.createElement("option");
		opt.value = ds.rel;
		opt.textContent = ds.label || ds.rel;
		opt.dataset.abs = ds.abs;
		opt.dataset.kind = ds.kind;
		sourceSel.appendChild(opt);
	});
	if (datasets.length) {
		sourceSel.value = ch.source_run_rel || datasets[0].rel;
		applySourceSelection();
	}
}

function applySourceSelection() {
	var sourceSel = document.getElementById("sourceDatasetSelect");
	var ch = currentChannel();
	if (!sourceSel || !ch) return;
	var opt = sourceSel.options[sourceSel.selectedIndex];
	if (!opt) return;
	ch.source_run_rel = opt.value;
	ch.source_abs = opt.dataset.abs;
	ch.source_kind = opt.dataset.kind;
	fillSlices();
	loadPreviewImage();
}

function fillSlices() {
	var ch = currentChannel();
	var sel = document.getElementById("sliceSelect");
	if (!sel || !ch) return;
	var files = listImageFiles(ch.source_abs);
	sel.innerHTML = "";
	files.forEach(function (f) {
		var opt = document.createElement("option");
		opt.value = f;
		opt.textContent = path.basename(f);
		sel.appendChild(opt);
	});
}

function updateVisitHelp() {
	var el = document.getElementById("channelVisitHelp");
	if (!el) return;
	var pending = state.channels.filter(function (c) {
		return !state.visited[c.id];
	});
	if (!pending.length) {
		el.textContent = "All channels visited — you can proceed to Process.";
	} else {
		el.textContent =
			"Still need to visit: " +
			pending
				.map(function (c) {
					return c.label;
				})
				.join(", ");
	}
}

function applyDisplayWindowToUi() {
	state.displayMin = Number((document.getElementById("displayMin") || {}).value || 0);
	state.displayMax = Number((document.getElementById("displayMax") || {}).value || 255);
	if (state.displayMax <= state.displayMin) {
		state.displayMax = state.displayMin + 1;
	}
	renderPreviewComposite();
}

function applyTransform() {
	var t = document.getElementById("preprocessPreviewTransform");
	if (!t) return;
	t.style.transform =
		"translate(" +
		state.panX +
		"px," +
		state.panY +
		"px) scale(" +
		(state.scale || 1) +
		")";
}

function updatePreviewZoomWarning() {
	var el = document.getElementById("preprocessPreviewZoomWarning");
	if (!el) return;
	preprocessWizard.computePreviewZoomPolicy(state);
	if (
		!state.showingFiltered &&
		state.previewZoomHint &&
		!preprocessWizard.isPreviewZoomEligible(state)
	) {
		el.textContent = state.previewZoomHint;
		el.classList.remove("d-none");
	} else {
		el.textContent = "";
		el.classList.add("d-none");
	}
}

function showPreviewLoading(on) {
	var el = document.getElementById("preprocessPreviewLoading");
	var vp = document.getElementById("preprocessPreviewViewport");
	if (el) el.classList.toggle("d-none", !on);
	if (vp) vp.classList.toggle("preprocess-preview-loading-active", !!on);
	if (on) {
		lastPreviewProgressPct = 0;
		var bar = document.getElementById("preprocessPreviewProgress");
		var txt = document.getElementById("preprocessPreviewProgressText");
		if (bar) bar.style.width = "0%";
		if (txt) txt.textContent = "0%";
	}
}

// Gap (px) between the stacked Original / Corrected panes in comparison mode.
function stackedPreviewGap(h) {
	return Math.max(2, Math.round((h || 0) * 0.02));
}

// Apply the display window to a sub-region already drawn on ctx at (x,y,w,h).
function applyWindowRegion(ctx, x, y, w, h) {
	if (w <= 0 || h <= 0) return;
	var d = ctx.getImageData(x, y, w, h);
	preprocessWizard.applyDisplayWindow(d, state.displayMin, state.displayMax);
	ctx.putImageData(d, x, y);
}

// Draw a translucent label chip at the top-left corner of a pane at (x, y).
function drawPaneLabel(ctx, text, x, y, labelH) {
	var pad = Math.max(2, Math.round(labelH * 0.25));
	ctx.font = labelH + "px sans-serif";
	ctx.textBaseline = "top";
	var tw = ctx.measureText(text).width;
	ctx.fillStyle = "rgba(0,0,0,0.6)";
	ctx.fillRect(x, y, tw + pad * 2, labelH + pad * 2);
	ctx.fillStyle = "#ffd400";
	ctx.fillText(text, x + pad, y + pad);
}

function renderPreviewComposite() {
	var previewImg = document.getElementById("preprocessPreviewImg");
	if (!previewImg) {
		applyTransform();
		return;
	}

	// Comparison mode: place Original (left) beside Corrected (right) as a 1x2 grid.
	if (state.showingFiltered && filteredBitmap && state.lastFilterRoi) {
		var roi = state.lastFilterRoi;
		var cw = filteredBitmap.width || filteredBitmap.naturalWidth;
		var chh = filteredBitmap.height || filteredBitmap.naturalHeight;
		if (cw && chh) {
			var gap = stackedPreviewGap(cw);
			var labelH = Math.max(12, Math.round(chh * 0.05));
			var canvas = document.createElement("canvas");
			canvas.width = cw * 2 + gap;
			canvas.height = chh;
			var ctx = canvas.getContext("2d");
			ctx.imageSmoothingEnabled = false;
			ctx.fillStyle = "#000";
			ctx.fillRect(0, 0, canvas.width, canvas.height);
			// Left: original cropped to the same ROI, scaled to corrected dims.
			if (baseBitmap) {
				ctx.drawImage(
					baseBitmap,
					roi.x,
					roi.y,
					roi.w,
					roi.h,
					0,
					0,
					cw,
					chh
				);
				applyWindowRegion(ctx, 0, 0, cw, chh);
			}
			// Right: corrected ROI.
			ctx.drawImage(filteredBitmap, cw + gap, 0);
			applyWindowRegion(ctx, cw + gap, 0, cw, chh);
			drawPaneLabel(ctx, "Original", 0, 0, labelH);
			drawPaneLabel(ctx, "Corrected", cw + gap, 0, labelH);
			previewImg.src = canvas.toDataURL("image/png");
			applyTransform();
			return;
		}
	}

	// Single view (original before preview, or corrected without ROI info).
	var bmp = state.showingFiltered && filteredBitmap ? filteredBitmap : baseBitmap;
	if (!bmp) {
		applyTransform();
		return;
	}
	var w = bmp.width || bmp.naturalWidth;
	var h = bmp.height || bmp.naturalHeight;
	if (!w || !h) {
		applyTransform();
		return;
	}
	var canvas2 = document.createElement("canvas");
	canvas2.width = w;
	canvas2.height = h;
	var ctx2 = canvas2.getContext("2d");
	ctx2.imageSmoothingEnabled = false;
	ctx2.drawImage(bmp, 0, 0);
	applyWindowRegion(ctx2, 0, 0, w, h);
	previewImg.src = canvas2.toDataURL("image/png");
	applyTransform();
}

function clearFilteredOverlay() {
	state.showingFiltered = false;
	state.filteredBitmap = null;
	state.lastFilterRoi = null;
	state.lastFullResFilterRoi = null;
	filteredBitmap = null;
	if (baseBitmap) {
		preprocessWizard.fitViewportToImage(state);
		renderPreviewComposite();
	}
}

function clearFilterOnViewChange() {
	if (!state.showingFiltered) return;
	clearFilteredOverlay();
	var status = document.getElementById("preprocessPreviewStatus");
	if (!preprocessWizard.isPreviewZoomEligible(state)) {
		updatePreviewZoomWarning();
	} else if (status) {
		status.textContent =
			"Pan/zoom cleared filter preview — click Preview correction to refresh.";
	}
}

function loadPreviewImage() {
	var sel = document.getElementById("sliceSelect");
	var previewImg = document.getElementById("preprocessPreviewImg");
	if (!sel || !previewImg || !sel.value) return;
	var ch = currentChannel();
	var sourceAbs = sel.value;
	state.sourceAbs = sourceAbs;
	var filePath = sourceAbs;
	if (ch) {
		var sid = sliceIdFromPath(filePath);
		var prev = path.join(
			bundleRoot(),
			"data",
			"counting",
			"_previews",
			sid + "_" + (ch.preview_suffix || "dapi") + ".png"
		);
		if (fs.existsSync(prev)) filePath = prev;
	}
	state.baseAbs = filePath;
	clearFilteredOverlay();
	state.scale = 1;
	state.panX = 0;
	state.panY = 0;
	state.fullNaturalW = 0;
	state.fullNaturalH = 0;
	var status = document.getElementById("preprocessPreviewStatus");
	if (status) status.textContent = PREVIEW_READY_HINT;

	var vp = document.getElementById("preprocessPreviewViewport");
	if (vp) {
		var rect = vp.getBoundingClientRect();
		state.viewW = Math.max(200, Math.floor(rect.width) || DEFAULT_VIEW_W);
		state.viewH = Math.max(200, Math.floor(rect.height) || DEFAULT_VIEW_H);
	}

	preprocessWizard.loadFullResDimensions(sourceAbs, function (fw, fh) {
		state.fullNaturalW = fw;
		state.fullNaturalH = fh;
		preprocessWizard.computePreviewZoomPolicy(state);
		updatePreviewZoomWarning();
		if (pendingPreviewAfterDims) {
			pendingPreviewAfterDims = false;
			if (fw > 0 && fh > 0) {
				requestPreview();
			} else if (status) {
				status.textContent = "Could not read full-resolution image dimensions.";
			}
		}
	});

	var img = new Image();
	img.onload = function () {
		baseBitmap = img;
		state.baseNaturalW = img.naturalWidth;
		state.baseNaturalH = img.naturalHeight;
		preprocessWizard.fitViewportToImage(state);
		preprocessWizard.computePreviewZoomPolicy(state);
		updatePreviewZoomWarning();
		renderPreviewComposite();
	};
	img.onerror = function () {
		baseBitmap = null;
		if (status) status.textContent = "Could not load slice image.";
	};
	img.src = preprocessWizard.fileUrlForPath(filePath) + "?t=" + Date.now();
}

function requestPreview() {
	saveCurrentChannelParams();
	var sel = document.getElementById("sliceSelect");
	var ch = currentChannel();
	var status = document.getElementById("preprocessPreviewStatus");
	if (!sel || !sel.value || !ch || state.previewBusy || state.running) return;

	var resolved = preprocessWizard.resolvePreviewRequest(
		state,
		filteredBitmap,
		sel.value
	);
	if (!resolved.ready) {
		if (resolved.reason === "zoom_too_far") {
			updatePreviewZoomWarning();
			return;
		}
		pendingPreviewAfterDims = true;
		if (status) status.textContent = "Loading full image dimensions…";
		return;
	}

	pendingPreviewAfterDims = false;
	state.lastFilterRoi = resolved.previewRoi || null;
	state.lastFullResFilterRoi = resolved.roi || null;
	state.previewBusy = true;
	showPreviewLoading(true);
	var previewBtn = document.getElementById("previewFilterBtn");
	if (previewBtn) previewBtn.disabled = true;
	if (status) status.textContent = "Running BaSiC preview…";

	var params = readParamsFromUi();
	var roi = resolved.roi;
	ipcRenderer.send("runBasicPreview", [
		resolved.filterAbs || sel.value,
		roi.x,
		roi.y,
		roi.w,
		roi.h,
		{
			previewDir: ensureMeta(),
			fitDir: ch.source_abs,
			get_darkfield: params.get_darkfield,
			smoothness_flatfield: params.smoothness_flatfield,
			smoothness_darkfield: params.smoothness_darkfield,
			working_size: params.working_size,
			sort_intensity: params.sort_intensity,
			autotune: params.autotune,
		},
	]);
}

function buildOutputAbsForSignal(ch) {
	var root = bundleRoot();
	var branch = ch.signal_branch || "signal";
	var stems = listImageFiles(ch.source_abs).map(sliceIdFromPath);
	var slug = pipelineRuns.buildRunSlug("basic", {
		sortedStems: stems,
		sourceKind: ch.source_kind || "max",
		sourceRunRel: ch.source_run_rel || "",
		smoothness: (state.paramsByChannel[ch.id] || defaultParams())
			.smoothness_flatfield,
	});
	var branchRoot = path.join(
		pipelineRuns.resolveRoleBaseAbsForBundle(root, projectRoles(), "max"),
		branch
	);
	return pipelineRuns.resolveRunLeaf(branchRoot, "basic", slug);
}

function buildConfig(opts) {
	opts = opts || {};
	saveCurrentChannelParams();
	var root = bundleRoot();
	var channels = state.channels.map(function (ch) {
		var params = state.paramsByChannel[ch.id] || defaultParams();
		var outAbs =
			ch.role === "dapi" ? ch.output_abs : buildOutputAbsForSignal(ch);
		return {
			id: ch.id,
			role: ch.role,
			enabled: true,
			signal_branch: ch.signal_branch || null,
			source_abs: ch.source_abs,
			source_run_rel: ch.source_run_rel || null,
			output_abs: outAbs,
			preview_suffix: ch.preview_suffix,
			params: params,
		};
	});
	return {
		bundle_root: root,
		channels: channels,
		force_refit: !!(document.getElementById("forceRefit") || {}).checked,
		start_fresh: !!(document.getElementById("startFresh") || {}).checked,
		resume: !opts.fresh,
		config_fingerprint: String(Date.now()),
	};
}

function writeConfig(cfg) {
	ensureMeta();
	var p = path.join(metaDir(), CONFIG_NAME);
	fs.writeFileSync(p, JSON.stringify(cfg, null, 2), "utf8");
	return p;
}

function loadInterruptedProgress() {
	var p = path.join(metaDir(), PROGRESS_NAME);
	if (!fs.existsSync(p)) return null;
	try {
		return JSON.parse(fs.readFileSync(p, "utf8"));
	} catch (_e) {
		return null;
	}
}

function prepareProcessStep() {
	var pending = state.channels.filter(function (c) {
		return !state.visited[c.id];
	});
	if (pending.length) {
		alert(
			"Visit and review parameters for every channel before Process:\n" +
				pending
					.map(function (c) {
						return c.label;
					})
					.join("\n")
		);
		return false;
	}
	var list = document.getElementById("channelConfirmList");
	if (list) {
		list.innerHTML = state.channels
			.map(function (c) {
				return "<li>" + c.label + " ← " + c.source_abs + "</li>";
			})
			.join("");
	}
	var banner = document.getElementById("resumeBanner");
	var prog = loadInterruptedProgress();
	if (banner) {
		if (prog && prog.interrupted) {
			banner.classList.remove("d-none");
			banner.textContent =
				"Previous shading run incomplete (last channel " +
				(prog.last_ok_channel || "?") +
				", slice " +
				(prog.last_ok_slice || "?") +
				"). Choose Resume (default) or Start fresh.";
		} else {
			banner.classList.add("d-none");
		}
	}
	return true;
}

function startProcess() {
	var cfg = buildConfig();
	state.lastOutputs = cfg.channels.map(function (c) {
		return { id: c.id, role: c.role, output_abs: c.output_abs, signal_branch: c.signal_branch };
	});
	var configPath = writeConfig(cfg);
	var log = document.getElementById("wizardLog");
	if (log) log.textContent = "";
	setBusy(true);
	appendLog("[BasicWizard] Starting " + configPath);
	ipcRenderer.send("runBasic", [configPath]);
}

function updateProjectTracking(ok) {
	var proj = project.getProject();
	if (!proj) return;
	if (!proj.processing) {
		proj.processing = project.defaultProcessing();
	}
	var basic = {
		last_run_at: new Date().toISOString(),
		interrupted: !ok,
		channels: (state.lastOutputs || []).map(function (o) {
			return {
				id: o.id,
				role: o.role,
				status: ok ? "done" : "failed",
				output_abs: o.output_abs,
				signal_branch: o.signal_branch || null,
			};
		}),
	};
	proj.processing.basic = basic;
	var setActive = document.getElementById("setActiveMax");
	if (ok && setActive && setActive.checked) {
		var signalOut = (state.lastOutputs || []).find(function (o) {
			return o.role === "signal";
		});
		if (signalOut && signalOut.output_abs) {
			var maxBase = pipelineRuns.resolveRoleBaseAbsForBundle(
				bundleRoot(),
				projectRoles(),
				"max"
			);
			var rel = path
				.relative(maxBase, signalOut.output_abs)
				.split(path.sep)
				.join("/");
			pipelineRuns.setActiveRunRel("max", rel);
		}
	}
	project.saveProjectJson();
	try {
		project.refreshProjectIndex(bundleRoot());
	} catch (_e) {}
}

function onRunFinished(result) {
	setBusy(false);
	var ok = result && result.ok;
	var msg = (result && result.message) || (ok ? "ok" : "failed");
	appendLog("[BasicWizard] " + msg);
	updateProjectTracking(!!ok);
	var summary = document.getElementById("finishSummary");
	if (summary) {
		summary.className = ok ? "alert alert-success" : "alert alert-danger";
		summary.textContent = ok
			? "Shading correction finished. Completed tasks on the workspace hub will list BaSiC outputs."
			: "Shading correction failed: " + msg;
	}
	setStep(3);
}

function wirePreviewPan() {
	var vp = document.getElementById("preprocessPreviewViewport");
	if (!vp) return;
	var rect = vp.getBoundingClientRect();
	state.viewW = Math.max(200, Math.floor(rect.width) || DEFAULT_VIEW_W);
	state.viewH = Math.max(200, Math.floor(rect.height) || DEFAULT_VIEW_H);

	var dragging = false;
	var lastX = 0;
	var lastY = 0;
	vp.addEventListener(
		"wheel",
		function (ev) {
			ev.preventDefault();
			clearFilterOnViewChange();
			var r = vp.getBoundingClientRect();
			var mx = ev.clientX - r.left;
			var my = ev.clientY - r.top;
			var delta = ev.deltaY > 0 ? 0.9 : 1.1;
			preprocessWizard.applyCursorAnchoredZoom(state, mx, my, delta);
			applyTransform();
			updatePreviewZoomWarning();
		},
		{ passive: false }
	);
	vp.addEventListener("mousedown", function (ev) {
		clearFilterOnViewChange();
		dragging = true;
		lastX = ev.clientX;
		lastY = ev.clientY;
	});
	window.addEventListener("mouseup", function () {
		if (dragging) {
			updatePreviewZoomWarning();
		}
		dragging = false;
	});
	window.addEventListener("mousemove", function (ev) {
		if (!dragging) return;
		state.panX += ev.clientX - lastX;
		state.panY += ev.clientY - lastY;
		lastX = ev.clientX;
		lastY = ev.clientY;
		applyTransform();
	});
}

function wire() {
	document.getElementById("attrProceed").addEventListener("click", function () {
		setStep(1);
		discoverChannels();
		fillChannelSelect();
		if (!state.channels.length) {
			var root = bundleRoot();
			alert(
				"No DAPI or signal max datasets found in this project.\n\n" +
					"Bundle root: " +
					(root || "(empty — is a project open?)")
			);
		}
	});
	var step1BackAttr = document.getElementById("step1BackAttr");
	if (step1BackAttr) {
		step1BackAttr.addEventListener("click", function () {
			if (state.running) return;
			saveCurrentChannelParams();
			setStep(0);
		});
	}
	document.getElementById("channelSelect").addEventListener("change", function () {
		onChannelChanged();
		state.visited[currentChannelId()] = true;
		updateVisitHelp();
	});
	var branchSel = document.getElementById("signalBranchSelect");
	if (branchSel) {
		branchSel.addEventListener("change", refreshSourceDatasets);
	}
	var sourceSel = document.getElementById("sourceDatasetSelect");
	if (sourceSel) {
		sourceSel.addEventListener("change", applySourceSelection);
	}
	document.getElementById("sliceSelect").addEventListener("change", loadPreviewImage);
	[
		"getDarkfield",
		"smoothFlat",
		"smoothDark",
		"workingSize",
		"sortIntensity",
		"autoTune",
	].forEach(function (id) {
		var el = document.getElementById(id);
		if (el) {
			el.addEventListener("change", saveCurrentChannelParams);
			el.addEventListener("input", saveCurrentChannelParams);
		}
	});
	var autoTuneEl = document.getElementById("autoTune");
	if (autoTuneEl) autoTuneEl.addEventListener("change", syncAutotuneUi);
	// Scroll wheel over a focused number input steps its value (respects min/max/step).
	document.querySelectorAll('input[type="number"]').forEach(function (el) {
		el.addEventListener(
			"wheel",
			function (e) {
				if (document.activeElement !== el || el.disabled) return;
				e.preventDefault();
				var step = parseFloat(el.step) || 1;
				var dir = e.deltaY < 0 ? 1 : -1;
				var v = (parseFloat(el.value) || 0) + dir * step;
				if (el.min !== "") v = Math.max(parseFloat(el.min), v);
				if (el.max !== "") v = Math.min(parseFloat(el.max), v);
				el.value = String(Number(v.toFixed(4)));
				el.dispatchEvent(new Event("change", { bubbles: true }));
			},
			{ passive: false }
		);
	});
	document.getElementById("displayMin").addEventListener("input", applyDisplayWindowToUi);
	document.getElementById("displayMax").addEventListener("input", applyDisplayWindowToUi);
	document.getElementById("previewFilterBtn").addEventListener("click", requestPreview);
	document.getElementById("step1Next").addEventListener("click", function () {
		saveCurrentChannelParams();
		state.visited[currentChannelId()] = true;
		if (!prepareProcessStep()) return;
		setStep(2);
	});
	document.getElementById("step2Back").addEventListener("click", function () {
		if (state.running) return;
		setStep(1);
	});
	document.getElementById("processStart").addEventListener("click", startProcess);
	document.getElementById("step2Cancel").addEventListener("click", function () {
		ipcRenderer.send("killBasic", []);
		appendLog("[BasicWizard] Cancel requested");
	});
	wirePreviewPan();

	ipcRenderer.on("basicPreviewResult", function (_e, payload) {
		state.previewBusy = false;
		showPreviewLoading(false);
		var previewBtn = document.getElementById("previewFilterBtn");
		if (previewBtn) previewBtn.disabled = !!state.running;
		var status = document.getElementById("preprocessPreviewStatus");
		if (!payload || !payload.ok) {
			if (status) {
				status.textContent =
					"Preview failed: " + ((payload && payload.error) || "unknown");
			}
			updatePreviewZoomWarning();
			return;
		}
		if (payload.autotuned) {
			var atRes = document.getElementById("autoTuneResult");
			var sfEl = document.getElementById("smoothFlat");
			var sdEl = document.getElementById("smoothDark");
			var sfv = payload.smoothness_flatfield;
			var sdv = payload.smoothness_darkfield;
			if (sfv != null && sfEl) sfEl.value = String(Number(sfv).toFixed(3));
			if (sdv != null && sdEl) sdEl.value = String(Number(sdv).toFixed(3));
			if (atRes) {
				atRes.textContent =
					sfv != null || sdv != null
						? "Auto-tuned — flatfield " +
						  (sfv != null ? Number(sfv).toFixed(3) : "n/a") +
						  ", darkfield " +
						  (sdv != null ? Number(sdv).toFixed(3) : "n/a")
						: "Auto-tune ran but returned no values (see log).";
			}
			saveCurrentChannelParams();
		}
		if (!payload.previewPath) {
			updatePreviewZoomWarning();
			return;
		}
		var filt = new Image();
		filt.onload = function () {
			filteredBitmap = filt;
			state.filteredBitmap = filt;
			state.showingFiltered = true;
			// Comparison mode places Original beside Corrected → fit to 2x width + gap.
			var stacked = !!state.lastFilterRoi;
			var fitW = stacked
				? filt.naturalWidth * 2 + stackedPreviewGap(filt.naturalWidth)
				: filt.naturalWidth;
			preprocessWizard.fitViewportToDimensions(
				state,
				fitW,
				filt.naturalHeight
			);
			renderPreviewComposite();
			if (status) {
				status.textContent = stacked
					? "Left: Original · Right: Corrected (" +
					  (payload.width || filt.naturalWidth) +
					  "x" +
					  (payload.height || filt.naturalHeight) +
					  " px) — pan/zoom clears preview; click Preview correction again."
					: "Corrected ROI (" +
					  (payload.width || filt.naturalWidth) +
					  "x" +
					  (payload.height || filt.naturalHeight) +
					  " px) — pan/zoom clears preview; click Preview correction again.";
			}
			updatePreviewZoomWarning();
		};
		filt.onerror = function () {
			state.showingFiltered = false;
			filteredBitmap = null;
			if (status) status.textContent = "Could not load corrected preview.";
			updatePreviewZoomWarning();
		};
		filt.src =
			preprocessWizard.fileUrlForPath(String(payload.previewPath)) +
			"?t=" +
			Date.now();
	});
	ipcRenderer.on("basicResult", function (_e, result) {
		onRunFinished(result || { ok: false, message: "no result" });
	});
	ipcRenderer.on("updateLoad", function (_e, response) {
		if (state.previewBusy && state.step === 1) {
			var previewPct = Number(response[0]) || 0;
			if (previewPct >= lastPreviewProgressPct) {
				lastPreviewProgressPct = previewPct;
				var previewBar = document.getElementById("preprocessPreviewProgress");
				var previewTxt = document.getElementById("preprocessPreviewProgressText");
				if (previewBar) previewBar.style.width = String(previewPct) + "%";
				if (previewTxt) {
					previewTxt.textContent =
						String(previewPct) + "% - " + (response[1] || "Preview…");
				}
			}
			return;
		}
		if (!state.running && state.step !== 2) return;
		var pct = Array.isArray(response) ? response[0] : 0;
		var msg = Array.isArray(response) ? response[1] : "";
		var bar = document.getElementById("processProgress");
		var pm = document.getElementById("processMessage");
		if (bar) bar.style.width = Math.max(0, Math.min(100, Number(pct) || 0)) + "%";
		if (pm) pm.textContent = msg || "";
		if (msg && String(msg).indexOf("LOG:") === 0) {
			appendLog(String(msg));
		}
	});
}

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	setStep(0);
	wire();
});
