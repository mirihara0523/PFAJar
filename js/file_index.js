"use strict";

var fs = require("fs");
var path = require("path");
var { ipcRenderer } = require("electron");
var homeDir = require("./home_dir");
var pipelineRuns = require("./pipeline_runs");

var FILE_INDEX_VERSION = 1;
var MANIFEST_V2 = 2;

var SCAN_ROLES = [
	"dapi",
	"slices",
	"max",
	"predictions",
	"quantification",
	"pkls",
	"dual",
];

var INPUT_MATCH_ROLES = ["dapi", "max", "slices"];

/** Steps that require a successful align warp; others (align retry, dapi_cleanup, …) may run. */
var STEPS_BLOCKED_BY_ALIGN_FAILURE = {
	count: true,
	intensity: true,
	dual: true,
	collate: true,
	adjust: true,
};

var FLAT_INDEX_ROLES = ["original_scans", "dapi"];

var STEP_OUTPUT = {
	max: { role: "max", step: "max" },
	sharpen: { role: "max", step: "sharpen" },
	tophat: { role: "max", step: "tophat" },
	align: { role: "slices", step: "align" },
	intensity: { role: "pkls", step: "intensity" },
	detect: { role: "predictions", step: "detect" },
	count: { role: "quantification", step: "count" },
	collate: { role: "quantification", step: "collate" },
	dual: { role: "dual", step: "dual" },
};

var IMAGE_EXT_RE = /\.(tif|tiff|png|jpe?g)$/i;

function resolveEnvPython() {
	var home = homeDir.masonHomePath();
	if (process.platform === "win32") {
		return path.join(home, "benv", "Scripts", "python.exe");
	}
	return path.join(home, "benv", "bin", "python3");
}

function sliceIdFromFilename(filename) {
	var stem = path.parse(filename).name;
	if (stem.toLowerCase().endsWith(".ome")) {
		stem = path.parse(stem).name;
	}
	var dot = stem.indexOf(".");
	return dot >= 0 ? stem.slice(0, dot) : stem;
}

function sliceStemFromPredictionPklBasename(basename) {
	var stem = basename.replace(/\.pkl$/i, "");
	if (/^predictions_/i.test(stem)) {
		stem = stem.replace(/^predictions_/i, "");
	}
	return sliceIdFromFilename(stem + ".pkl");
}

function sliceStemFromAnnotationPklBasename(basename) {
	var stem = basename.replace(/\.pkl$/i, "");
	var lower = stem.toLowerCase();
	if (lower.startsWith("annotation_")) {
		stem = stem.slice(11);
	} else if (lower.startsWith("annotations_")) {
		stem = stem.slice(12);
	}
	return sliceIdFromFilename(stem + ".pkl");
}

/**
 * Align outputs under a single directory leaf (no sibling run merge).
 */
function listSlicesRoleFilePaths(absDir, maxDepth) {
	maxDepth = maxDepth == null ? 0 : maxDepth;
	var images = [];
	var pkls = [];
	function walk(dir, depth) {
		if (!dir || !fs.existsSync(dir) || depth > maxDepth) {
			return;
		}
		var entries;
		try {
			entries = fs.readdirSync(dir, { withFileTypes: true });
		} catch (err) {
			return;
		}
		for (var i = 0; i < entries.length; i++) {
			var ent = entries[i];
			var full = path.join(dir, ent.name);
			if (ent.isDirectory()) {
				walk(full, depth + 1);
				continue;
			}
			if (!ent.isFile()) {
				continue;
			}
			if (/\.pkl$/i.test(ent.name) && !/^predictions_/i.test(ent.name)) {
				pkls.push(full);
			} else if (
				IMAGE_EXT_RE.test(ent.name) ||
				ent.name.toLowerCase().indexOf(".ome.") !== -1
			) {
				images.push(full);
			}
		}
	}
	walk(absDir, 0);
	return { images: images, pkls: pkls };
}

