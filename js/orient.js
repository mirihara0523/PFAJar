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
var orientSlicePlan = require("./orient_slice_plan");
var geometryState = require("./geometry_state");
var branding = require("./branding");
var projectIndexBusy = require("./project_index_busy");

var geometryRunning = false;
var previewRepairRunning = false;
var orientState = {
	bundleRoot: "",
	cziImport: null,
	displayChannel: cziImport.ORIENT_DISPLAY_DAPI,
	// slice_id -> true for slices the user has checked to send to the scoped
	// Check Orientation Consistency audit (only offered once geometry is applied).
	consistencySelection: {},
};

function qs(id) {
	return document.getElementById(id);
}

function reextractGeometryScope() {
	return (
		orientState.cziImport &&
		orientState.cziImport.reextract_geometry_scope
	);
}

function isReextractPartialOrient() {
	return !!reextractGeometryScope();
}

function orientGridSliceIds() {
	var scope = reextractGeometryScope();
	if (isReextractPartialOrient() && scope) {
		var scoped = cziImport.sliceIdsInReextractScope(scope);
		if (scoped.length) {
			return scoped;
		}
	}
	return ensureSlicePlan();
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
		orientState.displayChannel,
		scope,
		orientState.cziImport,
	);
}

function previewUrlCacheBuster() {
	var appliedAt =
		orientState.cziImport && orientState.cziImport.geometry_applied_at;
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
	var appliedAt =
		orientState.cziImport && orientState.cziImport.geometry_applied_at;
	var text = orientGeometry.orientPostApplySummaryText(
		appliedAt,
		orientState.cziImport && orientState.cziImport.geometry_applied_files_total,
	);
	if (text) {
		el.textContent = text;
		el.classList.remove("d-none");
	} else {
		el.textContent = "";
		el.classList.add("d-none");
	}
}

function verboseLog(msg) {
	console.log("[Orient]", msg);
	var el = qs("orientLog");
	if (el) {
		el.textContent = (el.textContent ? el.textContent + "\n" : "") + msg;
		el.scrollTop = el.scrollHeight;
	}
}

function setActivity(msg, pct) {
	var bar = qs("orientProgress");
	var status = qs("orientStatus");
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

function loadCziImportConfig() {
	var cfgPath = cziImport.importConfigPath(orientState.bundleRoot);
	if (fs.existsSync(cfgPath)) {
		try {
			var raw = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
			return raw.czi_import || raw;
		} catch (e) {
			console.warn("[Orient] config read failed", e);
		}
	}
	var proj = project.getProject() || {};
	if (proj.settings && proj.settings.czi_import) {
		return JSON.parse(JSON.stringify(proj.settings.czi_import));
	}
	return cziImport.buildDefaultCziImport("");
}

function collectSliceIdsFromIndex() {
	var index = project.readProjectFileIndex(orientState.bundleRoot);
	var byRole = (index && index.byRole) || {};
	var dapi = byRole.dapi || [];
	var ids = [];
	for (var i = 0; i < dapi.length; i++) {
		var stem = path.basename(String(dapi[i].name || dapi[i]), path.extname(String(dapi[i].name || dapi[i])));
		if (stem && ids.indexOf(stem) < 0) {
			ids.push(stem);
		}
	}
	ids.sort(function (a, b) {
		return cziImport.naturalCompare({ sliceId: a }, { sliceId: b });
	});
	return ids;
}

function ensureSlicePlan() {
	var cfg = orientState.cziImport;
	var orderIds = cziImport.collectSliceIds(cfg);
	var indexIds = collectSliceIdsFromIndex();
	var ids = orientSlicePlan.mergeOrientSliceIds(orderIds, indexIds);
	if (!ids.length) {
		return [];
	}
	if (!cfg.slice_order || !cfg.slice_order.length) {
		cfg.slice_order = ids.map(function (sid, idx) {
			return { ordinal: idx + 1, sliceId: sid, path: "", scene_index: 0 };
		});
	}
	cfg.geometry = orientGeometry.ensureGeometryMap(cfg.geometry || {}, ids);
	return ids;
}

function writeImportConfig(extra) {
	return geometryState.writeCziImportConfig(orientState.bundleRoot, orientState.cziImport, extra);
}

function persistGeometryToProject() {
	var proj = project.getProject();
	if (!proj) {
		return;
	}
	proj.settings = proj.settings || {};
	proj.settings.czi_import = orientState.cziImport;
	project.saveProjectJson();
}

function previewPathForSlice(sliceId) {
	return cziImport.resolveOrientPreviewPath(
		orientState.bundleRoot,
		orientState.cziImport,
		null,
		sliceId,
		orientState.displayChannel,
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
			orientState.bundleRoot,
			orientState.cziImport,
			scope,
		);
	} else {
		channels = cziImport.listOrientDisplayChannels(
			orientState.bundleRoot,
			orientState.cziImport,
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
		return ch.key === orientState.displayChannel && ch.enabled !== false;
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
			orientState.displayChannel = firstEnabled;
		} else {
			orientState.displayChannel = cziImport.ORIENT_DISPLAY_DAPI;
		}
	}
	select.value = orientState.displayChannel;
}

