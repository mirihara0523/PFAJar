"use strict";

var fs = require("fs");
var path = require("path");
var perfLog = require("./perf_log");
var branding = require("./branding");
var dialogs = require("./dialogs");
var fileIndex = require("./file_index");
var pipelineRuns = require("./pipeline_runs");

var ipcRenderer = null;
try {
	ipcRenderer = require("electron").ipcRenderer;
} catch (_err) {
	ipcRenderer = null;
}

function beginProjectIndexFairshare() {
	if (!ipcRenderer || typeof ipcRenderer.sendSync !== "function") {
		return;
	}
	try {
		ipcRenderer.sendSync("beginProjectIndexIo");
	} catch (_err) {
		// Fairshare is best-effort; index refresh must still proceed.
	}
}

function endProjectIndexFairshare() {
	if (!ipcRenderer || typeof ipcRenderer.sendSync !== "function") {
		return;
	}
	try {
		ipcRenderer.sendSync("endProjectIndexIo");
	} catch (_err) {
		// best effort
	}
}

var PROJECT_FILENAME = branding.PROJECT_FILENAME;
var META_DIR = branding.META_DIR;
var RECENT_KEY = branding.RECENT_KEY;
var ACTIVE_KEY = branding.ACTIVE_KEY;

var CANONICAL_ROLES = {
	original_scans: "data/original_scans",
	dapi: "data/counting/00_dapi",
	slices: "data/counting/01_slices",
	max: "data/counting/03_max",
	predictions: "data/counting/05_predictions",
	quantification: "data/counting/06_quantification",
	pkls: "data/counting/07_pkls",
	dual: "data/counting/08_dual",
};

/** workspace logical keys → project role keys */
var LOGICAL_TO_ROLE = {
	originalScans: "original_scans",
	dapi: "dapi",
	slices: "slices",
	max: "max",
	predictions: "predictions",
	quantification: "quantification",
	pkls: "pkls",
	dual: "dual",
	brainRoot: null,
	countingRoot: null,
};

var IMAGE_EXT_RE = /\.(tif|tiff|png|jpe?g)$/i;

var state = {
	active: false,
	bundleRoot: "",
	project: null,
	projectFilename: PROJECT_FILENAME,
	metaDirName: META_DIR,
	geometryWorkspaceBanner: null,
};

function nowIso() {
	return new Date().toISOString();
}

/** Safe slug for bundle dir and project file (e.g. M528 → M528.masonjar in M528_masonjar/). */
function sanitizeProjectSlug(name) {
	var s = String(name || "").trim();
	if (s.toLowerCase().endsWith(branding.BUNDLE_SUFFIX)) {
		s = s.slice(0, -branding.BUNDLE_SUFFIX.length);
	}
	if (s.toLowerCase().endsWith(branding.LEGACY_BUNDLE_SUFFIX)) {
		s = s.slice(0, -branding.LEGACY_BUNDLE_SUFFIX.length);
	}
	if (/_masonjar$/i.test(s)) {
		s = s.replace(/_masonjar$/i, "");
	}
	s = s.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/\s+/g, "_");
	s = s.replace(/^_+|_+$/g, "");
	if (!s) {
		s = "Project";
	}
	return s;
}

function bundleDirNameForSlug(slug) {
	return sanitizeProjectSlug(slug) + "_masonjar";
}

function projectFilenameForSlug(slug) {
	return sanitizeProjectSlug(slug) + ".masonjar";
}

/**
 * New bundle layout: parent/M528_masonjar/ with M528.masonjar + data/ inside.
 */
function resolveNewBundlePath(parentDir, projectName) {
	var slug = sanitizeProjectSlug(projectName);
	var displayName =
		String(projectName || "")
			.trim()
			.replace(/\.(masonjar|belljar)$/i, "") || slug;
	var bundleRoot = path.join(path.resolve(parentDir), bundleDirNameForSlug(slug));
	return {
		slug: slug,
		name: displayName,
		bundleRoot: bundleRoot,
		projectFilename: projectFilenameForSlug(slug),
		bundleDirName: bundleDirNameForSlug(slug),
	};
}

function findProjectFilename(bundleRoot) {
	if (!bundleRoot || !fs.existsSync(bundleRoot)) {
		return PROJECT_FILENAME;
	}
	var entries;
	try {
		entries = fs.readdirSync(bundleRoot, { withFileTypes: true });
	} catch (err) {
		return PROJECT_FILENAME;
	}
	var namedMasonjar = [];
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isFile()) {
			continue;
		}
		var n = entries[i].name;
		if (/\.masonjar$/i.test(n)) {
			namedMasonjar.push(n);
		}
	}
	if (namedMasonjar.length === 1) {
		return namedMasonjar[0];
	}
	if (namedMasonjar.length > 1) {
		var folderSlug = sanitizeProjectSlug(
			path.basename(bundleRoot).replace(/_masonjar$/i, ""),
		);
		var expected = projectFilenameForSlug(folderSlug);
		if (namedMasonjar.indexOf(expected) >= 0) {
			return expected;
		}
		namedMasonjar.sort();
		return namedMasonjar[0];
	}
	for (var j = 0; j < branding.PROJECT_FILENAMES.length; j++) {
		var legacy = branding.PROJECT_FILENAMES[j];
		if (fs.existsSync(path.join(bundleRoot, legacy))) {
			return legacy;
		}
	}
	return PROJECT_FILENAME;
}

function findMetaDirName(bundleRoot) {
	for (var i = 0; i < branding.META_DIRS.length; i++) {
		var name = branding.META_DIRS[i];
		if (fs.existsSync(path.join(bundleRoot, name))) {
			return name;
		}
	}
	return META_DIR;
}

function projectFilePath(bundleRoot) {
	return path.join(bundleRoot, state.projectFilename || findProjectFilename(bundleRoot));
}

function metaDir(bundleRoot) {
	return path.join(bundleRoot, state.metaDirName || findMetaDirName(bundleRoot));
}

function metaDirPath(bundleRoot) {
	return metaDir(bundleRoot || state.bundleRoot);
}

function isBundleRoot(dir) {
	if (!dir || !fs.existsSync(dir)) {
		return false;
	}
	var entries;
	try {
		entries = fs.readdirSync(dir, { withFileTypes: true });
	} catch (err) {
		return false;
	}
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isFile()) {
			continue;
		}
		var n = entries[i].name;
		if (/\.masonjar$/i.test(n)) {
			return true;
		}
		if (branding.PROJECT_FILENAMES.indexOf(n) >= 0) {
			return true;
		}
	}
	return false;
}