function listPredictionPklRecords(absDir, bundleRoot, maxDepth) {
	maxDepth = maxDepth == null ? 2 : maxDepth;
	var records = [];
	function walk(dir, depth) {
		if (!dir || !fs.existsSync(dir) || depth > maxDepth) {
			return;
		}
		var entries;
		try {
			entries = fs.readdirSync(dir, { withFileTypes: true });
		} catch (err) {
			return;
		}
		for (var i = 0; i < entries.length; i++) {
			var ent = entries[i];
			var full = path.join(dir, ent.name);
			if (ent.isDirectory()) {
				walk(full, depth + 1);
				continue;
			}
			if (!/^predictions_.*\.pkl$/i.test(ent.name)) {
				continue;
			}
			var st = fs.statSync(full);
			var sliceId = sliceStemFromPredictionPklBasename(ent.name);
			var relPath = path.relative(bundleRoot, full).split(path.sep).join("/");
			records.push({
				sliceId: sliceId,
				role: "predictions",
				relPath: relPath,
				basename: ent.name,
				size: st.size,
				mtime: st.mtime.toISOString(),
				metadata: {},
				orientation: { aspectRatio: null, landscape: null, exifOrientation: null },
				outputs: {},
			});
		}
	}
	walk(absDir, 0);
	return records;
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
		var name = entries[i].name;
		if (IMAGE_EXT_RE.test(name) || name.toLowerCase().indexOf(".ome.") !== -1) {
			out.push(path.join(dir, name));
		}
	}
	return out;
}

function orientationFromMeta(meta) {
	var w = meta && meta.width;
	var h = meta && meta.height;
	if (!w || !h) {
		return { aspectRatio: null, landscape: null, exifOrientation: null };
	}
	return {
		aspectRatio: w / h,
		landscape: w > h,
		exifOrientation: null,
	};
}

var METADATA_BATCH_SIZE = 40;

function fetchMetadataBatchChunk(filePaths) {
	if (!ipcRenderer || typeof ipcRenderer.on !== "function") {
		return Promise.resolve({});
	}
	return new Promise(function (resolve) {
		var reqId =
			"meta_" + Date.now() + "_" + Math.random().toString(36).slice(2);
		function onResult(_event, payload) {
			if (!payload || payload.reqId !== reqId) {
				return;
			}
			ipcRenderer.removeListener("indexMetadataResult", onResult);
			resolve(payload.map || {});
		}
		ipcRenderer.on("indexMetadataResult", onResult);
		try {
			ipcRenderer.send("runIndexMetadata", { reqId: reqId, paths: filePaths });
		} catch (err) {
			ipcRenderer.removeListener("indexMetadataResult", onResult);
			resolve({});
		}
	});
}

function fetchMetadataBatch(filePaths, appRoot) {
	if (!filePaths.length) {
		return Promise.resolve({});
	}
	var batches = [];
	for (var b = 0; b < filePaths.length; b += METADATA_BATCH_SIZE) {
		batches.push(filePaths.slice(b, b + METADATA_BATCH_SIZE));
	}
	return batches.reduce(function (chain, batch) {
		return chain.then(function (map) {
			return fetchMetadataBatchChunk(batch).then(function (chunkMap) {
				for (var key in chunkMap) {
					if (Object.prototype.hasOwnProperty.call(chunkMap, key)) {
						map[key] = chunkMap[key];
					}
				}
				return map;
			});
		});
	}, Promise.resolve({}));
}

function scanImageFiles(dir, options) {
	options = options || {};
	return scanImageFilesFromPaths(listImageFiles(dir), options.metadataMap || {});
}

function scanImageFilesFromPaths(filePaths, metaMap) {
	var records = [];
	for (var i = 0; i < filePaths.length; i++) {
		var abs = filePaths[i];
		var st = fs.statSync(abs);
		var meta = metaMap[abs] || {};
		records.push({
			basename: path.basename(abs),
			absPath: abs,
			size: st.size,
			mtime: st.mtime.toISOString(),
			metadata: meta,
			orientation: orientationFromMeta(meta),
		});
	}
	return records;
}

function resolveIndexLeafDir(bundleRoot, roles, role, activeRuns) {
	var rel = roles[role];
	if (!rel) {
		return "";
	}
	var base = path.isAbsolute(rel) ? rel : path.join(bundleRoot, rel);
	if (FLAT_INDEX_ROLES.indexOf(role) >= 0) {
		return base;
	}
	var activeRel = (activeRuns && activeRuns[role]) || "";
	if (activeRel) {
		return path.join(base, activeRel.split("/").join(path.sep));
	}
	var stepId = null;
	var cfgKeys = Object.keys(pipelineRuns.RUN_STEP_CONFIG);
	for (var i = 0; i < cfgKeys.length; i++) {
		if (pipelineRuns.RUN_STEP_CONFIG[cfgKeys[i]].outputRole === role) {
			stepId = cfgKeys[i];
			break;
		}
	}
	if (stepId && pipelineRuns.hasRunMarkers(base, stepId)) {
		return base;
	}
	return base;
}

function resolveOutputLeafDir(bundleRoot, stepId, roles, activeRuns) {
	var cfg = STEP_OUTPUT[stepId];
	if (!cfg) {
		return "";
	}
	return resolveIndexLeafDir(bundleRoot, roles, cfg.role, activeRuns);
}

