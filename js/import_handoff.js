"use strict";

var fs = require("fs");
var path = require("path");
var perfLog = require("./perf_log");
var cziImport = require("./czi_import");
var pipelineRuns = require("./pipeline_runs");

var PNG_RE = /\.png$/i;
var DISMISS_KEY = "masonjar.importHandoffDismissed";

function countPngFiles(dir) {
	if (!dir || !fs.existsSync(dir)) {
		return 0;
	}
	var count = 0;
	try {
		var names = fs.readdirSync(dir);
		for (var i = 0; i < names.length; i++) {
			if (PNG_RE.test(names[i])) {
				count++;
			}
		}
	} catch (err) {}
	return count;
}

function _readJsonSafe(p) {
	try {
		return JSON.parse(fs.readFileSync(p, "utf8"));
	} catch (_e) {
		return null;
	}
}

// PNG count with a persistent, directory-mtime-gated cache. readdirSync over a
// large previews/DAPI folder on NAS can take minutes; the count only changes
// when the directory changes, so we cache it keyed by the dir's mtime in a
// small JSON sidecar. A cache file survives page navigations (unlike in-memory
// state, which resets when the renderer reloads), so the hub stays fast.
function countPngFilesCached(dir, cacheFilePath) {
	if (!dir || !fs.existsSync(dir)) {
		return 0;
	}
	var mtimeMs = -1;
	try {
		mtimeMs = fs.statSync(dir).mtimeMs;
	} catch (_e) {}
	var cache = cacheFilePath ? _readJsonSafe(cacheFilePath) || {} : {};
	var entry = cache[dir];
	if (entry && entry.mtimeMs === mtimeMs && typeof entry.count === "number") {
		return entry.count;
	}
	var count = countPngFiles(dir);
	if (cacheFilePath) {
		cache[dir] = { mtimeMs: mtimeMs, count: count };
		try {
			fs.mkdirSync(path.dirname(cacheFilePath), { recursive: true });
			fs.writeFileSync(cacheFilePath, JSON.stringify(cache), "utf8");
		} catch (_e) {}
	}
	return count;
}

function _importCountsCachePath(bundleRoot) {
	try {
		var project = require("./project");
		if (project && typeof project.metaDirPath === "function") {
			return path.join(project.metaDirPath(bundleRoot), "import_counts.json");
		}
	} catch (_e) {}
	return null;
}

function maxRunFromCziImport(cziSettings, activeMaxRel) {
	if (!cziSettings || !activeMaxRel) {
		return false;
	}
	var maxRuns = cziSettings.max_runs || {};
	var keys = Object.keys(maxRuns);
	for (var i = 0; i < keys.length; i++) {
		if (maxRuns[keys[i]] === activeMaxRel) {
			return true;
		}
	}
	return false;
}

function maxRunFromManifest(bundleRoot, activeMaxRel) {
	if (!bundleRoot || !activeMaxRel) {
		return false;
	}
	var manifestPath = path.join(
		bundleRoot,
		"data/counting/03_max",
		activeMaxRel,
		"run_manifest.json",
	);
	if (!fs.existsSync(manifestPath)) {
		return false;
	}
	try {
		var raw = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
		return raw && raw.source === "czi_import";
	} catch (err) {
		return false;
	}
}

function hasAlignmentOutputs(bundleRoot, projectJson) {
	var activeRel = "";
	if (projectJson && projectJson.processing && projectJson.processing.active_runs) {
		activeRel = projectJson.processing.active_runs.slices || "";
	}
	if (!activeRel) {
		activeRel = pipelineRuns.getActiveRunRelForRole("slices");
	}
	activeRel = String(activeRel || "").replace(/\\/g, "/");
	if (!activeRel) {
		return false;
	}
	var roles = (projectJson && projectJson.roles) || {};
	var slicesBase = roles.slices
		? path.isAbsolute(roles.slices)
			? roles.slices
			: path.join(bundleRoot, roles.slices)
		: path.join(bundleRoot, "data/counting/01_slices");
	var leaf = path.join(slicesBase, activeRel.split("/").join(path.sep));
	if (!leaf || !fs.existsSync(leaf)) {
		return false;
	}
	try {
		var names = fs.readdirSync(leaf);
		for (var i = 0; i < names.length; i++) {
			if (/^Annotation_/i.test(names[i]) && /\.pkl$/i.test(names[i])) {
				return true;
			}
		}
	} catch (err) {}
	return false;
}

function dismissStorageKey(bundleRoot) {
	return DISMISS_KEY + ":" + String(bundleRoot || "");
}

function isHandoffDismissed(bundleRoot) {
	try {
		return localStorage.getItem(dismissStorageKey(bundleRoot)) === "1";
	} catch (err) {
		return false;
	}
}

function dismissHandoff(bundleRoot) {
	try {
		localStorage.setItem(dismissStorageKey(bundleRoot), "1");
	} catch (err) {}
}