function ensureBundleLayout(bundleRoot) {
	fs.mkdirSync(metaDir(bundleRoot), { recursive: true });
	var keys = Object.keys(CANONICAL_ROLES);
	for (var i = 0; i < keys.length; i++) {
		var rel = CANONICAL_ROLES[keys[i]];
		fs.mkdirSync(path.join(bundleRoot, rel), { recursive: true });
	}
}

function resolveRolePath(role) {
	if (!state.active || !state.project || !state.project.roles) {
		return "";
	}
	var rel = state.project.roles[role];
	if (!rel) {
		return "";
	}
	if (path.isAbsolute(rel)) {
		return rel;
	}
	return path.join(state.bundleRoot, rel);
}

function resolveLogicalPath(logicalKey, options) {
	options = options || {};
	if (!logicalKey) {
		return "";
	}
	if (logicalKey === "brainRoot") {
		return state.bundleRoot || "";
	}
	if (logicalKey === "countingRoot") {
		return state.bundleRoot
			? path.join(state.bundleRoot, "data", "counting")
			: "";
	}
	var role = LOGICAL_TO_ROLE[logicalKey];
	if (role && state.active) {
		if (pipelineRuns.isOutputRole(role)) {
			if (options.purpose === "output") {
				return resolveRolePath(role) || "";
			}
			var leaf = pipelineRuns.resolveActiveRunLeafAbs(role);
			if (leaf) {
				return leaf;
			}
		}
		var resolved = resolveRolePath(role);
		if (resolved) {
			return resolved;
		}
	}
	return "";
}

function resolveLogicalPathForOutput(logicalKey) {
	return resolveLogicalPath(logicalKey, { purpose: "output" });
}

function resolveLogicalPathForInput(logicalKey) {
	return resolveLogicalPath(logicalKey, { purpose: "input" });
}

function readProjectJson(bundleRoot) {
	var filename = findProjectFilename(bundleRoot);
	var filePath = path.join(bundleRoot, filename);
	var raw = fs.readFileSync(filePath, "utf8");
	try {
		return JSON.parse(raw);
	} catch (parseErr) {
		// A corrupt/truncated project file must not crash the whole renderer.
		throw new Error(
			"Project file is corrupt or not valid JSON: " +
				filePath +
				" (" +
				(parseErr && parseErr.message ? parseErr.message : parseErr) +
				")",
		);
	}
}

function loadProjectJson(bundleRoot) {
	state.projectFilename = findProjectFilename(bundleRoot);
	state.metaDirName = findMetaDirName(bundleRoot);
	return readProjectJson(bundleRoot);
}

function saveProjectJson() {
	if (!state.active || !state.bundleRoot || !state.project) {
		return false;
	}
	state.project.modified = nowIso();
	if (!state.project.created) {
		state.project.created = state.project.modified;
	}
	var filePath = projectFilePath(state.bundleRoot);
	fs.writeFileSync(filePath, JSON.stringify(state.project, null, 2), "utf8");
	return true;
}

function syncWorkspaceFromProject(workspace) {
	if (!state.active || !workspace) {
		return;
	}
	var ws = workspace.getWorkspace ? workspace.getWorkspace() : null;
	if (!ws) {
		workspace.loadWorkspace();
		ws = workspace.getWorkspace();
	}
	ws.brainRoot = state.bundleRoot;
	ws.countingRoot = path.join(state.bundleRoot, "data", "counting");
	ws.originalScans = resolveRolePath("original_scans") || "";
	ws.paths = {};
	var roleKeys = ["dapi", "slices", "max", "predictions", "quantification", "pkls", "dual"];
	for (var i = 0; i < roleKeys.length; i++) {
		var k = roleKeys[i];
		ws.paths[k] = resolveRolePath(k) || "";
	}
	workspace.saveWorkspace();
}

function migrateLegacyRecentProjects() {
	var current = [];
	try {
		current = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
	} catch (err) {
		current = [];
	}
	if (current.length) {
		return;
	}
	try {
		var legacy = JSON.parse(localStorage.getItem(branding.LEGACY_RECENT_KEY) || "[]");
		if (legacy.length) {
			localStorage.setItem(RECENT_KEY, JSON.stringify(legacy));
		}
	} catch (err) {
		/* ignore */
	}
}

function addRecentProject(bundleRoot, name) {
	migrateLegacyRecentProjects();
	var list = [];
	try {
		list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
	} catch (err) {
		list = [];
	}
	list = list.filter(function (entry) {
		return entry.path !== bundleRoot;
	});
	list.unshift({
		path: bundleRoot,
		name: name || path.basename(bundleRoot),
		openedAt: nowIso(),
	});
	list = list.slice(0, 12);
	localStorage.setItem(RECENT_KEY, JSON.stringify(list));
}

function getRecentProjects() {
	migrateLegacyRecentProjects();
	try {
		return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
	} catch (err) {
		return [];
	}
}

function setActiveProject(bundleRoot, projectData) {
	state.active = true;
	state.bundleRoot = bundleRoot;
	state.project = projectData;
	state.projectFilename = findProjectFilename(bundleRoot);
	state.metaDirName = findMetaDirName(bundleRoot);
	localStorage.setItem(ACTIVE_KEY, bundleRoot);
	addRecentProject(bundleRoot, projectData.name);
	try {
		var workspace = require("./workspace");
		syncWorkspaceFromProject(workspace);
	} catch (err) {
		/* workspace optional at load */
	}
}

function clearActiveProject() {
	state.active = false;
	state.bundleRoot = "";
	state.project = null;
	state.projectFilename = PROJECT_FILENAME;
	state.metaDirName = META_DIR;
	state.geometryWorkspaceBanner = null;
	localStorage.removeItem(ACTIVE_KEY);
}