function sliceImageExistsInDir(dir, sliceId) {
	if (!dir || !fs.existsSync(dir)) {
		return false;
	}
	var imgs = listImageFiles(dir);
	for (var im = 0; im < imgs.length; im++) {
		if (sliceIdFromFilename(path.basename(imgs[im])) === sliceId) {
			return true;
		}
	}
	return false;
}

function scanPreprocessStepLeaves(maxBase, stepId, sliceId) {
	if (!maxBase || !fs.existsSync(maxBase)) {
		return false;
	}
	var flatStep = path.join(maxBase, stepId);
	if (fs.existsSync(flatStep)) {
		var flatRuns;
		try {
			flatRuns = fs.readdirSync(flatStep, { withFileTypes: true });
		} catch (_err) {
			flatRuns = [];
		}
		for (var fr = 0; fr < flatRuns.length; fr++) {
			if (!flatRuns[fr].isDirectory()) {
				continue;
			}
			if (sliceImageExistsInDir(path.join(flatStep, flatRuns[fr].name), sliceId)) {
				return true;
			}
		}
	}
	var branches;
	try {
		branches = fs.readdirSync(maxBase, { withFileTypes: true });
	} catch (_err2) {
		return false;
	}
	for (var b = 0; b < branches.length; b++) {
		if (!branches[b].isDirectory()) {
			continue;
		}
		if (branches[b].name === stepId || branches[b].name === "max" || branches[b].name === "sharpen" || branches[b].name === "tophat") {
			continue;
		}
		var stepDir = path.join(maxBase, branches[b].name, stepId);
		if (!fs.existsSync(stepDir)) {
			continue;
		}
		var runs;
		try {
			runs = fs.readdirSync(stepDir, { withFileTypes: true });
		} catch (_err3) {
			continue;
		}
		for (var r = 0; r < runs.length; r++) {
			if (!runs[r].isDirectory()) {
				continue;
			}
			if (sliceImageExistsInDir(path.join(stepDir, runs[r].name), sliceId)) {
				return true;
			}
		}
	}
	return false;
}

function preprocessOutputExistsForSlice(bundleRoot, stepId, sliceId, roles, activeRuns) {
	var maxRel = roles.max;
	if (!maxRel) {
		return false;
	}
	var maxBase = path.isAbsolute(maxRel) ? maxRel : path.join(bundleRoot, maxRel);
	var activeRel = (activeRuns && activeRuns.max) || "";
	if (activeRel && activeRel.indexOf("/" + stepId + "/") >= 0) {
		var activeLeaf = path.join(maxBase, activeRel.split("/").join(path.sep));
		if (sliceImageExistsInDir(activeLeaf, sliceId)) {
			return true;
		}
	}
	return scanPreprocessStepLeaves(maxBase, stepId, sliceId);
}

function outputExistsForSlice(bundleRoot, stepId, sliceId, roles, activeRuns) {
	var cfg = STEP_OUTPUT[stepId];
	if (!cfg) {
		return false;
	}
	activeRuns = activeRuns || null;
	var dir = resolveOutputLeafDir(bundleRoot, stepId, roles, activeRuns);
	if (!dir || !fs.existsSync(dir)) {
		return false;
	}
	if (stepId === "align") {
		var candidates = [
			"Annotation_" + sliceId + ".pkl",
			"annotations_" + sliceId + ".pkl",
			sliceId + ".pkl",
		];
		for (var c = 0; c < candidates.length; c++) {
			if (fs.existsSync(path.join(dir, candidates[c]))) {
				return true;
			}
		}
		var entries = fs.readdirSync(dir);
		for (var e = 0; e < entries.length; e++) {
			if (!/\.pkl$/i.test(entries[e])) {
				continue;
			}
			var stem = entries[e].replace(/\.pkl$/i, "");
			if (/^annotation_/i.test(stem)) {
				stem = stem.replace(/^annotation_/i, "");
			} else if (/^annotations_/i.test(stem)) {
				stem = stem.replace(/^annotations_/i, "");
			}
			if (sliceIdFromFilename(stem) === sliceId) {
				return true;
			}
		}
		return false;
	}
	if (stepId === "intensity") {
		var prefix = sliceId + "_";
		var pkls = fs.readdirSync(dir);
		for (var p = 0; p < pkls.length; p++) {
			if (pkls[p].indexOf(prefix) === 0 && /\.pkl$/i.test(pkls[p])) {
				return true;
			}
		}
		return false;
	}
	if (stepId === "detect") {
		return predictionPklExistsForSlice(dir, sliceId);
	}
	if (stepId === "max") {
		var imgs = listImageFiles(dir);
		for (var im = 0; im < imgs.length; im++) {
			if (sliceIdFromFilename(path.basename(imgs[im])) === sliceId) {
				return true;
			}
		}
		return false;
	}
	if (stepId === "sharpen" || stepId === "tophat") {
		return preprocessOutputExistsForSlice(
			bundleRoot,
			stepId,
			sliceId,
			roles,
			activeRuns,
		);
	}
	if (stepId === "count" || stepId === "collate") {
		return fs.existsSync(path.join(dir, "count_results.csv"));
	}
	if (stepId === "dual") {
		var duals = fs.readdirSync(dir);
		for (var d = 0; d < duals.length; d++) {
			if (duals[d].indexOf(sliceId + "_") === 0 && /_dual\.tif$/i.test(duals[d])) {
				return true;
			}
		}
		return false;
	}
	return false;
}

