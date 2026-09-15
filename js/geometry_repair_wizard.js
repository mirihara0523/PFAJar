"use strict";

var fs = require("fs");
var path = require("path");
var url = require("url");
var ipc = require("electron").ipcRenderer;

var pageInit = require("./page_init");
var navTrail = require("./nav_trail");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var cziImport = require("./czi_import");
var orientGeometry = require("./orient_geometry");
var geometryState = require("./geometry_state");
var branding = require("./branding");

project.tryRestoreActiveProject();
pipelineGate.assertPipelineAccess();

var LOG_MAX = 800;
var state = {
	bundleRoot: "",
	cziImport: null,
	sliceIds: [],
	applyState: null,
	queue: null,
	reviewIndex: 0,
	reviewTotal: 0,
	reviewOps: null,
	auditRunning: false,
	repairRunning: false,
};

function qs(id) {
	return document.getElementById(id);
}

function setStep(step) {
	var panels = ["step0", "step1", "step2", "step3", "step4"];
	for (var i = 0; i < panels.length; i++) {
		var el = qs(panels[i]);
		if (el) {
			el.classList.toggle("d-none", panels[i] !== "step" + step);
		}
	}
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	for (var p = 0; p < pills.length; p++) {
		var pill = pills[p];
		var n = Number(pill.getAttribute("data-step"));
		pill.classList.toggle("active", n === step);
		pill.classList.toggle("disabled", n > step);
	}
}

function appendLog(preId, line) {
	var pre = qs(preId);
	if (!pre) {
		return;
	}
	var text = (pre.textContent ? pre.textContent + "\n" : "") + line;
	var lines = text.split("\n");
	if (lines.length > LOG_MAX) {
		text = lines.slice(lines.length - LOG_MAX).join("\n");
	}
	pre.textContent = text;
	pre.scrollTop = pre.scrollHeight;
}

function setBar(barId, pct, statusId, msg) {
	var bar = qs(barId);
	if (bar && typeof pct === "number") {
		bar.style.width = String(pct) + "%";
		bar.setAttribute("aria-valuenow", String(pct));
	}
	if (statusId && msg) {
		var st = qs(statusId);
		if (st) {
			st.textContent = msg;
		}
	}
}

function fileUrlForPath(filePath) {
	if (!filePath || !fs.existsSync(filePath)) {
		return "";
	}
	try {
		return url.pathToFileURL(path.resolve(filePath)).href;
	} catch (e) {
		return url.pathToFileURL(filePath).href;
	}
}

function loadCziImportConfig() {
	var cfgPath = cziImport.importConfigPath(state.bundleRoot);
	if (fs.existsSync(cfgPath)) {
		try {
			var raw = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
			return raw.czi_import || raw;
		} catch (e) {
			console.warn("[GeometryRepair]", e);
		}
	}
	var proj = project.getProject() || {};
	if (proj.settings && proj.settings.czi_import) {
		return JSON.parse(JSON.stringify(proj.settings.czi_import));
	}
	return cziImport.buildDefaultCziImport("");
}

function writeProbeConfig(extra) {
	return geometryState.writeCziImportConfig(state.bundleRoot, state.cziImport, extra || {});
}

function writeRepairApplyConfig(geometryMap) {
	var cfgPath = path.join(state.bundleRoot, branding.META_DIR, "geometry_repair_apply.json");
	var merged = Object.assign({}, state.cziImport, {
		geometry: geometryMap,
		apply_source: "repair",
	});
	merged.config_fingerprint = cziImport.cziImportFingerprint(state.cziImport);
	merged.geometry_hash = geometryState.geometryOnlyHash(merged);
	fs.mkdirSync(path.dirname(cfgPath), { recursive: true });
	fs.writeFileSync(cfgPath, JSON.stringify({ czi_import: merged }, null, 2), "utf8");
	return cfgPath;
}

function selectedReferenceBranch() {
	var sel = qs("referenceBranchSelect");
	if (sel && sel.value) {
		return sel.value;
	}
	return geometryState.defaultReferenceBranch(state.cziImport);
}