function openProject(bundleRoot) {
	bundleRoot = path.resolve(bundleRoot);
	if (!isBundleRoot(bundleRoot)) {
		throw new Error(
			"Not a " +
				branding.PRODUCT_NAME +
				" project: missing a .masonjar project file (e.g. M528.masonjar) or legacy project.belljar",
		);
	}
	perfLog.perfSection("open.ensureBundleLayout", function () {
		ensureBundleLayout(bundleRoot);
	});
	var data = perfLog.perfSection("open.loadProjectJson", function () {
		return loadProjectJson(bundleRoot);
	});
	if (!data.roles || Object.keys(data.roles).length === 0) {
		data.roles = Object.assign({}, CANONICAL_ROLES);
	}
	if (!data.layout) {
		data.layout = branding.LAYOUT_ID;
	}
	var activeRunsMigrated = false;
	if (!data.processing) {
		data.processing = defaultProcessing();
	} else {
		data.processing = Object.assign(defaultProcessing(), data.processing);
		var activeRunsBefore = JSON.stringify(data.processing.active_runs || {});
		data.processing.active_runs = pipelineRuns.migrateActiveRuns(data.processing);
		activeRunsMigrated = activeRunsBefore !== JSON.stringify(data.processing.active_runs);
		if (data.processing.active_prediction_run && !data.processing.active_runs.predictions) {
			data.processing.active_runs.predictions = String(
				data.processing.active_prediction_run,
			)
				.split(/[/\\]+/)
				.filter(Boolean)
				.join("/");
		}
		if (!data.processing.run_modes) {
			data.processing.run_modes = defaultProcessing().run_modes;
		} else {
			data.processing.run_modes = Object.assign(
				{},
				defaultProcessing().run_modes,
				data.processing.run_modes,
			);
		}
	}
	var orphanMigrate = perfLog.perfSection("open.migrateOrphanMaxFamilyLeaves", function () {
		return pipelineRuns.migrateOrphanMaxFamilyLeaves(
			bundleRoot,
			data.roles,
			data.processing,
			data.settings || {},
		);
	});
	if (orphanMigrate.messages && orphanMigrate.messages.length) {
		for (var mi = 0; mi < orphanMigrate.messages.length; mi++) {
			console.log(orphanMigrate.messages[mi]);
		}
	}
	if (orphanMigrate.changed) {
		data.processing.active_runs = orphanMigrate.active_runs;
		activeRunsMigrated = true;
	}
	var reconcile = perfLog.perfSection("open.reconcileProjectRunsOnOpen", function () {
		return pipelineRuns.reconcileProjectRunsOnOpen(
			bundleRoot,
			data.roles,
			data.processing,
		);
	});
	if (reconcile.changed) {
		data.processing.active_runs = reconcile.active_runs;
		activeRunsMigrated = true;
	}
	var geometryState = require("./geometry_state");
	var geometryReconcile = perfLog.perfSection("open.reconcileGeometryOnOpen", function () {
		return geometryState.reconcileGeometryOnOpen(bundleRoot, data);
	});
	if (geometryReconcile.changed) {
		activeRunsMigrated = true;
	}
	perfLog.perfSection("open.setActiveProject", function () {
		setActiveProject(bundleRoot, data);
	});
	state.geometryWorkspaceBanner = geometryReconcile.workspaceBanner || null;
	if (activeRunsMigrated) {
		perfLog.perfSection("open.saveProjectJson", function () {
			saveProjectJson();
		});
	}
	perfLog
		.perfSection("open.refreshProjectIndex.sync", function () {
			return refreshProjectIndex(bundleRoot);
		})
		.catch(function (err) {
			console.warn("[project] refreshProjectIndex on open:", err);
		});
	return state.project;
}

function defaultProcessing() {
	return {
		subset_enabled: false,
		slice_ids: [],
		active_prediction_run: "",
		active_runs: pipelineRuns.defaultActiveRuns(),
		run_modes: {
			align: "merge",
			intensity: "merge",
			count: "merge",
			detect: "merge",
			max: "merge",
			sharpen: "merge",
			basic: "merge",
		},
		step_failures: {
			align: {},
		},
	};
}

var processingStateListeners = [];

function addProcessingStateListener(fn) {
	if (typeof fn === "function") {
		processingStateListeners.push(fn);
	}
}

function notifyProcessingStateChanged() {
	for (var i = 0; i < processingStateListeners.length; i++) {
		try {
			processingStateListeners[i]();
		} catch (err) {
			console.warn("[project] processing state listener:", err);
		}
	}
}

function ensureStepFailures(processing) {
	if (!processing.step_failures) {
		processing.step_failures = defaultProcessing().step_failures;
	} else {
		processing.step_failures = Object.assign(
			{},
			defaultProcessing().step_failures,
			processing.step_failures,
		);
		if (!processing.step_failures.align) {
			processing.step_failures.align = {};
		}
	}
	return processing.step_failures;
}

function recordStepFailure(stepId, sliceId, detail) {
	if (!state.active || !state.project) {
		return;
	}
	if (!state.project.processing) {
		state.project.processing = defaultProcessing();
	}
	var failures = ensureStepFailures(state.project.processing);
	if (!failures[stepId]) {
		failures[stepId] = {};
	}
	failures[stepId][sliceId] = Object.assign(
		{
			message: "",
			file: "",
			at: nowIso(),
		},
		detail || {},
	);
	if (!failures[stepId][sliceId].message && detail && detail.error) {
		failures[stepId][sliceId].message = String(detail.error);
	}
	saveProjectJson();
	notifyProcessingStateChanged();
}

function clearStepFailure(stepId, sliceId) {
	if (!state.active || !state.project || !state.project.processing) {
		return;
	}
	var failures = ensureStepFailures(state.project.processing);
	if (!failures[stepId] || !failures[stepId][sliceId]) {
		return;
	}
	delete failures[stepId][sliceId];
	saveProjectJson();
	notifyProcessingStateChanged();
}

function getFailedSliceIds(stepId) {
	if (!state.project || !state.project.processing) {
		return [];
	}
	var failures = ensureStepFailures(state.project.processing);
	var stepMap = failures[stepId] || {};
	return Object.keys(stepMap);
}

function mergeAlignWarpReport(bundleRoot, alignLeafAbs) {
	if (!state.active || !alignLeafAbs) {
		return { recorded: 0, cleared: 0 };
	}
	bundleRoot = bundleRoot || state.bundleRoot;
	var reportPath = path.join(alignLeafAbs, ".masonjar", "align_warp_report.json");
	if (!fs.existsSync(reportPath)) {
		return { recorded: 0, cleared: 0 };
	}
	var report;
	try {
		report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
	} catch (err) {
		console.warn("[project] mergeAlignWarpReport:", err);
		return { recorded: 0, cleared: 0 };
	}
	var recorded = 0;
	var cleared = 0;
	var failed = report.warp_failed || [];
	for (var i = 0; i < failed.length; i++) {
		var entry = failed[i] || {};
		var sliceId = entry.slice_id || entry.sliceId;
		if (!sliceId) {
			continue;
		}
		recordStepFailure("align", sliceId, {
			message: entry.error || entry.message || "Alignment warp failed",
			file: entry.file || "",
			at: report.timestamp || nowIso(),
		});
		recorded += 1;
	}
	var ok = report.warp_ok || [];
	for (var j = 0; j < ok.length; j++) {
		var okId = ok[j];
		if (getFailedSliceIds("align").indexOf(okId) >= 0) {
			clearStepFailure("align", okId);
			cleared += 1;
		}
	}
	return { recorded: recorded, cleared: cleared };
}