function predictionPklExistsForSlice(predLeafDir, sliceId) {
	if (!predLeafDir || !sliceId || !fs.existsSync(predLeafDir)) {
		return false;
	}
	var direct = path.join(predLeafDir, "Predictions_" + sliceId + ".pkl");
	if (fs.existsSync(direct)) {
		return true;
	}
	try {
		var entries = fs.readdirSync(predLeafDir);
		for (var i = 0; i < entries.length; i++) {
			if (!/^predictions_.*\.pkl$/i.test(entries[i])) {
				continue;
			}
			if (sliceStemFromPredictionPklBasename(entries[i]) === sliceId) {
				return true;
			}
		}
	} catch (err) {
		return false;
	}
	return false;
}

function resolvePredictionsScan(predBaseDir, maxDepth) {
	maxDepth = maxDepth == null ? 2 : maxDepth;
	var result = {
		resolvedPath: predBaseDir || "",
		warning: "",
		candidates: [],
	};
	if (!predBaseDir || !fs.existsSync(predBaseDir)) {
		return result;
	}
	function hasTopLevelPredictions(dir) {
		try {
			var e = fs.readdirSync(dir);
			for (var i = 0; i < e.length; i++) {
				if (/^predictions_.*\.pkl$/i.test(e[i])) {
					return true;
				}
			}
		} catch (err) {}
		return false;
	}
	if (hasTopLevelPredictions(predBaseDir)) {
		result.resolvedPath = predBaseDir;
		return result;
	}
	function walk(dir, depth) {
		if (!dir || depth > maxDepth) {
			return;
		}
		var entries;
		try {
			entries = fs.readdirSync(dir, { withFileTypes: true });
		} catch (err) {
			return;
		}
		for (var j = 0; j < entries.length; j++) {
			if (!entries[j].isDirectory()) {
				continue;
			}
			var sub = path.join(dir, entries[j].name);
			if (hasTopLevelPredictions(sub)) {
				var mt = 0;
				try {
					var files = fs.readdirSync(sub);
					for (var k = 0; k < files.length; k++) {
						if (/^predictions_.*\.pkl$/i.test(files[k])) {
							var st = fs.statSync(path.join(sub, files[k]));
							if (st.mtimeMs > mt) {
								mt = st.mtimeMs;
							}
						}
					}
				} catch (err2) {}
				result.candidates.push({ path: sub, mtime: mt });
			}
			walk(sub, depth + 1);
		}
	}
	walk(predBaseDir, 0);
	if (!result.candidates.length) {
		result.resolvedPath = predBaseDir;
		return result;
	}
	result.candidates.sort(function (a, b) {
		return b.mtime - a.mtime;
	});
	result.resolvedPath = result.candidates[0].path;
	if (result.candidates.length > 1) {
		result.warning =
			"Multiple nested prediction outputs found under predictions; using the most recently modified folder. Choose predictions and slices on the Count page if needed.";
	}
	return result;
}