function populateReferenceBranchSelect() {
	var sel = qs("referenceBranchSelect");
	if (!sel) {
		return;
	}
	var branches = {};
	var slices = (state.queue && state.queue.slices) || [];
	for (var i = 0; i < slices.length; i++) {
		var chs = slices[i].channels || [];
		for (var c = 0; c < chs.length; c++) {
			branches[chs[c].branch] = true;
		}
	}
	if (!Object.keys(branches).length) {
		var channels = cziImport.listOrientDisplayChannels(state.bundleRoot, state.cziImport);
		for (var k = 0; k < channels.length; k++) {
			var key = channels[k].key;
			if (key === cziImport.ORIENT_DISPLAY_DAPI || key === "dapi") {
				branches.dapi = true;
			} else {
				branches[key] = true;
			}
		}
	}
	var keys = Object.keys(branches).sort();
	if (!keys.length) {
		keys = [geometryState.defaultReferenceBranch(state.cziImport)];
	}
	var preferred =
		(state.queue && state.queue.reference_branch) ||
		selectedReferenceBranch() ||
		geometryState.defaultReferenceBranch(state.cziImport);
	sel.innerHTML = "";
	for (var n = 0; n < keys.length; n++) {
		var opt = document.createElement("option");
		opt.value = keys[n];
		opt.textContent = keys[n];
		sel.appendChild(opt);
	}
	sel.value = keys.indexOf(preferred) >= 0 ? preferred : keys[0];
}

function wireReferenceBranchSelect() {
	var sel = qs("referenceBranchSelect");
	if (!sel || sel.dataset.wired === "1") {
		return;
	}
	sel.dataset.wired = "1";
	sel.addEventListener("change", function () {
		if (state.auditRunning || state.repairRunning) {
			return;
		}
		var prior = state.queue && state.queue.reference_branch;
		if (!prior || sel.value === prior) {
			return;
		}
		if (
			!confirm(
				"Re-run the orientation audit using “" +
					sel.value +
					"” as the reference branch? Other channels will be compared against its tissue layout.",
			)
		) {
			sel.value = prior;
			return;
		}
		runAudit().catch(function (err) {
			appendLog("geometryRepairLog", "ERROR: " + String(err.message || err));
			setBar("auditProgress", 0, "auditStatus", "Audit failed: " + err.message);
		});
	});
}

function renderAuditSummary() {
	var el = qs("auditSummary");
	if (!el || !state.queue) {
		return;
	}
	var sum = geometryState.summarizeQueue(state.queue);
	var s = state.queue.summary || {};
	el.textContent =
		(sum.total || 0) +
		" sections scanned · " +
		(s.ok != null ? s.ok : sum.ok) +
		" OK · " +
		(s.need_review != null ? s.need_review : sum.needReview) +
		" need review · " +
		(s.auto_repairable != null ? s.auto_repairable : sum.autoRepairable) +
		" auto-repairable";
	if (state.applyState && state.applyState.previewBlocked) {
		el.className = "alert alert-warning small";
		el.textContent +=
			" — Repair previews in Orient first (invalid TIFF/missing _previews).";
	} else if (sum.needReview === 0 && sum.autoRepairable === 0 && sum.ok === sum.total) {
		el.className = "alert alert-success small";
		el.textContent =
			"No inconsistent geometry detected across " +
			(sum.total || 0) +
			" section(s). Back to Orient to adjust slices, or close this wizard.";
	} else {
		el.className = "alert alert-info small";
	}
	var step1Next = qs("step1Next");
	if (step1Next && state.queue) {
		var needsWork = sum.needReview > 0 || sum.autoRepairable > 0;
		step1Next.classList.toggle("d-none", !needsWork);
	}
}