function readProjectFileIndex(bundleRoot) {
	bundleRoot = bundleRoot || state.bundleRoot;
	if (!bundleRoot) {
		return null;
	}
	return fileIndex.readFileIndex(bundleRoot, metaDir(bundleRoot));
}

var _indexRefreshPromise = null;
var _indexRefreshBundleRoot = null;

function getIndexRefreshPromise() {
	return _indexRefreshPromise;
}

function isIndexRefreshing() {
	return !!_indexRefreshPromise;
}

function indexedFilesAreUnchanged(priorIndex, nextIndex) {
	if (
		!priorIndex ||
		!nextIndex ||
		!Array.isArray(priorIndex.files) ||
		!Array.isArray(nextIndex.files) ||
		priorIndex.files.length !== nextIndex.files.length
	) {
		return false;
	}
	var priorByKey = {};
	for (var i = 0; i < priorIndex.files.length; i++) {
		var prior = priorIndex.files[i];
		if (!prior || !prior.relPath || !prior.role) {
			return false;
		}
		priorByKey[prior.role + "\u0000" + prior.relPath] = prior;
	}
	for (var j = 0; j < nextIndex.files.length; j++) {
		var next = nextIndex.files[j];
		if (!next || !next.relPath || !next.role) {
			return false;
		}
		var cached = priorByKey[next.role + "\u0000" + next.relPath];
		if (
			!cached ||
			cached.size !== next.size ||
			cached.mtime !== next.mtime ||
			cached.sliceId !== next.sliceId
		) {
			return false;
		}
	}
	return true;
}

function refreshProjectIndex(bundleRoot, options) {
	options = options || {};
	bundleRoot = bundleRoot || state.bundleRoot;
	if (!bundleRoot) {
		return Promise.reject(new Error("no bundle root"));
	}
	if (
		_indexRefreshPromise &&
		_indexRefreshBundleRoot === bundleRoot &&
		!options.force
	) {
		return _indexRefreshPromise;
	}
	var roles;
	if (state.project && state.bundleRoot === bundleRoot) {
		roles = state.project.roles || CANONICAL_ROLES;
	} else {
		try {
			roles = loadProjectJson(bundleRoot).roles || CANONICAL_ROLES;
		} catch (err) {
			roles = CANONICAL_ROLES;
		}
	}
	var onProgress = options.onProgress;
	beginProjectIndexFairshare();
	var priorIndex = null;
	try {
		priorIndex = fileIndex.readFileIndex(bundleRoot, metaDir(bundleRoot));
	} catch (err) {
		priorIndex = null;
	}
	var promise = fileIndex
		.buildFileIndex(bundleRoot, roles, {
			appRoot: options.appRoot,
			priorIndex: priorIndex,
			activeRuns:
				(state.project &&
					state.project.processing &&
					state.project.processing.active_runs) ||
				pipelineRuns.migrateActiveRuns(
					state.project && state.project.processing
						? state.project.processing
						: null,
				),
		})
		.then(function (index) {
			if (typeof onProgress === "function") {
				onProgress(80, "Writing file index…");
			}
			// This .then runs on the main thread AFTER the page has painted, so
			// heavy work here (notably writeRunsCatalog's recursive NAS walk)
			// freezes an already-visible page. Timed to expose that cost.
			var _idxT0 = perfLog.now();
			var mdir = metaDir(bundleRoot);
			var cachedArtifactsUsable =
				!options.force &&
				indexedFilesAreUnchanged(priorIndex, index) &&
				fs.existsSync(path.join(mdir, "runs_catalog.json")) &&
				fs.existsSync(path.join(mdir, "manifest.json"));
			if (cachedArtifactsUsable) {
				// Page-to-page opens often see exactly the same file set. Reusing
				// these persisted artifacts avoids the recursive output-run scan,
				// which is the main source of navigation stalls on shared storage.
				perfLog.perfLog("refreshIndex.reuseCachedArtifacts", perfLog.now() - _idxT0);
			} else {
				perfLog.perfSection("refreshIndex.writeFileIndex", function () {
					fileIndex.writeFileIndex(bundleRoot, mdir, index);
				});
				perfLog.perfSection("refreshIndex.writeRunsCatalog", function () {
					pipelineRuns.writeRunsCatalog(bundleRoot, mdir, roles);
				});
			}
			var activeRuns =
				(state.project &&
					state.project.processing &&
					state.project.processing.active_runs) ||
				pipelineRuns.defaultActiveRuns();
			var report = perfLog.perfSection("refreshIndex.computeMatchReport", function () {
				return fileIndex.computeMatchReport(index, fileIndex.INPUT_MATCH_ROLES, {
					activeRuns: activeRuns,
					bundleRoot: bundleRoot,
					roles: roles,
				});
			});
			var manifestPath = path.join(mdir, "manifest.json");
			if (!cachedArtifactsUsable) {
				var manifestV2 = fileIndex.buildManifestV2(bundleRoot, index, report);
				fs.writeFileSync(manifestPath, JSON.stringify(manifestV2, null, 2), "utf8");
			}
			perfLog.perfLog("refreshIndex.then_total", perfLog.now() - _idxT0);
			if (typeof onProgress === "function") {
				onProgress(100, "Index complete (" + report.matchedSliceIds.length + " matched)");
			}
			return { index: index, report: report, manifestPath: manifestPath };
		})
		.finally(function () {
			if (_indexRefreshPromise === promise) {
				endProjectIndexFairshare();
				_indexRefreshPromise = null;
				_indexRefreshBundleRoot = null;
			}
		});
	_indexRefreshPromise = promise;
	_indexRefreshBundleRoot = bundleRoot;
	return promise;
}

function tryRestoreActiveProject() {
	migrateLegacyRecentProjects();
	var saved = localStorage.getItem(ACTIVE_KEY);
	if (!saved) {
		saved = localStorage.getItem(branding.LEGACY_ACTIVE_KEY);
	}
	if (!saved || !isBundleRoot(saved)) {
		return false;
	}
	try {
		// Every page calls this during its initialization.  Once this renderer
		// already owns the saved bundle, reopening it would repeat layout repair,
		// run reconciliation, and the recursive runs-catalog walk.  Output-producing
		// actions use their explicit refresh paths, so a page change can safely reuse
		// the active in-memory project.
		if (state.active && path.resolve(state.bundleRoot) === path.resolve(saved)) {
			perfLog.perfLog("open.reuseActiveProject", 0);
			return true;
		}
		openProject(saved);
		return true;
	} catch (err) {
		clearActiveProject();
		return false;
	}
}