function compareDimensions(recordsByRole, sliceId) {
	var issues = [];
	var dims = {};
	var roleKeys = Object.keys(recordsByRole);
	for (var r = 0; r < roleKeys.length; r++) {
		var role = roleKeys[r];
		var rec = recordsByRole[role];
		if (!rec || !rec.metadata || !rec.metadata.width || !rec.metadata.height) {
			continue;
		}
		dims[role] = rec.metadata;
	}
	var rolesWithDims = Object.keys(dims);
	if (rolesWithDims.length < 2) {
		return issues;
	}
	var base = dims[rolesWithDims[0]];
	for (var i = 1; i < rolesWithDims.length; i++) {
		var otherRole = rolesWithDims[i];
		var other = dims[otherRole];
		var bw = base.width;
		var bh = base.height;
		var ow = other.width;
		var oh = other.height;
		var baseAr = bw / bh;
		var otherAr = ow / oh;
		if (Math.abs(baseAr - otherAr) < 0.02 && (bw !== ow || bh !== oh)) {
			issues.push({
				sliceId: sliceId,
				severity: "warning",
				code: "resolution_mismatch",
				message:
					rolesWithDims[0] +
					" and " +
					otherRole +
					" for " +
					sliceId +
					" differ in resolution (" +
					bw +
					"×" +
					bh +
					" vs " +
					ow +
					"×" +
					oh +
					")—processing will resize; verify this is intended.",
			});
		}
		if (
			Math.abs(baseAr - 1 / otherAr) < 0.05 &&
			Math.abs(baseAr - otherAr) > 0.15
		) {
			issues.push({
				sliceId: sliceId,
				severity: "warning",
				code: "orientation_swap",
				message:
					"Possible orientation mismatch: " +
					rolesWithDims[0] +
					" is " +
					bw +
					"×" +
					bh +
					" but " +
					otherRole +
					" is " +
					ow +
					"×" +
					oh +
					".",
			});
		}
		if (baseAr > 4 || baseAr < 0.25) {
			issues.push({
				sliceId: sliceId,
				severity: "info",
				code: "extreme_aspect",
				message: "Unusual aspect ratio for " + sliceId + "; confirm section orientation.",
			});
		}
	}
	return issues;
}

function computeMatchReport(index, activeRoles, options) {
	activeRoles = activeRoles || INPUT_MATCH_ROLES;
	options = options || {};
	var bySlice = {};
	var orphansByRole = {};
	for (var r = 0; r < activeRoles.length; r++) {
		orphansByRole[activeRoles[r]] = [];
	}
	for (var i = 0; i < (index.files || []).length; i++) {
		var rec = index.files[i];
		if (activeRoles.indexOf(rec.role) < 0) {
			continue;
		}
		if (!bySlice[rec.sliceId]) {
			bySlice[rec.sliceId] = { sliceId: rec.sliceId, roles: {} };
		}
		bySlice[rec.sliceId].roles[rec.role] = rec;
	}
	var matchedSliceIds = [];
	var qualityIssues = [];
	// Natural (numeric-aware) sort so sections load in human order — e.g.
	// ...(2), (3), ..., (9), (11) rather than lexicographic (11) before (2).
	var sliceKeys = Object.keys(bySlice).sort(function (a, b) {
		return String(a).localeCompare(String(b), undefined, {
			numeric: true,
			sensitivity: "base",
		});
	});
	for (var s = 0; s < sliceKeys.length; s++) {
		var sid = sliceKeys[s];
		var entry = bySlice[sid];
		var present = activeRoles.filter(function (role) {
			return !!entry.roles[role];
		});
		if (present.length >= 2) {
			matchedSliceIds.push(sid);
			var dimIssues = compareDimensions(entry.roles, sid);
			qualityIssues = qualityIssues.concat(dimIssues);
		} else if (present.length === 1) {
			orphansByRole[present[0]].push(sid);
		} else {
			for (var o = 0; o < present.length; o++) {
				orphansByRole[present[o]].push(sid);
			}
		}
	}
	for (var or = 0; or < activeRoles.length; or++) {
		var role = activeRoles[or];
		var roleSlices = Object.keys(bySlice).filter(function (k) {
			return bySlice[k].roles[role] && activeRoles.filter(function (ar) {
				return bySlice[k].roles[ar];
			}).length < 2;
		});
		for (var rs = 0; rs < roleSlices.length; rs++) {
			var onlyRole = bySlice[roleSlices[rs]].roles[role];
			if (onlyRole && orphansByRole[role].indexOf(roleSlices[rs]) < 0) {
				var hasOther = false;
				for (var ar2 = 0; ar2 < activeRoles.length; ar2++) {
					if (
						activeRoles[ar2] !== role &&
						bySlice[roleSlices[rs]].roles[activeRoles[ar2]]
					) {
						hasOther = true;
					}
				}
				if (!hasOther) {
					orphansByRole[role].push(roleSlices[rs]);
				}
			}
		}
	}
	var pairwiseCounts = {};
	for (var pi = 0; pi < activeRoles.length; pi++) {
		for (var pj = pi + 1; pj < activeRoles.length; pj++) {
			var a = activeRoles[pi];
			var b = activeRoles[pj];
			var key = a + "+" + b;
			pairwiseCounts[key] = 0;
			for (var m = 0; m < matchedSliceIds.length; m++) {
				var mid = matchedSliceIds[m];
				if (bySlice[mid].roles[a] && bySlice[mid].roles[b]) {
					pairwiseCounts[key]++;
				}
			}
		}
	}
	return {
		matchedSliceIds: matchedSliceIds,
		orphansByRole: orphansByRole,
		pairwiseCounts: pairwiseCounts,
		qualityIssues: qualityIssues,
		bySlice: bySlice,
	};
}