function renderReviewSlice() {
	var reviewSlices = geometryState.slicesNeedingReview(state.queue);
	if (!reviewSlices.length) {
		setStep(3);
		renderConfirmSummary();
		return;
	}
	var awaiting = geometryState.slicesAwaitingReviewConfirmation(state.queue);
	if (!state.reviewTotal || state.reviewTotal < reviewSlices.length) {
		state.reviewTotal = reviewSlices.length;
	}
	state.reviewIndex = 0;
	var sl = reviewSlices[0];
	if (!sl) {
		setStep(3);
		renderConfirmSummary();
		return;
	}
	var reviewPosition = state.reviewTotal - reviewSlices.length + 1;
	var warnEl = qs("reviewBlockingBanner");
	if (warnEl) {
		if (awaiting.length) {
			warnEl.className = "alert alert-warning small";
			warnEl.textContent =
				reviewBlockingMessage() +
				" Use Confirm slice (after any rotation) or No rotation needed.";
		} else {
			warnEl.className = "d-none";
			warnEl.textContent = "";
		}
	}
	var suggested = geometryState.suggestedOpsForReviewSlice(sl);
	state.reviewOps = orientGeometry.cloneGeometry({ ops: suggested });
	qs("reviewSliceTitle").textContent =
		sl.slice_id + " (" + reviewPosition + "/" + state.reviewTotal + ")";
	var issueText = "Issue: " + (sl.issue || "review");
	if (suggested.length) {
		issueText +=
			" — audit suggests: " + orientGeometry.geometryStatusText({ ops: suggested });
	}
	qs("reviewSliceIssue").textContent = issueText;
	updateReviewOpsStatus();

	var grid = qs("reviewChannelGrid");
	grid.innerHTML = "";
	var channels = sl.channels || [];
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		var abs = path.join(state.bundleRoot, ch.rel_path);
		var pair = document.createElement("div");
		pair.className = "repair-thumb-pair";
		pair.innerHTML = "<div class='repair-thumb-label'>" + ch.branch + " (on disk)</div>";
		var imgDisk = document.createElement("img");
		imgDisk.src = fileUrlForPath(abs);
		imgDisk.alt = ch.branch;
		pair.appendChild(imgDisk);

		var targetWrap = document.createElement("div");
		targetWrap.className = "mt-1";
		targetWrap.innerHTML = "<div class='repair-thumb-label'>Target</div>";
		var viewport = document.createElement("div");
		viewport.style.transform = orientGeometry.geometryCssTransform(state.reviewOps);
		viewport.style.transformOrigin = "center center";
		var imgTarget = document.createElement("img");
		imgTarget.src = fileUrlForPath(abs);
		imgTarget.alt = ch.branch + " target";
		viewport.appendChild(imgTarget);
		targetWrap.appendChild(viewport);
		pair.appendChild(targetWrap);
		grid.appendChild(pair);
	}
}

function updateReviewOpsStatus() {
	var el = qs("reviewOpsStatus");
	if (el) {
		el.textContent = "Slice ops: " + orientGeometry.geometryStatusText(state.reviewOps);
	}
	var grid = qs("reviewChannelGrid");
	if (!grid) {
		return;
	}
	var viewports = grid.querySelectorAll("div[style*='transform']");
	for (var i = 0; i < viewports.length; i++) {
		viewports[i].style.transform = orientGeometry.geometryCssTransform(state.reviewOps);
	}
}

function confirmCurrentReviewSlice() {
	var reviewSlices = geometryState.slicesNeedingReview(state.queue);
	var sl = reviewSlices[0];
	if (!sl) {
		return;
	}
	sl.confirmed_ops = (state.reviewOps && state.reviewOps.ops) ? state.reviewOps.ops.slice() : [];
	sl.needs_manual_review = false;
	persistReviewSlice(sl);
	advanceReviewAfterConfirm();
}

function confirmCurrentReviewSliceNoChange() {
	var reviewSlices = geometryState.slicesNeedingReview(state.queue);
	var sl = reviewSlices[0];
	if (!sl) {
		return;
	}
	sl.confirmed_ops = [];
	sl.needs_manual_review = false;
	persistReviewSlice(sl);
	advanceReviewAfterConfirm();
}

function persistReviewSlice(sl) {
	for (var i = 0; i < (state.queue.slices || []).length; i++) {
		if (state.queue.slices[i].slice_id === sl.slice_id) {
			state.queue.slices[i] = sl;
			break;
		}
	}
	geometryState.writeRepairQueue(state.bundleRoot, state.queue);
}

function advanceReviewAfterConfirm() {
	state.reviewIndex = 0;
	renderReviewSlice();
}