function createProject(options) {
	options = options || {};
	var bundleRoot = path.resolve(options.bundleRoot);
	var name = options.name || path.basename(bundleRoot);
	var slug = options.projectSlug || sanitizeProjectSlug(name);
	var referenceOnly = !!options.referenceOnly;
	var roles = options.roles || Object.assign({}, CANONICAL_ROLES);
	var sources = options.sources || {};
	var now = nowIso();

	state.projectFilename =
		options.projectFilename || projectFilenameForSlug(slug);
	state.metaDirName = META_DIR;
	ensureBundleLayout(bundleRoot);

	var projectData = {
		version: "1.0",
		name: name,
		layout: branding.LAYOUT_ID,
		created: now,
		modified: now,
		roles: roles,
		sources: sources,
		settings: options.settings || {},
		pipeline: options.pipeline || {},
		reference_only: referenceOnly,
		alignments: {},
		processing: defaultProcessing(),
	};

	fs.writeFileSync(
		projectFilePath(bundleRoot),
		JSON.stringify(projectData, null, 2),
		"utf8",
	);
	setActiveProject(bundleRoot, projectData);
	return projectData;
}

function isActive() {
	return state.active;
}

function getBundleRoot() {
	return state.bundleRoot;
}

function getProject() {
	return state.project;
}

function getStatusMessage() {
	if (!state.active) {
		return "";
	}
	var name = (state.project && state.project.name) || path.basename(state.bundleRoot);
	var mode = state.project && state.project.reference_only ? "reference" : "bundle";
	return "Project: " + name + " (" + mode + ")";
}

function countFilesRecursive(src) {
	var count = 0;
	var entries;
	try {
		entries = fs.readdirSync(src, { withFileTypes: true });
	} catch (err) {
		return 0;
	}
	for (var i = 0; i < entries.length; i++) {
		var srcPath = path.join(src, entries[i].name);
		if (entries[i].isDirectory()) {
			count += countFilesRecursive(srcPath);
		} else if (entries[i].isFile()) {
			count += 1;
		}
	}
	return count;
}

function copyDirRecursive(src, dest, options) {
	options = options || {};
	var onProgress = options.onProgress;
	var counter = options._counter || { n: 0 };
	var total = options._total;
	if (total === undefined && onProgress) {
		total = countFilesRecursive(src);
		options._total = total;
		options._counter = counter;
	}

	fs.mkdirSync(dest, { recursive: true });
	var entries = fs.readdirSync(src, { withFileTypes: true });
	for (var i = 0; i < entries.length; i++) {
		var entry = entries[i];
		var srcPath = path.join(src, entry.name);
		var destPath = path.join(dest, entry.name);
		if (entry.isDirectory()) {
			copyDirRecursive(srcPath, destPath, {
				onProgress: onProgress,
				_total: total,
				_counter: counter,
			});
		} else {
			fs.copyFileSync(srcPath, destPath);
			counter.n += 1;
			if (onProgress) {
				onProgress({
					type: "file",
					path: srcPath,
					index: counter.n,
					total: total || counter.n,
				});
			}
		}
	}
}

function copyDirRecursiveAsync(src, dest, options) {
	options = options || {};
	var onProgress = options.onProgress;
	var yieldFn = options.yieldFn;
	var yieldEvery = options.yieldEvery || 10;
	var counter = options._counter || { n: 0 };
	var total = options._total;
	if (total === undefined && onProgress) {
		total = countFilesRecursive(src);
		options._total = total;
		options._counter = counter;
	}

	fs.mkdirSync(dest, { recursive: true });
	var entries = fs.readdirSync(src, { withFileTypes: true });
	var chain = Promise.resolve();
	for (var i = 0; i < entries.length; i++) {
		(function (entry) {
			var srcPath = path.join(src, entry.name);
			var destPath = path.join(dest, entry.name);
			chain = chain.then(function () {
				if (entry.isDirectory()) {
					return copyDirRecursiveAsync(srcPath, destPath, {
						onProgress: onProgress,
						yieldFn: yieldFn,
						yieldEvery: yieldEvery,
						_total: total,
						_counter: counter,
					});
				}
				fs.copyFileSync(srcPath, destPath);
				counter.n += 1;
				if (onProgress) {
					onProgress({
						type: "file",
						path: srcPath,
						index: counter.n,
						total: total || counter.n,
					});
				}
				if (yieldFn && counter.n % yieldEvery === 0) {
					return yieldFn();
				}
			});
		})(entries[i]);
	}
	return chain;
}

function classifySourceLayout(sourcePath, role) {
	if (!sourcePath || !fs.existsSync(sourcePath)) {
		return { layout: "flat", runs: [], flatFiles: [], warnings: ["missing source"] };
	}
	var stat = fs.statSync(sourcePath);
	if (!stat.isDirectory()) {
		return { layout: "flat", runs: [], flatFiles: [path.basename(sourcePath)], warnings: [] };
	}
	var isOutput = pipelineRuns.isOutputRole(role);
	var entries;
	try {
		entries = fs.readdirSync(sourcePath, { withFileTypes: true });
	} catch (err) {
		return { layout: "flat", runs: [], flatFiles: [], warnings: [String(err.message || err)] };
	}
	var runs = [];
	var flatFiles = [];
	var warnings = [];
	if (isOutput) {
		var roleStep = null;
		var cfgKeys = Object.keys(pipelineRuns.RUN_STEP_CONFIG);
		for (var c = 0; c < cfgKeys.length; c++) {
			if (pipelineRuns.RUN_STEP_CONFIG[cfgKeys[c]].outputRole === role) {
				roleStep = cfgKeys[c];
				break;
			}
		}
		if (roleStep) {
			runs = pipelineRuns.discoverOutputRuns(
				sourcePath,
				roleStep,
				pipelineRuns.discoveryMaxDepth(roleStep),
			);
		}
		for (var i = 0; i < entries.length; i++) {
			if (entries[i].isFile()) {
				flatFiles.push(entries[i].name);
			}
		}
		var layout = "flat";
		if (runs.length && flatFiles.length) {
			layout = "mixed";
		} else if (runs.length) {
			layout = "nested_runs";
		}
		return { layout: layout, runs: runs, flatFiles: flatFiles, warnings: warnings };
	}
	for (var j = 0; j < entries.length; j++) {
		if (entries[j].isDirectory()) {
			warnings.push("unexpected subdirectory in input role import: " + entries[j].name);
		} else if (entries[j].isFile()) {
			flatFiles.push(entries[j].name);
		}
	}
	return { layout: "flat", runs: [], flatFiles: flatFiles, warnings: warnings };
}