function buildManifestV2(bundleRoot, index, report) {
	return {
		version: MANIFEST_V2,
		generated_at: new Date().toISOString(),
		matched_count: report.matchedSliceIds.length,
		orphans_by_role: report.orphansByRole,
		quality_summary: {
			issue_count: report.qualityIssues.length,
			codes: report.qualityIssues.map(function (q) {
				return q.code;
			}),
		},
		slices: report.matchedSliceIds.map(function (sid) {
			var files = {};
			var sliceEntry = report.bySlice[sid];
			if (sliceEntry) {
				var roles = Object.keys(sliceEntry.roles);
				for (var i = 0; i < roles.length; i++) {
					files[roles[i]] = sliceEntry.roles[roles[i]].relPath;
				}
			}
			return { sliceId: sid, files: files };
		}),
	};
}

function buildPreviewIndexFromSources(sources, options) {
	options = options || {};
	var files = [];
	var pathsForMeta = [];
	var roleKeys = Object.keys(sources || {});
	for (var r = 0; r < roleKeys.length; r++) {
		var role = roleKeys[r];
		var src = sources[role];
		if (!src || !fs.existsSync(src)) {
			continue;
		}
		var st = fs.statSync(src);
		var dir = st.isDirectory() ? src : path.dirname(src);
		var imgs = listImageFiles(dir);
		for (var f = 0; f < imgs.length; f++) {
			pathsForMeta.push(imgs[f]);
		}
	}
	return fetchMetadataBatch(pathsForMeta, options.appRoot).then(function (metaMap) {
		for (var ri = 0; ri < roleKeys.length; ri++) {
			var scanRole = roleKeys[ri];
			var scanSrc = sources[scanRole];
			if (!scanSrc || !fs.existsSync(scanSrc)) {
				continue;
			}
			var st2 = fs.statSync(scanSrc);
			var scanDir = st2.isDirectory() ? scanSrc : path.dirname(scanSrc);
			var scanned = scanImageFiles(scanDir, { metadataMap: metaMap });
			for (var si = 0; si < scanned.length; si++) {
				var row = scanned[si];
				files.push({
					sliceId: sliceIdFromFilename(row.basename),
					role: scanRole,
					relPath: row.absPath,
					basename: row.basename,
					size: row.size,
					mtime: row.mtime,
					metadata: row.metadata,
					orientation: row.orientation,
					outputs: {},
				});
			}
		}
		return {
			version: FILE_INDEX_VERSION,
			generated_at: new Date().toISOString(),
			files: files,
			preview: true,
		};
	});
}