function reviewBlockingMessage() {
	var awaiting = geometryState.slicesAwaitingReviewConfirmation(state.queue);
	if (!awaiting.length) {
		return "";
	}
	var ids = awaiting.slice(0, 8).map(function (sl) {
		return sl.slice_id;
	});
	var tail = awaiting.length > 8 ? "… +" + (awaiting.length - 8) + " more" : "";
	return (
		awaiting.length +
		" section(s) still need review — use Confirm slice or No rotation needed on each: " +
		ids.join(", ") +
		tail
	);
}

function updateRepairPlanControls() {
	var awaiting = geometryState.slicesAwaitingReviewConfirmation(state.queue);
	var runBtn = qs("runRepair");
	var warnEl = qs("confirmRepairWarning");
	if (warnEl) {
		if (awaiting.length) {
			warnEl.className = "alert alert-warning small";
			warnEl.textContent = reviewBlockingMessage();
		} else {
			warnEl.className = "d-none";
			warnEl.textContent = "";
		}
	}
	if (runBtn) {
		runBtn.disabled = awaiting.length > 0;
	}
}

function renderConfirmSummary() {
	var repairGeometry =
		state.repairGeometry || geometryState.buildRepairGeometryFromQueue(state.queue);
	var pre = qs("confirmSummary");
	if (pre) {
		var applyIds = Object.keys(repairGeometry).sort();
		var lines = [
			applyIds.length +
				" section(s) will receive full pipeline apply (00_dapi, _previews, z-stacks, max)",
			applyIds.join(", ") || "(none)",
		];
		pre.textContent = lines.join("\n");
	}
	state.repairGeometry = repairGeometry;
	updateRepairPlanControls();
}

function sendApplyGeometryJob(cfgPath, label) {
	return new Promise(function (resolve, reject) {
		function onProgress(ev, data) {
			var pct = Number(data[0]) || 0;
			var msg = String(data[1] || "");
			setBar("repairProgress", pct, "repairStatus", msg || label);
		}
		function onJobLog(ev, line) {
			var msg = String(line || "").trim();
			if (msg) {
				appendLog("repairLog", msg.replace(/^LOG:\s*/i, ""));
			}
		}
		function onResult(ev, payload) {
			ipc.removeListener("updateLoad", onProgress);
			ipc.removeListener("cziJobLog", onJobLog);
			ipc.removeListener("applyGeometryResult", onResult);
			if (!payload || payload.ok === false) {
				reject(new Error((payload && payload.error) || label + " failed"));
				return;
			}
			resolve(payload);
		}
		ipc.on("updateLoad", onProgress);
		ipc.on("cziJobLog", onJobLog);
		ipc.once("applyGeometryResult", onResult);
		ipc.send("runApplyGeometry", [String(state.bundleRoot).trim(), String(cfgPath).trim()]);
	});
}

function persistCziImportToProject() {
	var proj = project.getProject();
	if (!proj) {
		return;
	}
	proj.settings = proj.settings || {};
	proj.settings.czi_import = state.cziImport;
	project.saveProjectJson();
}

function runPostRepairVerify() {
	var probeCfg = writeProbeConfig({
		slice_ids: state.sliceIds,
		reference_branch: selectedReferenceBranch(),
		geometry: state.cziImport.geometry || {},
	});
	return new Promise(function (resolve, reject) {
		function onResult(ev, payload) {
			ipc.removeListener("geometryFingerprintResult", onResult);
			if (!payload || payload.ok === false) {
				reject(new Error((payload && payload.error) || "Post-repair verify failed"));
				return;
			}
			var slices = payload.slices || [];
			var failed = slices.filter(function (sl) {
				return sl.issue && sl.issue !== "ok";
			});
			appendLog(
				"repairLog",
				"Post-repair verify: " +
					(slices.length - failed.length) +
					"/" +
					slices.length +
					" OK",
			);
			if (failed.length) {
				appendLog(
					"repairLog",
					"Still flagged: " +
						failed
							.map(function (sl) {
								return sl.slice_id + " (" + sl.issue + ")";
							})
							.join(", "),
				);
			}
			resolve({ ok: failed.length === 0, failed: failed, payload: payload });
		}
		ipc.once("geometryFingerprintResult", onResult);
		ipc.send("runGeometryFingerprintProbe", [
			String(state.bundleRoot).trim(),
			String(probeCfg).trim(),
		]);
	});
}