function getGeometryApplyState(ids) {
	return geometryState.assessGeometryApplyState(orientState.bundleRoot, orientState.cziImport, {
		sliceIds: ids,
		previewHealth: cziImport.assessOrientPreviewHealth(orientState.bundleRoot, orientState.cziImport),
	});
}

function updateOrientPreviewBanner() {
	var health = cziImport.assessOrientPreviewHealth(
		orientState.bundleRoot,
		orientState.cziImport,
	);
	var banner = qs("orientPreviewBanner");
	var geomBanner = qs("orientGeometryBanner");
	var repairBtn = qs("orientRepairPreviews");
	var rebuildLink = qs("orientRebuildGeometry");
	var finalizeBtn = qs("orientFinalizeGeometry");
	var applyBtn = qs("orientApply");
	var status = qs("orientStatus");
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
	var ids = orientGridSliceIds();
	var geoState = getGeometryApplyState(ids);
	var geoMsg = geometryState.geometryStateBannerText(geoState, health);
	if (geomBanner) {
		if (geoMsg && !msg) {
			geomBanner.textContent = geoMsg;
			geomBanner.classList.remove("d-none");
		} else if (
			geoMsg &&
			(geoState.policyState === "interrupted" ||
				geoState.policyState === "reextract_partial")
		) {
			geomBanner.textContent = geoMsg;
			geomBanner.classList.remove("d-none");
		} else {
			geomBanner.textContent = "";
			geomBanner.classList.add("d-none");
		}
	}
	if (repairBtn) {
		repairBtn.classList.toggle("d-none", !health.canApply || !health.needsRepair);
		repairBtn.disabled = previewRepairRunning || !geoState.allowPreviewRepair;
	}
	var reimportLink = qs("orientReimportCzi");
	if (reimportLink) {
		var showReimport =
			health.blankDapi &&
			health.blankDapi.length > 0 &&
			orientState.cziImport &&
			orientState.cziImport.files;
		reimportLink.classList.toggle("d-none", !showReimport);
		if (showReimport) {
			var sliceParam = health.blankDapi
				.map(function (b) {
					return encodeURIComponent(b.slice_id);
				})
				.join(",");
			reimportLink.href = "./czi_wizard.html?flow=reextract&slices=" + sliceParam;
		}
	}
	if (rebuildLink) {
		rebuildLink.classList.toggle(
			"d-none",
			geoState.policyState !== "interrupted" && geoState.policyState !== "finalize_pending",
		);
	}
	if (finalizeBtn) {
		finalizeBtn.classList.toggle("d-none", !geoState.canFinalizeOnly);
	}
	// Cache the disk-derived state so per-click geometry tweaks can refresh the
	// Apply button / status without re-auditing the NAS (see
	// updateOrientApplyControlsLight).
	orientState._lastHealth = health;
	orientState._lastGeoState = geoState;
	applyOrientControlsFromState(health, geoState);
	return health;
}