function buildFileIndex(bundleRoot, roles, options) {
	options = options || {};
	var activeRuns = options.activeRuns || pipelineRuns.defaultActiveRuns();
	var files = [];
	var pathsForMeta = [];
	var roleDirs = {};
	for (var r = 0; r < SCAN_ROLES.length; r++) {
		var role = SCAN_ROLES[r];
		var rel = roles[role];
		if (!rel) {
			continue;
		}
		var dir = resolveIndexLeafDir(bundleRoot, roles, role, activeRuns);
		roleDirs[role] = dir;
		if (role === "predictions" || role === "slices") {
			continue;
		}
		if (FLAT_INDEX_ROLES.indexOf(role) >= 0 || role === "dapi") {
			var flatImgs = listImageFiles(dir);
			for (var df = 0; df < flatImgs.length; df++) {
				pathsForMeta.push(flatImgs[df]);
			}
		} else {
			var imgs = listImageFiles(dir);
			for (var f = 0; f < imgs.length; f++) {
				pathsForMeta.push(imgs[f]);
			}
		}
	}
	// Incremental metadata cache: reuse metadata from the previous
	// file_index.json for files whose size + mtime are unchanged, so only
	// new/changed images are sent for (expensive) metadata extraction. On NAS
	// this removes the dominant per-image cost on repeat index builds.
	var priorByAbs = {};
	if (options.priorIndex && Array.isArray(options.priorIndex.files)) {
		var _pf = options.priorIndex.files;
		for (var _pi = 0; _pi < _pf.length; _pi++) {
			var _e = _pf[_pi];
			if (!_e || !_e.relPath) {
				continue;
			}
			var _absPrev = path.resolve(
				bundleRoot,
				String(_e.relPath).split("/").join(path.sep),
			);
			priorByAbs[_absPrev] = {
				size: _e.size,
				mtime: _e.mtime,
				metadata: _e.metadata || {},
			};
		}
	}
	var reusedMeta = {};
	var toFetch = [];
	for (var _mp = 0; _mp < pathsForMeta.length; _mp++) {
		var _apath = pathsForMeta[_mp];
		var _prev = priorByAbs[path.resolve(_apath)];
		var _reused = false;
		if (_prev && _prev.metadata && Object.keys(_prev.metadata).length) {
			var _stc = null;
			try {
				_stc = fs.statSync(_apath);
			} catch (_err) {
				_stc = null;
			}
			if (
				_stc &&
				_stc.size === _prev.size &&
				_stc.mtime.toISOString() === _prev.mtime
			) {
				reusedMeta[_apath] = _prev.metadata;
				_reused = true;
			}
		}
		if (!_reused) {
			toFetch.push(_apath);
		}
	}
	return fetchMetadataBatch(toFetch, options.appRoot).then(function (fetchedMeta) {
		var metaMap = Object.assign({}, reusedMeta, fetchedMeta);
		for (var ri = 0; ri < SCAN_ROLES.length; ri++) {
			var scanRole = SCAN_ROLES[ri];
			var scanDir = roleDirs[scanRole];
			if (!scanDir) {
				continue;
			}
			if (scanRole === "predictions") {
				var predRows = listPredictionPklRecords(scanDir, bundleRoot, 0);
				for (var pi = 0; pi < predRows.length; pi++) {
					files.push(predRows[pi]);
				}
				continue;
			}
			if (scanRole === "slices") {
				var slicesFiles = listSlicesRoleFilePaths(scanDir, 0);
				for (var pi = 0; pi < slicesFiles.pkls.length; pi++) {
					var pklAbs = slicesFiles.pkls[pi];
					var pklBase = path.basename(pklAbs);
					var pklSliceId = sliceStemFromAnnotationPklBasename(pklBase);
					var stPkl = fs.statSync(pklAbs);
					var relPkl = path
						.relative(bundleRoot, pklAbs)
						.split(path.sep)
						.join("/");
					files.push({
						sliceId: pklSliceId,
						role: "slices",
						relPath: relPkl,
						basename: pklBase,
						size: stPkl.size,
						mtime: stPkl.mtime.toISOString(),
						metadata: {},
						orientation: {
							aspectRatio: null,
							landscape: null,
							exifOrientation: null,
						},
						outputs: {
							slices: {
								relPath: relPkl,
								exists: true,
								mtime: stPkl.mtime.toISOString(),
							},
						},
					});
				}
				continue;
			}
			var scannedOther = scanImageFiles(scanDir, { metadataMap: metaMap });
			for (var sj = 0; sj < scannedOther.length; sj++) {
				var rowOther = scannedOther[sj];
				var sliceIdOther = sliceIdFromFilename(rowOther.basename);
				var relPathOther = path
					.relative(bundleRoot, rowOther.absPath)
					.split(path.sep)
					.join("/");
				files.push({
					sliceId: sliceIdOther,
					role: scanRole,
					relPath: relPathOther,
					basename: rowOther.basename,
					size: rowOther.size,
					mtime: rowOther.mtime,
					metadata: rowOther.metadata,
					orientation: rowOther.orientation,
					outputs: {},
				});
			}
		}
		var index = {
			version: FILE_INDEX_VERSION,
			generated_at: new Date().toISOString(),
			bundle_root: bundleRoot,
			files: files,
		};
		return index;
	});
}

function writeFileIndex(bundleRoot, metaDirPath, index) {
	var indexPath = path.join(metaDirPath, "file_index.json");
	fs.mkdirSync(path.dirname(indexPath), { recursive: true });
	fs.writeFileSync(indexPath, JSON.stringify(index, null, 2), "utf8");
	return indexPath;
}

function readFileIndex(bundleRoot, metaDirPath) {
	var indexPath = path.join(metaDirPath, "file_index.json");
	if (!fs.existsSync(indexPath)) {
		return null;
	}
	try {
		return JSON.parse(fs.readFileSync(indexPath, "utf8"));
	} catch (err) {
		return null;
	}
}