function runRepair() {
	if (state.repairRunning) {
		return;
	}
	var awaiting = geometryState.slicesAwaitingReviewConfirmation(state.queue);
	if (awaiting.length) {
		alert(reviewBlockingMessage());
		return;
	}
	state.repairRunning = true;
	setStep(4);
	var repairGeometry =
		state.repairGeometry || geometryState.buildRepairGeometryFromQueue(state.queue);
	var applyIds = Object.keys(repairGeometry);
	if (!applyIds.length) {
		alert("No sections need rotation repair.");
		state.repairRunning = false;
		return;
	}
	state.repairGeometry = repairGeometry;
	appendLog(
		"repairLog",
		"Full pipeline apply for " + applyIds.length + " section(s): " + applyIds.join(", "),
	);
	var cfgPath = writeRepairApplyConfig(repairGeometry);
	return sendApplyGeometryJob(cfgPath, "Geometry repair apply")
		.then(function (payload) {
			geometryState.persistLastApplyResult(state.bundleRoot, state.cziImport, payload || {});
			geometryState.finalizeGeometryAfterApply(state.bundleRoot, state.cziImport, {
				sliceIds: state.sliceIds,
				payload: payload,
				applySource: "repair",
			});
			persistCziImportToProject();
			var summary =
				"Repair complete — " +
				(payload.changed != null ? payload.changed : "?") +
				" file(s) on " +
				applyIds.length +
				" section(s)";
			appendLog("repairLog", summary);
			setBar("repairProgress", 95, "repairStatus", "Verifying orientation…");
			return runPostRepairVerify().then(function (verify) {
				setBar("repairProgress", 100, "repairStatus", "Repair complete");
				if (!verify.ok) {
					appendLog(
						"repairLog",
						"WARNING: " +
							verify.failed.length +
							" section(s) still flagged after repair — review in Orient or re-run audit.",
					);
				}
				writeProbeConfig({});
				state.repairRunning = false;
				return payload;
			});
		})
		.catch(function (err) {
			state.repairRunning = false;
			setBar("repairProgress", 0, "repairStatus", "Repair failed");
			throw err;
		});
}

// Parse the user's slice-scope input against the full id list. Accepts exact
// slice IDs or 1-based numbers, comma/space separated. Returns matched ids (in
// full-list order) plus any tokens that matched nothing.
function parseSliceScope(input, allIds) {
	var out = [];
	var invalid = [];
	var picked = {};
	var idSet = {};
	for (var a = 0; a < allIds.length; a++) {
		idSet[String(allIds[a])] = a;
	}
	var tokens = String(input || "")
		.split(/[\s,]+/)
		.filter(function (t) {
			return t.length > 0;
		});
	for (var t = 0; t < tokens.length; t++) {
		var tok = tokens[t];
		var idx = -1;
		if (Object.prototype.hasOwnProperty.call(idSet, tok)) {
			idx = idSet[tok];
		} else if (/^\d+$/.test(tok)) {
			var n = parseInt(tok, 10);
			if (n >= 1 && n <= allIds.length) {
				idx = n - 1;
			}
		}
		if (idx >= 0) {
			picked[idx] = true;
		} else {
			invalid.push(tok);
		}
	}
	for (var i = 0; i < allIds.length; i++) {
		if (picked[i]) {
			out.push(allIds[i]);
		}
	}
	return { ids: out, invalid: invalid };
}