function importSourceToRoleWithLayout(sourcePath, role, mode, bundleRoot, roles, options) {
	options = options || {};
	bundleRoot = bundleRoot || state.bundleRoot;
	roles = roles || (state.project && state.project.roles) || CANONICAL_ROLES;
	var relDest = roles[role] || CANONICAL_ROLES[role];
	var dest = path.isAbsolute(relDest) ? relDest : path.join(bundleRoot, relDest);

	if (!sourcePath || !fs.existsSync(sourcePath)) {
		return { role: role, source: sourcePath, error: "missing source" };
	}
	if (mode === "reference") {
		roles[role] = sourcePath;
		return { role: role, source: sourcePath, dest: sourcePath, mode: mode };
	}

	var classified = classifySourceLayout(sourcePath, role);
	if (pipelineRuns.isOutputRole(role) && classified.layout !== "flat") {
		fs.mkdirSync(dest, { recursive: true });
		if (options.yieldFn) {
			var chain = Promise.resolve();
			for (var ri = 0; ri < classified.runs.length; ri++) {
				(function (runRel) {
					var srcRun = runRel
						? path.join(sourcePath, runRel.split("/").join(path.sep))
						: sourcePath;
					var destRun = runRel
						? path.join(dest, runRel.split("/").join(path.sep))
						: dest;
					chain = chain.then(function () {
						fs.mkdirSync(path.dirname(destRun), { recursive: true });
						return copyDirRecursiveAsync(srcRun, destRun, options);
					});
				})(classified.runs[ri].rel);
			}
			return chain.then(function () {
				if (classified.flatFiles.length) {
					var stamp = new Date().toISOString().replace(/[:.]/g, "-");
					var flatDest = path.join(dest, "import_" + stamp);
					fs.mkdirSync(flatDest, { recursive: true });
					for (var ff = 0; ff < classified.flatFiles.length; ff++) {
						fs.copyFileSync(
							path.join(sourcePath, classified.flatFiles[ff]),
							path.join(flatDest, classified.flatFiles[ff]),
						);
					}
				}
				if (
					state.active &&
					state.project &&
					state.project.processing &&
					classified.runs.length &&
					!pipelineRuns.getActiveRunRelForRole(role)
				) {
					pipelineRuns.setActiveRunRelForRole(role, classified.runs[0].rel);
				}
				return {
					role: role,
					source: sourcePath,
					dest: relDest,
					mode: mode,
					layout: classified.layout,
					runs: classified.runs,
					warnings: classified.warnings,
				};
			});
		}
		var importedRuns = [];
		for (var r = 0; r < classified.runs.length; r++) {
			var runRel = classified.runs[r].rel;
			var srcRun = runRel
				? path.join(sourcePath, runRel.split("/").join(path.sep))
				: sourcePath;
			var destRun = runRel ? path.join(dest, runRel.split("/").join(path.sep)) : dest;
			fs.mkdirSync(path.dirname(destRun), { recursive: true });
			copyDirRecursive(srcRun, destRun, options);
			importedRuns.push(runRel);
		}
		if (classified.flatFiles.length) {
			var stamp = new Date().toISOString().replace(/[:.]/g, "-");
			var flatDest = path.join(dest, "import_" + stamp);
			fs.mkdirSync(flatDest, { recursive: true });
			for (var f = 0; f < classified.flatFiles.length; f++) {
				fs.copyFileSync(
					path.join(sourcePath, classified.flatFiles[f]),
					path.join(flatDest, classified.flatFiles[f]),
				);
			}
		}
		if (
			state.active &&
			state.project &&
			state.project.processing &&
			classified.runs.length &&
			!pipelineRuns.getActiveRunRelForRole(role)
		) {
			pipelineRuns.setActiveRunRelForRole(role, classified.runs[0].rel);
		}
		return {
			role: role,
			source: sourcePath,
			dest: relDest,
			mode: mode,
			layout: classified.layout,
			runs: classified.runs,
			warnings: classified.warnings,
		};
	}
	return importSourceToRole(sourcePath, role, mode, bundleRoot, roles, options);
}

function importSourceToRole(sourcePath, role, mode, bundleRoot, roles, options) {
	options = options || {};
	bundleRoot = bundleRoot || state.bundleRoot;
	roles = roles || (state.project && state.project.roles) || CANONICAL_ROLES;
	var relDest = roles[role] || CANONICAL_ROLES[role];
	var dest = path.isAbsolute(relDest)
		? relDest
		: path.join(bundleRoot, relDest);

	if (!sourcePath || !fs.existsSync(sourcePath)) {
		return { role: role, source: sourcePath, error: "missing source" };
	}

	if (mode === "reference") {
		roles[role] = sourcePath;
		return { role: role, source: sourcePath, dest: sourcePath, mode: mode };
	}

	if (fs.existsSync(dest)) {
		try {
			var st = fs.lstatSync(dest);
			if (st.isSymbolicLink() || st.isFile()) {
				fs.unlinkSync(dest);
			} else if (st.isDirectory() && mode === "copy") {
				fs.rmSync(dest, { recursive: true, force: true });
			}
		} catch (unlinkErr) {
			/* continue */
		}
	}

	var stat = fs.statSync(sourcePath);
	if (mode === "symlink") {
		var linkType = stat.isDirectory() ? "dir" : "file";
		try {
			fs.symlinkSync(sourcePath, dest, linkType);
		} catch (symErr) {
			return {
				role: role,
				source: sourcePath,
				error: String(symErr.message || symErr),
			};
		}
	} else if (stat.isDirectory()) {
		if (options.yieldFn) {
			return copyDirRecursiveAsync(sourcePath, dest, options).then(function () {
				return {
					role: role,
					source: sourcePath,
					dest: relDest,
					mode: mode,
				};
			});
		}
		copyDirRecursive(sourcePath, dest, options);
	} else {
		fs.mkdirSync(path.dirname(dest), { recursive: true });
		fs.copyFileSync(sourcePath, dest);
		if (options.onProgress) {
			options.onProgress({
				type: "file",
				path: sourcePath,
				index: 1,
				total: 1,
			});
		}
	}

	return {
		role: role,
		source: sourcePath,
		dest: relDest,
		mode: mode,
	};
}