function getProcessingSliceIds(bundleRoot, projectData, index, report, options) {
	report =
		report ||
		(index ? computeMatchReport(index, INPUT_MATCH_ROLES) : { matchedSliceIds: [] });
	var ids = report.matchedSliceIds.slice();
	var proc = (projectData && projectData.processing) || {};
	if (proc.subset_enabled && proc.slice_ids && proc.slice_ids.length) {
		var subset = {};
		for (var i = 0; i < proc.slice_ids.length; i++) {
			subset[proc.slice_ids[i]] = true;
		}
		ids = ids.filter(function (sid) {
			return subset[sid];
		});
	}
	options = options || {};
	var stepId = options.stepId || "";
	var filterAlignFailures =
		options.filterAlignFailures != null
			? options.filterAlignFailures
			: !stepId || !!STEPS_BLOCKED_BY_ALIGN_FAILURE[stepId];
	if (filterAlignFailures) {
		var alignFailures =
			proc.step_failures && proc.step_failures.align ? proc.step_failures.align : {};
		var failedAlignIds = Object.keys(alignFailures);
		if (failedAlignIds.length) {
			var failedSet = {};
			for (var f = 0; f < failedAlignIds.length; f++) {
				failedSet[failedAlignIds[f]] = true;
			}
			ids = ids.filter(function (sid) {
				return !failedSet[sid];
			});
		}
	}
	return ids;
}

function scanOutputsForStep(bundleRoot, stepId, sliceIds, roles, activeRuns) {
	var out = {};
	for (var i = 0; i < sliceIds.length; i++) {
		var sid = sliceIds[i];
		out[sid] = outputExistsForSlice(bundleRoot, stepId, sid, roles, activeRuns);
	}
	return out;
}

function planRun(bundleRoot, stepId, options) {
	options = options || {};
	var mode = options.mode || "merge";
	var sliceIds = options.sliceIds || [];
	var roles = options.roles || {};
	var activeRuns = options.activeRuns || null;
	var toProcess = [];
	var skipped = [];
	for (var i = 0; i < sliceIds.length; i++) {
		var sid = sliceIds[i];
		var exists = outputExistsForSlice(bundleRoot, stepId, sid, roles, activeRuns);
		if (mode === "overwrite" || !exists) {
			toProcess.push(sid);
		} else {
			skipped.push({ sliceId: sid, reason: "output_exists" });
		}
	}
	return { toProcess: toProcess, skipped: skipped, mode: mode };
}

function writeRunSliceList(metaDirPath, sliceIds, filenameStems) {
	var runPath = path.join(metaDirPath, "run_slice_list.json");
	fs.writeFileSync(
		runPath,
		JSON.stringify({ slice_ids: sliceIds, filename_stems: filenameStems || [], generated_at: new Date().toISOString() }, null, 2),
		"utf8",
	);
	return runPath;
}

module.exports = {
	FILE_INDEX_VERSION: FILE_INDEX_VERSION,
	MANIFEST_V2: MANIFEST_V2,
	SCAN_ROLES: SCAN_ROLES,
	INPUT_MATCH_ROLES: INPUT_MATCH_ROLES,
	STEPS_BLOCKED_BY_ALIGN_FAILURE: STEPS_BLOCKED_BY_ALIGN_FAILURE,
	STEP_OUTPUT: STEP_OUTPUT,
	sliceIdFromFilename: sliceIdFromFilename,
	sliceStemFromAnnotationPklBasename: sliceStemFromAnnotationPklBasename,
	sliceStemFromPredictionPklBasename: sliceStemFromPredictionPklBasename,
	listSlicesRoleFilePaths: listSlicesRoleFilePaths,
	listImageFiles: listImageFiles,
	scanImageFiles: scanImageFiles,
	buildFileIndex: buildFileIndex,
	computeMatchReport: computeMatchReport,
	buildManifestV2: buildManifestV2,
	writeFileIndex: writeFileIndex,
	readFileIndex: readFileIndex,
	getProcessingSliceIds: getProcessingSliceIds,
	scanOutputsForStep: scanOutputsForStep,
	planRun: planRun,
	outputExistsForSlice: outputExistsForSlice,
	writeRunSliceList: writeRunSliceList,
	buildPreviewIndexFromSources: buildPreviewIndexFromSources,
	predictionPklExistsForSlice: predictionPklExistsForSlice,
	resolvePredictionsScan: resolvePredictionsScan,
	resolveIndexLeafDir: resolveIndexLeafDir,
	resolveOutputLeafDir: resolveOutputLeafDir,
	listPredictionPklRecords: listPredictionPklRecords,
};