// In-memory-only refresh of the Apply button, status text, and summary from a
// (cached) health/geoState. No filesystem access — safe to call on every
// rotate/flip click.
function applyOrientControlsFromState(health, geoState) {
	var ids = orientGridSliceIds();
	var applyBtn = qs("orientApply");
	var status = qs("orientStatus");
	var pending = orientGeometry.countNonIdentityGeometry(orientState.cziImport.geometry, ids);
	var pendingForApply = geoState.partialReextractApply
		? geoState.pendingInScope
		: pending;
	if (applyBtn && !geometryRunning) {
		applyBtn.disabled = !geoState.allowApply || !health.canApply || pendingForApply === 0;
	}
	if (status && !geometryRunning) {
		if (!health.canApply) {
			/* repair banner carries the message */
		} else if (geoState.policyState === "interrupted" && !geoState.partialReextractApply) {
			status.textContent = "Apply is disabled — use Check Orientation Consistency to audit and repair.";
		} else if (geoState.policyState === "reextract_partial") {
			status.textContent =
				"Preview shows how each re-imported channel will look after Apply geometry.";
		} else if (pending > 0 && !orientState.cziImport.geometry_applied_at) {
			status.textContent =
				"CSS preview — adjust slices, then Apply geometry. If a prior apply crashed, use Check Orientation Consistency first.";
		} else if (pending === 0) {
			status.textContent = orientState.cziImport.geometry_applied_at
				? "No pending changes. Tiles show on-disk orientation; adjust a slice and Apply again for further changes."
				: "No pending geometry changes.";
		} else {
			status.textContent = "CSS preview — adjust slices, then Apply geometry to write files.";
		}
	}
	updateOrientApplySummary();
}

// Lightweight per-click updater: reuse the last NAS audit (health/geoState)
// and only recompute the in-memory pending count. Falls back to a full refresh
// if the cache isn't warm yet.
function updateOrientApplyControlsLight() {
	if (!orientState._lastHealth || !orientState._lastGeoState) {
		updateOrientPreviewBanner();
		return;
	}
	applyOrientControlsFromState(orientState._lastHealth, orientState._lastGeoState);
}

function runPreviewRepair() {
	if (previewRepairRunning || geometryRunning) {
		return Promise.reject(new Error("Another job is running"));
	}
	var health = updateOrientPreviewBanner();
	var audit = health.audit;
	var targets = cziImport.buildRepairTargetsFromAudit(audit, orientState.cziImport);
	previewRepairRunning = true;
	cziImport.ensureOrientDapiPreviewsFromPipeline(orientState.bundleRoot);
	var repairBtn = qs("orientRepairPreviews");
	if (repairBtn) {
		repairBtn.disabled = true;
	}
	setActivity("Repairing previews…", 5);
	verboseLog("Starting preview repair (migrate + " + targets.length + " target(s))…");

	var payload = Object.assign({}, orientState.cziImport);
	payload.repair_mode = "previews";
	payload.repair_targets = targets;
	payload.config_fingerprint = cziImport.cziImportFingerprint(payload);
	var cfgPath = cziImport.importConfigPath(orientState.bundleRoot);
	fs.mkdirSync(path.dirname(cfgPath), { recursive: true });
	fs.writeFileSync(cfgPath, JSON.stringify({ czi_import: payload }, null, 2), "utf8");

	return new Promise(function (resolve, reject) {
		function onProgress(ev, data) {
			var pct = Number(data[0]) || 0;
			setActivity(String(data[1] || "Repairing previews…"), pct);
		}
		function onJobLog(ev, line) {
			var msg = String(line || "").trim();
			if (msg) {
				verboseLog(msg.replace(/^LOG:\s*/i, ""));
			}
		}
		function onResult(ev, result) {
			ipc.removeListener("updateLoad", onProgress);
			ipc.removeListener("cziJobLog", onJobLog);
			ipc.removeListener("cziImportResult", onResult);
			previewRepairRunning = false;
			if (repairBtn) {
				repairBtn.disabled = false;
			}
			if (!result || result.ok === false) {
				var errMsg = (result && result.error) || "Preview repair failed";
				setActivity(errMsg, 0);
				reject(new Error(errMsg));
				return;
			}
			if (result.preview_format_version) {
				orientState.cziImport.preview_format_version = result.preview_format_version;
			}
			persistGeometryToProject();
			updateOrientPreviewBanner();
			renderOrientationGrid();
			setActivity("Preview repair complete.", 100);
			resolve(result);
		}
		ipc.on("updateLoad", onProgress);
		ipc.on("cziJobLog", onJobLog);
		ipc.once("cziImportResult", onResult);
		ipc.send("runCziImport", [
			String(orientState.bundleRoot || "").trim(),
			String(cfgPath || "").trim(),
		]);
	});
}