// Short-lived memo so the several getImportHandoffState() calls in one hub
// render pass (shouldShowImportNextSteps + the banner itself) don't each repeat
// the expensive per-slice audit / NAS checks. Cleared on the next microtask, so
// it never returns state that is stale beyond the current synchronous burst.
var _handoffMemoBundle = null;
var _handoffMemoState = null;

function getImportHandoffState(bundleRoot, projectJson) {
	if (
		bundleRoot &&
		_handoffMemoBundle === bundleRoot &&
		_handoffMemoState
	) {
		return _handoffMemoState;
	}
	projectJson = projectJson || {};
	var cziSettings = (projectJson.settings && projectJson.settings.czi_import) || null;
	var state = {
		complete: false,
		fromCziImport: false,
		dapiCount: 0,
		previewCount: 0,
		maxRunLabel: "",
		maxRunRel: "",
		needsAlignment: true,
		geometryAppliedAt: "",
		sliceCount: 0,
	};
	if (!bundleRoot || !cziSettings) {
		return state;
	}
	state.fromCziImport = true;
	state.geometryAppliedAt = cziSettings.geometry_applied_at || "";
	var dapiDir = path.join(bundleRoot, "data/counting/00_dapi");
	var previewDir = path.join(bundleRoot, cziImport.PREVIEWS_REL);
	var countsCache = _importCountsCachePath(bundleRoot);
	perfLog.perfSection("handoff.counts", function () {
		state.dapiCount = countPngFilesCached(dapiDir, countsCache);
		state.previewCount = countPngFilesCached(previewDir, countsCache);
	});
	state.maxRunRel = pipelineRuns.getActiveRunRelForRole("max") || "";
	if (!state.maxRunRel) {
		state.maxRunRel = cziImport.primaryMaxRunRel(cziSettings, null) || "";
	}
	state.maxRunLabel = state.maxRunRel || "";
	// Only missingMaxRuns is needed here (for state.complete). Use the light
	// audit to avoid the full per-slice zstack/preview integrity scan, which
	// costs minutes on NAS and is irrelevant to this banner.
	var audit = perfLog.perfSection("handoff.audit", function () {
		return cziImport.auditMaxRunsOnly(bundleRoot, cziSettings, {
			importResult: { max_runs: cziSettings.max_runs || {} },
		});
	});
	state.complete =
		state.dapiCount > 0 &&
		state.maxRunRel !== "" &&
		audit.missingMaxRuns.length === 0;
	var sliceIds = cziImport.collectSliceIds(cziSettings);
	state.sliceCount = sliceIds.length;
	state.needsAlignment = perfLog.perfSection(
		"handoff.hasAlignmentOutputs",
		function () {
			return !hasAlignmentOutputs(bundleRoot, projectJson);
		},
	);
	// Memoize for the current synchronous render burst only.
	_handoffMemoBundle = bundleRoot;
	_handoffMemoState = state;
	try {
		Promise.resolve().then(function () {
			_handoffMemoBundle = null;
			_handoffMemoState = null;
		});
	} catch (_e) {
		setTimeout(function () {
			_handoffMemoBundle = null;
			_handoffMemoState = null;
		}, 0);
	}
	return state;
}

function shouldShowImportNextSteps(projectJson, fileIndex, bundleRoot) {
	if (!projectJson || !bundleRoot) {
		return false;
	}
	if (isHandoffDismissed(bundleRoot)) {
		return false;
	}
	var state = getImportHandoffState(bundleRoot, projectJson);
	if (!state.complete || !state.needsAlignment) {
		return false;
	}
	return true;
}

function isMaxFromCziImport(bundleRoot, projectJson) {
	projectJson = projectJson || {};
	var cziSettings = (projectJson.settings && projectJson.settings.czi_import) || null;
	if (!cziSettings) {
		return false;
	}
	var activeRel = pipelineRuns.getActiveRunRelForRole("max");
	if (!activeRel) {
		return false;
	}
	return (
		maxRunFromCziImport(cziSettings, activeRel) ||
		maxRunFromManifest(bundleRoot, activeRel)
	);
}

function formatChoiceLabel(role, rel, projectJson, bundleRoot) {
	var label = rel || "(flat)";
	if (role === "max" && isMaxFromCziImport(bundleRoot, projectJson)) {
		var active = pipelineRuns.getActiveRunRelForRole("max");
		if (active === rel) {
			label += " (from CZI import)";
		}
	}
	if (role === "max" && /\/basic\//.test(String(rel || "").replace(/\\/g, "/"))) {
		label += " (BaSiC shading)";
	}
	return label;
}

module.exports = {
	DISMISS_KEY: DISMISS_KEY,
	countPngFiles: countPngFiles,
	getImportHandoffState: getImportHandoffState,
	shouldShowImportNextSteps: shouldShowImportNextSteps,
	isMaxFromCziImport: isMaxFromCziImport,
	isHandoffDismissed: isHandoffDismissed,
	dismissHandoff: dismissHandoff,
	formatChoiceLabel: formatChoiceLabel,
	hasAlignmentOutputs: hasAlignmentOutputs,
};