function runAudit() {
	if (state.auditRunning) {
		return Promise.resolve();
	}
	state.auditRunning = true;
	setStep(0);
	appendLog("geometryRepairLog", "Bundle: " + state.bundleRoot);
	setBar("auditProgress", 2, "auditStatus", "Loading slice list…");

	var allSliceIds = geometryState.resolveSliceIds(state.bundleRoot, state.cziImport);
	var scopeRaw = state.scopeRaw || "";
	var scoped = parseSliceScope(scopeRaw, allSliceIds);
	if (String(scopeRaw).trim() && !scoped.ids.length) {
		state.auditRunning = false;
		setBar(
			"auditProgress",
			0,
			"auditStatus",
			"No matching slices for: " + scopeRaw + " — check the IDs/numbers.",
		);
		return Promise.reject(new Error("No matching slices for scope"));
	}
	state.sliceIds = scoped.ids.length ? scoped.ids : allSliceIds;
	state.scopedSelection = scoped.ids.length > 0;
	if (scoped.invalid.length) {
		appendLog(
			"geometryRepairLog",
			"Ignored unknown slice tokens: " + scoped.invalid.join(", "),
		);
	}
	appendLog(
		"geometryRepairLog",
		"Auditing " +
			state.sliceIds.length +
			" of " +
			allSliceIds.length +
			" slice(s) — " +
			(scoped.ids.length ? "scoped selection" : "all slices"),
	);
	state.applyState = geometryState.assessGeometryApplyState(state.bundleRoot, state.cziImport, {
		sliceIds: state.sliceIds,
		previewHealth: cziImport.assessOrientPreviewHealth(state.bundleRoot, state.cziImport),
	});

	appendLog("geometryRepairLog", "Layer 1 policy: " + state.applyState.policyState);
	setBar("auditProgress", 15, "auditStatus", "Policy audit complete; starting fingerprint probe…");

	var probeCfg = writeProbeConfig({
		slice_ids: state.sliceIds,
		reference_branch: selectedReferenceBranch(),
		geometry: state.cziImport.geometry || {},
	});

	return new Promise(function (resolve, reject) {
		var fileTotal = 0;
		var fileDone = 0;

		function onProgress(ev, data) {
			var rawPct = Number(data[0]) || 0;
			var message = String(data[1] || "");
			var match = message.match(/\[(\d+)\/(\d+)\]/);
			if (match) {
				fileDone = Number(match[1]);
				fileTotal = Number(match[2]);
				rawPct = Math.min(94, 20 + Math.round((fileDone / fileTotal) * 75));
			}
			setBar("auditProgress", rawPct, "auditStatus", message || "Auditing sections…");
		}
		function onJobLog(ev, line) {
			var msg = String(line || "").trim();
			if (msg) {
				appendLog("geometryRepairLog", msg.replace(/^LOG:\s*/i, ""));
			}
		}
		function onResult(ev, payload) {
			ipc.removeListener("updateLoad", onProgress);
			ipc.removeListener("cziJobLog", onJobLog);
			ipc.removeListener("geometryFingerprintResult", onResult);
			state.auditRunning = false;
			if (!payload || payload.ok === false) {
				var errDetail = (payload && payload.error) || "Fingerprint probe failed";
				if (!state.sliceIds.length) {
					errDetail =
						"No tissue sections found — check that _previews PNGs or 00_dapi exist on this project.";
				}
				appendLog("geometryRepairLog", "ERROR: " + errDetail);
				setBar("auditProgress", 0, "auditStatus", "Audit failed: " + errDetail);
				reject(new Error(errDetail));
				return;
			}
			state.queue = geometryState.mergeProbeIntoQueue(state.bundleRoot, payload, state.cziImport);
			// The orientation fingerprint is unreliable for sections whose tissue
			// shape barely differs across orientations (structural_confidence is
			// low), so it can mark a truly mis-oriented slice "ok". When the user
			// explicitly scoped to specific slices, they already know these need
			// attention — force every scoped slice into manual review so they can
			// apply the correct rotation themselves, regardless of the auto verdict.
			if (state.scopedSelection && state.queue && state.queue.slices) {
				for (var qri = 0; qri < state.queue.slices.length; qri++) {
					if (!state.queue.slices[qri].confirmed_ops) {
						state.queue.slices[qri].needs_manual_review = true;
					}
				}
				geometryState.writeRepairQueue(state.bundleRoot, state.queue);
			}
			setBar("auditProgress", 100, "auditStatus", "Audit complete");
			appendLog("geometryRepairLog", "Queue written: " + geometryState.repairQueuePath(state.bundleRoot));
			populateReferenceBranchSelect();
			renderAuditSummary();
			setStep(1);
			resolve(state.queue);
		}

		ipc.on("updateLoad", onProgress);
		ipc.on("cziJobLog", onJobLog);
		ipc.once("geometryFingerprintResult", onResult);
		setBar("auditProgress", 22, "auditStatus", "Launching orientation probe…");
		ipc.send("runGeometryFingerprintProbe", [
			String(state.bundleRoot).trim(),
			String(probeCfg).trim(),
		]);
	});
}