// Update the "Check Orientation Consistency" link to carry the checked slices
// as a ?slices= scope (URL-based: stateless, no address bar in the app, and
// well under any length limit for a project's slice count).
function updateConsistencyButton() {
	var link = document.getElementById("orientCheckGeometry");
	if (!link) {
		return;
	}
	var ids = Object.keys(orientState.consistencySelection || {});
	if (ids.length) {
		link.href =
			"./geometry_repair_wizard.html?slices=" +
			encodeURIComponent(ids.join(","));
		link.textContent = "Check Orientation Consistency (" + ids.length + ")";
	} else {
		link.href = "./geometry_repair_wizard.html";
		link.textContent = "Check Orientation Consistency";
	}
}

function renderOrientationGrid() {
	var grid = qs("orientGrid");
	if (!grid) {
		return;
	}
	if (!grid._consistencyWired) {
		grid._consistencyWired = true;
		grid.addEventListener("change", function (ev) {
			var t = ev.target;
			if (!t || !t.classList || !t.classList.contains("czi-consistency-check")) {
				return;
			}
			var tileEl = t.closest ? t.closest("[data-slice-id]") : null;
			var sid = tileEl ? tileEl.getAttribute("data-slice-id") : null;
			if (!sid) {
				return;
			}
			if (t.checked) {
				orientState.consistencySelection[sid] = true;
			} else {
				delete orientState.consistencySelection[sid];
			}
			updateConsistencyButton();
		});
	}
	var ids = orientGridSliceIds();
	grid.innerHTML = "";
	for (var i = 0; i < ids.length; i++) {
		var sliceId = ids[i];
		var geom = orientState.cziImport.geometry[sliceId];
		var tile = document.createElement("div");
		tile.className = "czi-orient-tile";
		tile.setAttribute("data-slice-id", sliceId);
		var titleEl = document.createElement("strong");
		titleEl.textContent = sliceId;
		tile.appendChild(titleEl);
		var imgPath = previewPathForSlice(sliceId);
		var imgSrc = fileUrlForPath(imgPath);
		if (imgSrc) {
			var viewport = document.createElement("div");
			viewport.className = "czi-orient-tile-viewport";
			if (shouldApplyCssPreviewForTile(sliceId, geom)) {
				viewport.style.transform = orientGeometry.geometryCssTransform(geom);
				viewport.style.transformOrigin = "center center";
			}
			var img = document.createElement("img");
			img.src = imgSrc;
			img.alt = sliceId;
			img.onerror = function () {
				var msg = document.createElement("p");
				msg.className = "small text-muted";
				msg.textContent = "No preview";
				if (viewport.parentNode) {
					viewport.parentNode.replaceChild(msg, viewport);
				}
			};
			viewport.appendChild(img);
			tile.appendChild(viewport);
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
		// Once geometry has been applied, a raw re-Apply is blocked. Offer a
		// per-slice checkbox to send this slice to the scoped Check Orientation
		// Consistency audit instead. Selection is read via the tile's
		// data-slice-id (delegated listener), so no per-iteration closures.
		if (orientState.cziImport && orientState.cziImport.geometry_applied_at) {
			var checkWrap = document.createElement("div");
			checkWrap.className = "form-check form-check-sm small mt-1";
			var cb = document.createElement("input");
			cb.type = "checkbox";
			cb.className = "form-check-input czi-consistency-check";
			cb.id = "consistency-" + sliceId;
			cb.checked = !!orientState.consistencySelection[sliceId];
			var lbl = document.createElement("label");
			lbl.className = "form-check-label";
			lbl.setAttribute("for", cb.id);
			lbl.textContent = "Send to consistency check";
			checkWrap.appendChild(cb);
			checkWrap.appendChild(lbl);
			tile.appendChild(checkWrap);
		}
		grid.appendChild(tile);
	}
	updateConsistencyButton();

	orientGeometry.wireOrientationGridClicks(
		grid,
		function () {
			return orientState.cziImport.geometry;
		},
		function (sliceId) {
			var ids = ensureSlicePlan();
			var copyAll = qs("orientApplyAll");
			if (copyAll && copyAll.checked && ids.length && sliceId === ids[0]) {
				orientGeometry.copyFirstTileGeometryToAll(
					orientState.cziImport.geometry,
					ids,
					grid,
					isReextractPartialOrient()
						? function (targetSliceId, geom) {
								return shouldApplyCssPreviewForTile(targetSliceId, geom);
							}
						: null,
				);
			}
			// Per-click: refresh controls from cached audit (no NAS scan).
			updateOrientApplyControlsLight();
		},
		isReextractPartialOrient()
			? function (sliceId, geom) {
					return shouldApplyCssPreviewForTile(sliceId, geom);
				}
			: null,
	);
}

function finalizeGeometryAfterApply(payload) {
	var ids = ensureSlicePlan();
	var orphans = cziImport.findGeometryKeysWithoutPreviewFiles(
		orientState.bundleRoot,
		orientState.cziImport.geometry,
		ids,
	);
	if (orphans.length) {
		verboseLog(
			"WARNING: geometry for slice(s) without DAPI/_previews files: " + orphans.join(", "),
		);
	}
	geometryState.finalizeGeometryAfterApply(orientState.bundleRoot, orientState.cziImport, {
		sliceIds: ids,
		payload: payload,
		applySource: "orient",
	});
	persistGeometryToProject();
	updateOrientPreviewBanner();
	updateOrientApplySummary();
	renderOrientationGrid();
}

function runApplyGeometry() {
	if (geometryRunning) {
		return Promise.reject(new Error("Geometry apply already running"));
	}
	var ids = ensureSlicePlan();
	var geomCount = orientGeometry.countNonIdentityGeometry(orientState.cziImport.geometry, ids);
	if (geomCount === 0) {
		return Promise.reject(new Error("No pending geometry changes to apply."));
	}
	var geoState = getGeometryApplyState(ids);
	if (geoState.policyState === "interrupted" && !geoState.partialReextractApply) {
		return Promise.reject(
			new Error("Geometry apply is blocked — use Check Orientation Consistency to audit and repair."),
		);
	}
	if (
		geomCount > 0 &&
		orientState.cziImport.geometry_applied_at &&
		geoState.policyState === "healthy"
	) {
		if (
			!confirm(
				"Geometry was already applied to files. Apply again will rotate/flip current on-disk images. Continue?",
			)
		) {
			return Promise.reject(new Error("Apply cancelled."));
		}
	}
	geometryRunning = true;
	var applyBtn = qs("orientApply");
	if (applyBtn) {
		applyBtn.disabled = true;
	}
	var logEl = qs("orientLog");
	if (logEl) {
		logEl.textContent = "";
	}
	verboseLog("Bundle: " + orientState.bundleRoot);
	verboseLog("Slices with rotation/flip: " + geomCount + " of " + ids.length);
	writeImportConfig({ apply_source: "orient" });
	persistGeometryToProject();
	setActivity("Applying geometry…", 2);

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
			setActivity(message || "Applying geometry…", rawPct);
		}
		function onJobLog(ev, line) {
			var msg = String(line || "").trim();
			if (!msg) {
				return;
			}
			if (/^\d+$/.test(msg) && fileTotal === 0) {
				fileTotal = Number(msg);
				verboseLog("Transforming " + fileTotal + " file(s)…");
				if (fileTotal >= LONG_JOB_FILE_THRESHOLD && !longJobWarned) {
					longJobWarned = true;
					verboseLog(
						"Large geometry job (" +
							fileTotal +
							" files) — this may take several minutes. See Application log for detail.",
					);
					setActivity(
						"Large job: transforming " + fileTotal + " files (see log)…",
						5,
					);
				}
				return;
			}
			verboseLog(msg.replace(/^LOG:\s*/i, ""));
		}
		function onResult(ev, payload) {
			ipc.removeListener("updateLoad", onProgress);
			ipc.removeListener("cziJobLog", onJobLog);
			ipc.removeListener("applyGeometryResult", onResult);
			geometryRunning = false;
			if (applyBtn) {
				applyBtn.disabled = false;
			}
			geometryState.persistLastApplyResult(orientState.bundleRoot, orientState.cziImport, payload || {});
			if (!payload || payload.ok === false) {
				var errMsg = (payload && payload.error) || "Geometry apply failed";
				if (payload && payload.error === "preflight_failed" && payload.failed_probes) {
					errMsg = "Preflight failed — fix unreadable files before applying";
					verboseLog("ERROR: " + errMsg);
					for (var fp = 0; fp < payload.failed_probes.length; fp++) {
						verboseLog("  " + payload.failed_probes[fp]);
					}
				} else if (payload && payload.failed && payload.failed.length) {
					errMsg += ": " + payload.failed.join("; ");
				}
				setActivity("Geometry apply failed: " + errMsg, 0);
				verboseLog("ERROR: " + errMsg);
				updateOrientPreviewBanner();
				reject(new Error(errMsg));
				return;
			}
			var summary =
				"Geometry applied — " +
				(payload.changed != null ? payload.changed : "?") +
				" file(s)";
			if (payload.files_total != null) {
				summary += " of " + payload.files_total;
			}
			if (payload.elapsed_sec != null) {
				summary += " in " + payload.elapsed_sec + "s";
			}
			if (payload.bytes_total != null) {
				summary += " (" + Math.round(payload.bytes_total / (1024 * 1024)) + " MB written)";
			}
			setActivity(summary, 100);
			verboseLog(summary);
			finalizeGeometryAfterApply(payload);
			setActivity(
				"Geometry saved to files. Tiles show on-disk orientation; adjust and Apply again for further changes.",
				100,
			);
			resolve(payload);
		}
		ipc.on("updateLoad", onProgress);
		ipc.on("cziJobLog", onJobLog);
		ipc.once("applyGeometryResult", onResult);
		var cfgPath = writeImportConfig({
			resume_apply: !!(geoState && geoState.canResume),
		});
		verboseLog("Config: " + cfgPath);
		setActivity("Starting Python geometry apply…", 5);
		ipc.send("runApplyGeometry", [
			String(orientState.bundleRoot || "").trim(),
			String(cfgPath || "").trim(),
		]);
	});
}