function writeImportLog(bundleRoot, mode, entries) {
	var logPath = path.join(metaDir(bundleRoot), "import_log.json");
	var payload = {
		imported_at: nowIso(),
		mode: mode,
		entries: entries || [],
	};
	fs.writeFileSync(logPath, JSON.stringify(payload, null, 2), "utf8");
	return logPath;
}

function sliceIdFromFilename(filename) {
	var stem = path.parse(filename).name;
	if (stem.toLowerCase().endsWith(".ome")) {
		stem = path.parse(stem).name;
	}
	var dot = stem.indexOf(".");
	return dot >= 0 ? stem.slice(0, dot) : stem;
}

function listImageFiles(dir) {
	if (!dir || !fs.existsSync(dir)) {
		return [];
	}
	var out = [];
	var entries;
	try {
		entries = fs.readdirSync(dir, { withFileTypes: true });
	} catch (err) {
		return [];
	}
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isFile()) {
			continue;
		}
		if (IMAGE_EXT_RE.test(entries[i].name) || entries[i].name.toLowerCase().indexOf(".ome.") !== -1) {
			out.push(path.join(dir, entries[i].name));
		}
	}
	return out;
}

function resolveRolePathForBundle(bundleRoot, roles, role) {
	var rel = roles[role];
	if (!rel) {
		return "";
	}
	if (path.isAbsolute(rel)) {
		return rel;
	}
	return path.join(bundleRoot, rel);
}

async function buildManifest(bundleRoot, onProgress) {
	bundleRoot = bundleRoot || state.bundleRoot;
	var roles = (state.project && state.project.roles) || CANONICAL_ROLES;
	if (bundleRoot && (!state.project || state.bundleRoot !== bundleRoot)) {
		try {
			roles = loadProjectJson(bundleRoot).roles || roles;
		} catch (err) {
			/* use defaults */
		}
	}
	var scanRoles = [
		"dapi",
		"slices",
		"max",
		"predictions",
		"quantification",
		"pkls",
		"dual",
	];
	var slicesMap = {};
	var total = scanRoles.length;

	for (var r = 0; r < scanRoles.length; r++) {
		var role = scanRoles[r];
		if (typeof onProgress === "function") {
			var progressRet = onProgress(Math.round((r / total) * 100), "Scanning " + role);
			if (progressRet && typeof progressRet.then === "function") {
				await progressRet;
			}
		}
		var roleDir = resolveRolePathForBundle(bundleRoot, roles, role);
		if (!roleDir && state.active) {
			roleDir = resolveRolePath(role);
		}
		var files = listImageFiles(roleDir);
		for (var f = 0; f < files.length; f++) {
			var filePath = files[f];
			var sliceId = sliceIdFromFilename(path.basename(filePath));
			if (!sliceId) {
				continue;
			}
			if (!slicesMap[sliceId]) {
				slicesMap[sliceId] = { sliceId: sliceId, files: {} };
			}
			var relPath = path.relative(bundleRoot, filePath).split(path.sep).join("/");
			slicesMap[sliceId].files[role] = relPath;
		}
	}

	var sliceList = Object.keys(slicesMap)
		.sort()
		.map(function (k) {
			return slicesMap[k];
		});

	var manifestPath = path.join(metaDir(bundleRoot), "manifest.json");
	fs.mkdirSync(path.dirname(manifestPath), { recursive: true });
	fs.writeFileSync(
		manifestPath,
		JSON.stringify(
			{
				version: 1,
				generated_at: nowIso(),
				slices: sliceList,
			},
			null,
			2,
		),
		"utf8",
	);

	if (typeof onProgress === "function") {
		var doneRet = onProgress(100, "Manifest complete (" + sliceList.length + " slices)");
		if (doneRet && typeof doneRet.then === "function") {
			await doneRet;
		}
	}

	return manifestPath;
}

function chooseProjectBundle(callback) {
	var defaultPath = state.bundleRoot || "";
	dialogs
		.pickDirectory({ tag: "projectBundle", defaultPath: defaultPath })
		.then(function (selected) {
			if (!selected) {
				if (typeof callback === "function") {
					callback(null);
				}
				return;
			}
			try {
				openProject(selected);
				if (typeof callback === "function") {
					callback(null, state.project);
				}
			} catch (err) {
				if (typeof callback === "function") {
					callback(err);
				} else {
					alert(String(err.message || err));
				}
			}
		});
}

function listBundlesInDirectory(parentDir) {
	parentDir = path.resolve(parentDir);
	if (!parentDir || !fs.existsSync(parentDir)) {
		return [];
	}
	var out = [];
	var entries;
	try {
		entries = fs.readdirSync(parentDir, { withFileTypes: true });
	} catch (err) {
		return [];
	}
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isDirectory()) {
			continue;
		}
		var full = path.join(parentDir, entries[i].name);
		if (isBundleRoot(full)) {
			out.push(full);
		}
	}
	return out.sort();
}

var ANNOTATION_PKL_RE = /^Annotation_.*\.pkl$/i;

function countAnnotationPkls(dir) {
	if (!dir || !fs.existsSync(dir)) {
		return 0;
	}
	var count = 0;
	var entries;
	try {
		entries = fs.readdirSync(dir, { withFileTypes: true });
	} catch (err) {
		return 0;
	}
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isFile()) {
			continue;
		}
		if (ANNOTATION_PKL_RE.test(entries[i].name) || /\.pkl$/i.test(entries[i].name)) {
			count++;
		}
	}
	return count;
}

function resolvePathsForBundle(bundleRoot, stepId) {
	bundleRoot = path.resolve(bundleRoot);
	var proj;
	try {
		proj = readProjectJson(bundleRoot);
	} catch (err) {
		proj = null;
	}
	var roles = (proj && proj.roles) || CANONICAL_ROLES;
	var processing = proj ? proj.processing : null;
	return pipelineRuns.resolvePathsForBundleStep(
		bundleRoot,
		roles,
		processing,
		stepId,
	);
}