function init() {
	navTrail.renderTrail(
		[
			{ label: "Start", href: "./menu.html" },
			{ label: "Workspace", href: "./workspace_menu.html" },
			{ label: "Orient", href: "./orient.html" },
			{ label: "Check Orientation Consistency" },
		],
		"navTrail",
	);

	state.bundleRoot = project.getBundleRoot();
	if (!state.bundleRoot || !project.isActive()) {
		setBar("auditProgress", 0, "auditStatus", "No active project.");
		return;
	}
	state.cziImport = loadCziImportConfig();

	// Slice scope comes from the ?slices= param that Orient sets when the user
	// checks "Send to consistency check" on specific slices. No param = all.
	var scopeParam = "";
	try {
		scopeParam = new URLSearchParams(window.location.search).get("slices") || "";
	} catch (_e) {
		scopeParam = "";
	}
	state.scopeRaw = scopeParam;
	var scopeSummaryEl = qs("repairScopeSummary");
	if (scopeSummaryEl) {
		if (String(scopeParam).trim()) {
			var allForSummary = geometryState.resolveSliceIds(state.bundleRoot, state.cziImport);
			var pre = parseSliceScope(scopeParam, allForSummary);
			scopeSummaryEl.textContent = pre.ids.length
				? "Auditing " + pre.ids.length + " selected slice(s): " + pre.ids.join(", ")
				: "Selection did not match any slice — all slices will be audited.";
		} else {
			scopeSummaryEl.textContent = "No selection — all slices will be audited.";
		}
	}

	populateReferenceBranchSelect();
	wireReferenceBranchSelect();

	var step1NextBtn = qs("step1Next");
	if (step1NextBtn) {
		step1NextBtn.addEventListener("click", function () {
			var sel = qs("referenceBranchSelect");
			if (sel && state.queue) {
				state.queue.reference_branch = sel.value;
				geometryState.writeRepairQueue(state.bundleRoot, state.queue);
			}
			var needReview = geometryState.slicesNeedingReview(state.queue).length;
			if (needReview > 0) {
				state.reviewIndex = 0;
				state.reviewTotal = needReview;
				setStep(2);
				renderReviewSlice();
			} else {
				setStep(3);
				renderConfirmSummary();
			}
		});
	}
	qs("reviewRot90").addEventListener("click", function () {
		state.reviewOps = orientGeometry.applyGeometryAction(state.reviewOps, "rot90");
		updateReviewOpsStatus();
	});
	qs("reviewFlipX").addEventListener("click", function () {
		state.reviewOps = orientGeometry.applyGeometryAction(state.reviewOps, "flipX");
		updateReviewOpsStatus();
	});
	qs("reviewFlipY").addEventListener("click", function () {
		state.reviewOps = orientGeometry.applyGeometryAction(state.reviewOps, "flipY");
		updateReviewOpsStatus();
	});
	qs("reviewConfirmSlice").addEventListener("click", confirmCurrentReviewSlice);
	var reviewNoChangeBtn = qs("reviewNoChangeSlice");
	if (reviewNoChangeBtn) {
		reviewNoChangeBtn.addEventListener("click", confirmCurrentReviewSliceNoChange);
	}
	qs("runRepair").addEventListener("click", function () {
		runRepair().catch(function (err) {
			alert(String(err.message || err));
		});
	});
	qs("step3Back").addEventListener("click", function () {
		state.reviewIndex = 0;
		setStep(2);
		renderReviewSlice();
	});

	// Audit is user-initiated (so it can be scoped to specific slices instead of
	// always fingerprinting every slice on the NAS). Blank scope = audit all.
	var startBtn = qs("startAuditBtn");
	if (startBtn) {
		startBtn.addEventListener("click", function () {
			if (state.auditRunning) {
				return;
			}
			runAudit().catch(function (err) {
				appendLog("geometryRepairLog", "ERROR: " + String(err.message || err));
				setBar("auditProgress", 0, "auditStatus", "Audit failed: " + err.message);
			});
		});
	}
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	init();
});