function init() {
	navTrail.renderTrail(
		[
			{ label: "Start", href: "./menu.html" },
			{ label: "Workspace", href: "./workspace_menu.html" },
			{ label: "Orient slices" },
		],
		"navTrail",
	);

	orientState.bundleRoot = project.getBundleRoot();
	if (!orientState.bundleRoot || !project.isActive()) {
		qs("orientMissing").classList.remove("d-none");
		return;
	}
	orientState.cziImport = loadCziImportConfig();
	var ids = ensureSlicePlan();
	if (!ids.length) {
		qs("orientMissing").classList.remove("d-none");
		qs("orientMissing").textContent =
			"No DAPI slice IDs found. Import CZI data or add orient PNGs under _previews (*_dapi.png), or run repair.";
		return;
	}
	qs("orientPanel").classList.remove("d-none");
	populateOrientDisplayChannelSelect();
	updateOrientPreviewBanner();
	renderOrientationGrid();

	var repairBtn = qs("orientRepairPreviews");
	if (repairBtn) {
		repairBtn.addEventListener("click", function () {
			runPreviewRepair().catch(function (err) {
				alert(String(err.message || err));
			});
		});
	}

	var displaySelect = qs("orientDisplayChannel");
	if (displaySelect) {
		displaySelect.addEventListener("change", function (ev) {
			orientState.displayChannel = ev.target.value;
			updateOrientPreviewBanner();
			renderOrientationGrid();
		});
	}

	qs("orientApplyAll").addEventListener("change", function (ev) {
		if (!ev.target.checked) {
			return;
		}
		var sliceIds = ensureSlicePlan();
		// Only the CSS transform changes — update each tile in place instead of
		// re-rendering the whole grid (which would reload every preview PNG from
		// the NAS).
		var grid = qs("orientGrid");
		var shouldApplyCss = isReextractPartialOrient()
			? function (sliceId, geom) {
					return shouldApplyCssPreviewForTile(sliceId, geom);
				}
			: null;
		orientGeometry.copyFirstTileGeometryToAll(
			orientState.cziImport.geometry, sliceIds, grid, shouldApplyCss,
		);
		updateOrientApplyControlsLight();
	});

	qs("orientApply").addEventListener("click", function () {
		runApplyGeometry().catch(function (err) {
			alert(String(err.message || err));
		});
	});

	var finalizeBtn = qs("orientFinalizeGeometry");
	if (finalizeBtn) {
		finalizeBtn.addEventListener("click", function () {
			var sliceIds = ensureSlicePlan();
			finalizeGeometryAfterApply({ ok: true, files_total: 0, changed: 0, finalize_only: true });
			updateOrientPreviewBanner();
			setActivity("Geometry settings finalized (no file changes).", 100);
			verboseLog("Finalize only — pending geometry reset in project JSON.");
		});
	}
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	projectIndexBusy.populatePage(
		function () {
			project.tryRestoreActiveProject();
			pipelineGate.assertPipelineAccess();
			init();
		},
		// Orient reads slice IDs from the cached index (+ config slice_order);
		// don't block the page on a full recursive NAS re-index (heavy right
		// after a repair). The refresh still runs in the background.
		{ waitForIndex: false },
	);
});