function preflightBatchPlan(plan) {
	var warnings = [];
	if (!plan || !plan.projects || !plan.steps) {
		return warnings;
	}
	var stepsNeedingAnnotations = { count: true, intensity: true };
	for (var p = 0; p < plan.projects.length; p++) {
		var proj = plan.projects[p];
		var bundleRoot = proj.path;
		var projName = proj.name || path.basename(bundleRoot);
		for (var s = 0; s < plan.steps.length; s++) {
			var stepId = plan.steps[s];
			var paths = resolvePathsForBundle(bundleRoot, stepId);
			var pathKeys = Object.keys(paths);
			for (var k = 0; k < pathKeys.length; k++) {
				var pk = pathKeys[k];
				if (pk === "dapi") {
					continue;
				}
				var dirPath = paths[pk];
				if (!dirPath || !fs.existsSync(dirPath)) {
					warnings.push(
						projName +
							": missing " +
							pk +
							" for " +
							stepId +
							" (" +
							(dirPath || "unset") +
							")",
					);
				}
			}
			if (stepsNeedingAnnotations[stepId]) {
				var slicesDir = resolvePathsForBundle(bundleRoot, stepId).annodir;
				if (!slicesDir || countAnnotationPkls(slicesDir) === 0) {
					warnings.push(
						projName +
							": no annotation PKLs in slices — " +
							stepId +
							" may fail",
					);
				}
			}
		}
	}
	return warnings;
}

function resolveRoleLeafAbsForBundle(bundleRoot, roles, role) {
	var proj;
	try {
		proj = readProjectJson(bundleRoot);
	} catch (err) {
		proj = null;
	}
	var processing = proj ? proj.processing : null;
	return pipelineRuns.resolveActiveRunLeafAbsForBundle(
		bundleRoot,
		roles,
		processing,
		role,
	);
}

function resolvePredictionsRoleBase() {
	return resolveRolePath("predictions") || "";
}

function resolvePredictionsLeafAbs() {
	return pipelineRuns.resolveActiveRunLeafAbs("predictions");
}

function listPredictionRunChoices() {
	return listRunChoicesForRole("predictions");
}

function listRunChoicesForRole(role) {
	return pipelineRuns.listRunChoicesForRole(role);
}

function setActivePredictionRun(rel) {
	return setActiveRunForRole("predictions", rel);
}

function setActiveRunForRole(role, rel) {
	return pipelineRuns.setActiveRunRelForRole(role, rel);
}

function setActiveRunForStep(stepId, rel) {
	return pipelineRuns.setActiveRunRel(stepId, rel);
}

function ensureDefaultActivePredictionRun() {
	pipelineRuns.ensureDefaultActiveRunForRole("predictions");
}

function ensureDefaultActiveRunForRole(role) {
	pipelineRuns.ensureDefaultActiveRunForRole(role);
}

function chooseNewBundleLocation(callback) {
	dialogs.pickDirectory({ tag: "newProjectBundle" }).then(function (selected) {
		if (typeof callback === "function") {
			callback(selected || "");
		}
	});
}

function getGeometryWorkspaceBanner() {
	return state.geometryWorkspaceBanner;
}

module.exports = {
	PROJECT_FILENAME: PROJECT_FILENAME,
	sanitizeProjectSlug: sanitizeProjectSlug,
	bundleDirNameForSlug: bundleDirNameForSlug,
	projectFilenameForSlug: projectFilenameForSlug,
	resolveNewBundlePath: resolveNewBundlePath,
	CANONICAL_ROLES: CANONICAL_ROLES,
	LOGICAL_TO_ROLE: LOGICAL_TO_ROLE,
	isBundleRoot: isBundleRoot,
	isActive: isActive,
	getBundleRoot: getBundleRoot,
	getProject: getProject,
	getGeometryWorkspaceBanner: getGeometryWorkspaceBanner,
	getStatusMessage: getStatusMessage,
	resolveRolePath: resolveRolePath,
	resolveLogicalPath: resolveLogicalPath,
	resolveLogicalPathForOutput: resolveLogicalPathForOutput,
	resolveLogicalPathForInput: resolveLogicalPathForInput,
	createProject: createProject,
	openProject: openProject,
	clearActiveProject: clearActiveProject,
	tryRestoreActiveProject: tryRestoreActiveProject,
	saveProjectJson: saveProjectJson,
	ensureBundleLayout: ensureBundleLayout,
	metaDirPath: metaDirPath,
	importSourceToRole: importSourceToRole,
	importSourceToRoleWithLayout: importSourceToRoleWithLayout,
	classifySourceLayout: classifySourceLayout,
	writeImportLog: writeImportLog,
	buildManifest: buildManifest,
	refreshProjectIndex: refreshProjectIndex,
	getIndexRefreshPromise: getIndexRefreshPromise,
	isIndexRefreshing: isIndexRefreshing,
	readProjectFileIndex: readProjectFileIndex,
	indexedFilesAreUnchanged: indexedFilesAreUnchanged,
	defaultProcessing: defaultProcessing,
	recordStepFailure: recordStepFailure,
	clearStepFailure: clearStepFailure,
	getFailedSliceIds: getFailedSliceIds,
	mergeAlignWarpReport: mergeAlignWarpReport,
	addProcessingStateListener: addProcessingStateListener,
	notifyProcessingStateChanged: notifyProcessingStateChanged,
	computeMatchReport: fileIndex.computeMatchReport,
	planRun: fileIndex.planRun,
	getProcessingSliceIds: fileIndex.getProcessingSliceIds,
	scanOutputsForStep: fileIndex.scanOutputsForStep,
	buildPreviewIndexFromSources: fileIndex.buildPreviewIndexFromSources,
	chooseProjectBundle: chooseProjectBundle,
	chooseNewBundleLocation: chooseNewBundleLocation,
	getRecentProjects: getRecentProjects,
	addRecentProject: addRecentProject,
	syncWorkspaceFromProject: syncWorkspaceFromProject,
	setActiveProject: setActiveProject,
	listBundlesInDirectory: listBundlesInDirectory,
	resolvePathsForBundle: resolvePathsForBundle,
	preflightBatchPlan: preflightBatchPlan,
	countAnnotationPkls: countAnnotationPkls,
	ensureDefaultActivePredictionRun: ensureDefaultActivePredictionRun,
	listPredictionRunChoices: listPredictionRunChoices,
	resolvePredictionsLeafAbs: resolvePredictionsLeafAbs,
	resolvePredictionsRoleBase: resolvePredictionsRoleBase,
	setActivePredictionRun: setActivePredictionRun,
	setActiveRunForRole: setActiveRunForRole,
	setActiveRunForStep: setActiveRunForStep,
	listRunChoicesForRole: listRunChoicesForRole,
	ensureDefaultActiveRunForRole: ensureDefaultActiveRunForRole,
	removeRunForRole: pipelineRuns.removeRunForRole,
	resolveRoleLeafAbsForBundle: resolveRoleLeafAbsForBundle,
	loadProjectJson: loadProjectJson,
	readProjectJson: readProjectJson,
};
