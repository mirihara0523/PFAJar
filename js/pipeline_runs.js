"use strict";

var fs = require("fs");
var path = require("path");
var crypto = require("crypto");

function projectModule() {
	return require("./project");
}

var IMAGE_EXT_RE = /\.(tif|tiff|png|jpe?g)$/i;

var OUTPUT_ROLES = [
	"max",
	"slices",
	"predictions",
	"quantification",
	"pkls",
	"dual",
];

var INPUT_ROLES = ["original_scans", "dapi"];

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

var RUN_STEP_CONFIG = {
	max: {
		stepId: "max",
		outputRole: "max",
		branch: "max",
		inputRoles: ["original_scans"],
		scriptRoles: { indir: "original_scans", outdir: "max" },
	},
	sharpen: {
		stepId: "sharpen",
		outputRole: "max",
		branch: "sharpen",
		inputRoles: ["max"],
		scriptRoles: { indir: "max", outdir: "max" },
	},
	tophat: {
		stepId: "tophat",
		outputRole: "max",
		branch: "tophat",
		inputRoles: ["max"],
		scriptRoles: { indir: "max", outdir: "max" },
	},
	basic: {
		stepId: "basic",
		outputRole: "max",
		branch: "basic",
		inputRoles: ["max"],
		scriptRoles: { indir: "max", outdir: "max" },
	},
	seam: {
		stepId: "seam",
		outputRole: "max",
		branch: "seam",
		inputRoles: ["max"],
		scriptRoles: { indir: "max", outdir: "max" },
	},
	align: {
		stepId: "align",
		outputRole: "slices",
		branch: "align",
		inputRoles: ["dapi"],
		scriptRoles: { indir: "dapi", outdir: "slices" },
	},
	intensity: {
		stepId: "intensity",
		outputRole: "pkls",
		branch: "intensity",
		inputRoles: ["max", "slices"],
		scriptRoles: {
			indir: "max",
			annodir: "slices",
			outdir: "pkls",
			dapi: "dapi",
		},
	},
	detect: {
		stepId: "detect",
		outputRole: "predictions",
		branch: null,
		inputRoles: ["max"],
		scriptRoles: { indir: "max", outdir: "predictions" },
	},
	count: {
		stepId: "count",
		outputRole: "quantification",
		branch: "count",
		inputRoles: ["predictions", "slices"],
		scriptRoles: {
			preddir: "predictions",
			annodir: "slices",
			outdir: "quantification",
		},
	},
	collate: {
		stepId: "collate",
		outputRole: "quantification",
		branch: "collate",
		inputRoles: ["quantification"],
		scriptRoles: { indir: "quantification", outdir: "quantification" },
	},
	dual: {
		stepId: "dual",
		outputRole: "dual",
		branch: "dual",
		inputRoles: ["pkls"],
		scriptRoles: { indir: "pkls", outdir: "dual" },
	},
	dapi_cleanup: {
		stepId: "dapi_cleanup",
		outputRole: null,
		branch: null,
		inputRoles: ["dapi"],
		scriptRoles: { indir: "dapi", outdir: "dapi" },
	},
	parcellation: {
		stepId: "parcellation",
		outputRole: null,
		branch: null,
		inputRoles: ["slices"],
		scriptRoles: { annodir: "slices" },
	},
	apply_geometry: {
		stepId: "apply_geometry",
		outputRole: null,
		branch: null,
		inputRoles: [],
		scriptRoles: {},
	},
};

var STEP_BY_OUTPUT_ROLE = {};
var roleKeys = Object.keys(RUN_STEP_CONFIG);
for (var i = 0; i < roleKeys.length; i++) {
	var sid = roleKeys[i];
	var cfg = RUN_STEP_CONFIG[sid];
	if (!STEP_BY_OUTPUT_ROLE[cfg.outputRole]) {
		STEP_BY_OUTPUT_ROLE[cfg.outputRole] = sid;
	}
}

function isOutputRole(role) {
	return OUTPUT_ROLES.indexOf(role) >= 0;
}

function decToken(num) {
	return String(num).replace(/\./g, "p");
}

function sanitizeSlugPart(s) {
	return String(s || "")
		.replace(/[/\\:*?"<>|]+/g, "_")
		.replace(/\s+/g, "_")
		.replace(/_+/g, "_")
		.replace(/^_|_$/g, "")
		.slice(0, 80);
}

function sliceSpanToken(sortedStems) {
	var seen = {};
	var uniq = [];
	for (var i = 0; i < sortedStems.length; i++) {
		var s = sortedStems[i];
		if (!seen[s]) {
			seen[s] = true;
			uniq.push(s);
		}
	}
	uniq.sort();
	if (!uniq.length) {
		return "noslices";
	}
	if (uniq.length === 1) {
		return sanitizeSlugPart(uniq[0]);
	}
	var first = uniq[0];
	var last = uniq[uniq.length - 1];
	if (uniq.length === 2) {
		return sanitizeSlugPart(first + "-" + last);
	}
	var h = crypto.createHash("sha1").update(uniq.join("|")).digest("hex").slice(0, 4);
	return sanitizeSlugPart(first + "-" + last) + "_h" + h;
}

function shortRefToken(ref) {
	var s = String(ref || "")
		.split(/[/\\]+/)
		.filter(Boolean)
		.join("_");
	if (!s) {
		return "flat";
	}
	if (s.length <= 32) {
		return sanitizeSlugPart(s);
	}
	return (
		sanitizeSlugPart(s.slice(0, 20)) +
		"_h" +
		crypto.createHash("sha1").update(s).digest("hex").slice(0, 6)
	);
}

function listImageSliceStems(dirPath) {
	if (!dirPath || !fs.existsSync(dirPath)) {
		return [];
	}
	var entries;
	try {
		entries = fs.readdirSync(dirPath, { withFileTypes: true });
	} catch (err) {
		return [];
	}
	var stems = [];
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isFile()) {
			continue;
		}
		var n = entries[i].name;
		if (IMAGE_EXT_RE.test(n) || n.toLowerCase().indexOf(".ome.") !== -1) {
			var stem = path.parse(n).name;
			if (/\.ome$/i.test(stem)) {
				stem = path.parse(stem).name;
			}
			var dot = stem.indexOf(".");
			stems.push(dot >= 0 ? stem.slice(0, dot) : stem);
		}
	}
	stems.sort();
	return stems;
}

// {name, abs} version of listImageSliceStems() above, natural-sorted by name.
// Same IMAGE_EXT_RE (tif/tiff/png/jpg/jpeg + .ome.) -- the single shared
// definition of "what counts as a processable slice image" outside the
// TIFF-only max-role convention (isProcessableTiffName in preprocess_wizard.js).
// Added 2026-09-04 so DAPI-source listing (preprocess_wizard.js) and BaSiC's
// own DAPI discovery (basic_wizard.js listImageFiles()) can both point at one
// place instead of each keeping a private copy of the same regex/loop.
function listImageSliceFiles(dirPath) {
	if (!dirPath || !fs.existsSync(dirPath)) {
		return [];
	}
	var entries;
	try {
		entries = fs.readdirSync(dirPath, { withFileTypes: true });
	} catch (err) {
		return [];
	}
	var out = [];
	for (var i = 0; i < entries.length; i++) {
		var n = entries[i].name;
		if (entries[i].isFile() && (IMAGE_EXT_RE.test(n) || n.toLowerCase().indexOf(".ome.") !== -1)) {
			out.push({ name: n, abs: path.join(dirPath, n) });
		}
	}
	// sensitivity: "base" matches basic_wizard.js's original listImageFiles()
	// sort exactly (case-insensitive) -- kept when that function was
	// consolidated to delegate here, 2026-09-04.
	out.sort(function (a, b) {
		return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
	});
	return out;
}

// Stable dual identity for dotted acquisition filenames. Existing callers keep
// using the short logical slice ID; new consumers can use filenameStem for
// exact file and sidecar matching without changing legacy project keys.
function listImageSliceRecords(dirPath) {
	return listImageSliceFiles(dirPath).map(function (entry) {
		var stem = path.parse(entry.name).name;
		if (/\.ome$/i.test(stem)) stem = path.parse(stem).name;
		var dot = stem.indexOf(".");
		return {
			name: entry.name,
			abs: entry.abs,
			sliceId: dot >= 0 ? stem.slice(0, dot) : stem,
			filenameStem: stem,
		};
	});
}

function buildDetectRunSlug(options) {
	var c = options.confidence;
	var t = options.tile;
	var a = options.area;
	var e = options.eccentricity;
	var params =
		"c" +
		decToken(c) +
		"_t" +
		String(Math.round(t)) +
		"_a" +
		String(Math.round(a)) +
		"_e" +
		decToken(e);
	if (options.intensityMin && Number(options.intensityMin) > 0) {
		params += "_i" + decToken(Number(options.intensityMin));
	}
	var span = sliceSpanToken(options.sortedStems || []);
	var subset = "";
	if (options.subsetCount && options.subsetCount > 0) {
		subset = "_subset_" + String(options.subsetCount);
	}
	// Prefer dataset kind (max/sharpen/…) over full branch path — signal branch
	// is already the predictions folder name.
	var inputToken = "";
	if (options.inputDatasetRel) {
		var inParts = normalizeRelPath(options.inputDatasetRel).split("/");
		var kindTok =
			inParts.length >= 2 ? inParts.slice(1).join("_") : options.inputDatasetRel;
		inputToken = "_from_" + shortRefToken(kindTok);
	}
	var modelTok = "";
	if (options.modelBranch && options.modelBranch !== "somata") {
		modelTok = "_m_" + sanitizeSlugPart(options.modelBranch);
	}
	return sanitizeSlugPart(span + "_" + params + inputToken + modelTok + subset);
}

function buildRunSlug(stepId, context) {
	context = context || {};
	var stems = context.sortedStems || listImageSliceStems(context.inputDir || "");
	var span = sliceSpanToken(stems);
	var subset = "";
	if (context.subsetCount && context.subsetCount > 0) {
		subset = "_sub" + String(context.subsetCount);
	}

	if (stepId === "detect") {
		return buildDetectRunSlug({
			confidence: context.confidence,
			tile: context.tile,
			area: context.area,
			eccentricity: context.eccentricity,
			intensityMin: context.intensityMin,
			sortedStems: stems,
			subsetCount: context.subsetCount,
			inputDatasetRel: context.inputDatasetRel,
		});
	}
	if (stepId === "max") {
		var flags = "";
		if (context.dendrite) {
			flags += "_dend";
		}
		if (context.tophat) {
			flags += "_tophat";
		}
		return sanitizeSlugPart(span + flags + subset);
	}
	if (stepId === "sharpen") {
		var sharpenSrc = "";
		if (context.sourceKind && context.sourceKind !== "max" && context.sourceRunRel) {
			sharpenSrc = "_from_" + shortRefToken(context.sourceRunRel);
		}
		return sanitizeSlugPart(
			span +
				"_r" +
				decToken(context.radius) +
				"_a" +
				decToken(context.amount) +
				(context.equalize ? "_eq" : "") +
				sharpenSrc +
				subset,
		);
	}
	if (stepId === "tophat") {
		var topPrefix = "top" + String(Math.round(context.radius != null ? context.radius : 10));
		var gammaTok = "";
		if (context.gamma != null && Math.abs(Number(context.gamma) - 1.25) > 0.001) {
			gammaTok = "_g" + decToken(context.gamma);
		}
		var topSrc = "";
		if (context.sourceKind && context.sourceKind !== "max" && context.sourceRunRel) {
			topSrc = "_from_" + shortRefToken(context.sourceRunRel);
		}
		return sanitizeSlugPart(topPrefix + "_" + span + gammaTok + topSrc + subset);
	}
	if (stepId === "basic") {
		var basicSrc = "";
		if (context.sourceKind && context.sourceKind !== "max" && context.sourceRunRel) {
			basicSrc = "_from_" + shortRefToken(context.sourceRunRel);
		}
		var sm =
			context.smoothness != null ? "_s" + decToken(context.smoothness) : "";
		return sanitizeSlugPart(span + "_basic" + sm + basicSrc + subset);
	}
	if (stepId === "align") {
		var spacing = context.spacing != null ? "_sp" + String(context.spacing) : "";
		var layoutTok = "_auto";
		if (context.whole === "False") {
			layoutTok = "_half";
		} else if (context.whole === "True") {
			layoutTok = "_whole";
		}
		var leg = context.legacy === true || context.legacy === "True" ? "_leg" : "";
		return sanitizeSlugPart(span + spacing + layoutTok + leg + subset);
	}
	if (stepId === "intensity") {
		var mode = context.whole === false || context.whole === "False" ? "_hemi" : "_whole";
		var dapi = context.useDapi ? "_dapi" : "";
		var rc =
			context.regionCount && context.regionCount > 0
				? "_r" + String(context.regionCount)
				: "";
		var layers = context.includeLayers ? "_layers" : "";
		return sanitizeSlugPart(span + mode + dapi + rc + layers + subset);
	}
	if (stepId === "count") {
		var predRef = shortRefToken(context.predictionRunRel);
		var sliceRef = shortRefToken(context.slicesRunRel);
		return sanitizeSlugPart(
			"p_" + predRef + "_s_" + sliceRef + subset,
		);
	}
	if (stepId === "collate") {
		return sanitizeSlugPart("from_" + shortRefToken(context.sourceRunRel) + subset);
	}
	if (stepId === "dual") {
		return sanitizeSlugPart(
			"pkls_" + shortRefToken(context.pklsRunRel) + "_" + span + subset,
		);
	}
	return sanitizeSlugPart(span + subset);
}

function resolveRunLeaf(roleBaseAbs, branch, slug, flat) {
	if (!roleBaseAbs) {
		return "";
	}
	if (flat || !branch || !slug) {
		return roleBaseAbs;
	}
	return path.join(roleBaseAbs, branch, slug);
}

function normalizeRelPath(rel) {
	return String(rel || "")
		.split(/[/\\]+/)
		.filter(Boolean)
		.join("/");
}

function branchForOutputRole(role) {
	var stepId = STEP_BY_OUTPUT_ROLE[role];
	if (!stepId) {
		return "";
	}
	var cfg = RUN_STEP_CONFIG[stepId];
	return cfg && cfg.branch ? cfg.branch : "";
}

/** Collapse ``branch/slug/branch/slug/...`` (repeat while duplicated prefix). */
function dedupeBranchRunRel(rel, branch) {
	rel = normalizeRelPath(rel);
	if (!rel || !branch) {
		return rel;
	}
	var escaped = branch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
	var re = new RegExp("^(" + escaped + "\\/[^/]+)\\/\\1");
	var next = rel;
	do {
		rel = next;
		next = rel.replace(re, "$1");
	} while (next !== rel);
	return rel;
}

/** Collapse nested model-branch folders (Detect: ``somata/a/somata/b`` → ``somata/b``). */
function dedupeModelBranchRunRel(rel) {
	rel = normalizeRelPath(rel);
	if (!rel) {
		return rel;
	}
	var parts = rel.split("/");
	if (parts.length < 3) {
		return rel;
	}
	var branch = parts[0];
	var lastIdx = 0;
	for (var i = 1; i < parts.length; i++) {
		if (parts[i] === branch) {
			lastIdx = i;
		}
	}
	if (lastIdx > 0) {
		return parts.slice(lastIdx).join("/");
	}
	return rel;
}

var MAX_KIND_DIRS = ["max", "sharpen", "tophat", "basic"];

function inferSignalBranchForMaxFamily(activeRel, indirAbs) {
	activeRel = normalizeRelPath(activeRel || "");
	if (activeRel) {
		var parts = activeRel.split("/");
		if (parts.length >= 2 && MAX_KIND_DIRS.indexOf(parts[1]) >= 0) {
			return parts[0];
		}
		if (
			parts.length >= 1 &&
			MAX_KIND_DIRS.indexOf(parts[0]) < 0 &&
			parts[0]
		) {
			return parts[0];
		}
	}
	if (indirAbs) {
		var normIndir = path.resolve(indirAbs);
		var roleBases = ["max", "original_scans"];
		for (var ri = 0; ri < roleBases.length; ri++) {
			var roleBase = resolveRoleBaseAbs(roleBases[ri]);
			if (!roleBase) {
				continue;
			}
			var normBase = path.resolve(roleBase);
			if (
				normIndir === normBase ||
				normIndir.indexOf(normBase + path.sep) === 0
			) {
				var relParts = path
					.relative(normBase, normIndir)
					.split(path.sep)
					.filter(Boolean);
				if (!relParts.length) {
					continue;
				}
				// 03_max/{branch}/max|sharpen|tophat/... or original_scans/{branch}/...
				if (
					roleBases[ri] === "max" &&
					relParts.length >= 2 &&
					MAX_KIND_DIRS.indexOf(relParts[1]) >= 0
				) {
					return relParts[0];
				}
				if (MAX_KIND_DIRS.indexOf(relParts[0]) < 0) {
					return relParts[0];
				}
			}
		}
	}
	return "";
}

function activeRunCompatibleForWrite(stepId, activeRel, branchOverride, signalBranch) {
	activeRel = normalizeRelPath(activeRel || "");
	if (!activeRel) {
		return false;
	}
	if (stepId === "detect") {
		activeRel = dedupeModelBranchRunRel(activeRel);
		return (
			!!branchOverride &&
			(activeRel === branchOverride ||
				activeRel.indexOf(branchOverride + "/") === 0)
		);
	}
	if (stepId === "max" && signalBranch) {
		activeRel = dedupeModelBranchRunRel(activeRel);
		return activeRel.indexOf(signalBranch + "/") === 0;
	}
	var branch =
		branchOverride != null
			? branchOverride
			: RUN_STEP_CONFIG[stepId] && RUN_STEP_CONFIG[stepId].branch;
	if (branch) {
		activeRel = dedupeBranchRunRel(activeRel, branch);
		return activeRel === branch || activeRel.indexOf(branch + "/") === 0;
	}
	return true;
}

function getActiveRunsMap() {
	if (!projectModule().isActive()) {
		return {};
	}
	var proc = projectModule().getProject().processing || projectModule().defaultProcessing();
	if (!proc.active_runs) {
		proc.active_runs = defaultActiveRuns();
	}
	return proc.active_runs;
}

function defaultActiveRuns() {
	var map = {};
	for (var i = 0; i < OUTPUT_ROLES.length; i++) {
		map[OUTPUT_ROLES[i]] = "";
	}
	return map;
}

function migrateActiveRuns(processing) {
	if (!processing) {
		return defaultActiveRuns();
	}
	var runs = Object.assign(defaultActiveRuns(), processing.active_runs || {});
	if (!runs.predictions && processing.active_prediction_run) {
		runs.predictions = normalizeRelPath(processing.active_prediction_run);
	}
	for (var i = 0; i < OUTPUT_ROLES.length; i++) {
		var role = OUTPUT_ROLES[i];
		var branch = branchForOutputRole(role);
		if (role === "predictions" && runs[role]) {
			runs[role] = dedupeModelBranchRunRel(runs[role]);
		} else if (branch && runs[role]) {
			runs[role] = dedupeBranchRunRel(runs[role], branch);
		}
	}
	return runs;
}

function getActiveRunRelForRole(role) {
	if (!isOutputRole(role)) {
		return "";
	}
	var runs = getActiveRunsMap();
	var rel = normalizeRelPath(runs[role] || "");
	if (role === "predictions") {
		return dedupeModelBranchRunRel(rel);
	}
	var branch = branchForOutputRole(role);
	if (branch) {
		rel = dedupeBranchRunRel(rel, branch);
	}
	return rel;
}

function getActiveRunRel(stepId) {
	var cfg = RUN_STEP_CONFIG[stepId];
	if (!cfg) {
		return "";
	}
	return getActiveRunRelForRole(cfg.outputRole);
}

function setActiveRunRel(stepId, rel) {
	if (!projectModule().isActive()) {
		return false;
	}
	var cfg = RUN_STEP_CONFIG[stepId];
	if (!cfg) {
		return false;
	}
	return setActiveRunRelForRole(cfg.outputRole, rel);
}

function setActiveRunRelForRole(role, rel) {
	if (!projectModule().isActive() || !isOutputRole(role)) {
		return false;
	}
	var proj = projectModule().getProject();
	if (!proj.processing) {
		proj.processing = projectModule().defaultProcessing();
	}
	if (!proj.processing.active_runs) {
		proj.processing.active_runs = migrateActiveRuns(proj.processing);
	}
	rel = normalizeRelPath(rel);
	if (role === "predictions") {
		rel = dedupeModelBranchRunRel(rel);
	} else {
		var branch = branchForOutputRole(role);
		if (branch) {
			rel = dedupeBranchRunRel(rel, branch);
		}
	}
	proj.processing.active_runs[role] = rel;
	if (role === "predictions") {
		proj.processing.active_prediction_run = proj.processing.active_runs[role];
	}
	if (role === "max") {
		try {
			if (typeof sessionStorage !== "undefined") {
				sessionStorage.removeItem("masonjar.detect.maxDataset");
				sessionStorage.removeItem("masonjar.intensity.maxDataset");
			}
		} catch (_err) {
			/* ignore */
		}
	}
	projectModule().saveProjectJson();
	return true;
}

function resolveRoleBaseAbs(role) {
	return projectModule().resolveRolePath(role) || "";
}

function resolveActiveRunLeafAbs(role) {
	var base = resolveRoleBaseAbs(role);
	if (!base) {
		return "";
	}
	var rel = getActiveRunRelForRole(role);
	if (!rel) {
		return base;
	}
	return path.join(base, rel.split("/").join(path.sep));
}

function resolveRoleLeafAbs(stepId) {
	var cfg = RUN_STEP_CONFIG[stepId];
	if (!cfg) {
		return "";
	}
	return resolveActiveRunLeafAbs(cfg.outputRole);
}

function resolveInputLeafAbs(role) {
	if (INPUT_ROLES.indexOf(role) >= 0) {
		return resolveRoleBaseAbs(role);
	}
	return resolveActiveRunLeafAbs(role);
}

function resolveActiveBranchLeafAbs(role, branch) {
	if (!projectModule().isActive()) {
		return "";
	}
	var proj = projectModule().getProject();
	var bundleRoot = projectModule().getBundleRoot();
	var roles = (proj && proj.roles) || CANONICAL_ROLES;
	var processing = proj ? proj.processing : null;
	return resolveActiveBranchLeafAbsForBundle(
		bundleRoot,
		roles,
		processing,
		role,
		branch,
	);
}

function resolveInputLeafAbsForStep(stepId, inputRole) {
	if (!projectModule().isActive()) {
		return "";
	}
	var proj = projectModule().getProject();
	var bundleRoot = projectModule().getBundleRoot();
	var roles = (proj && proj.roles) || CANONICAL_ROLES;
	var processing = proj ? proj.processing : null;
	return resolveInputLeafAbsForStepBundle(
		bundleRoot,
		roles,
		processing,
		stepId,
		inputRole,
	);
}

/** Bundle-aware role base directory (does not require an active project). */
function resolveRoleBaseAbsForBundle(bundleRoot, roles, role) {
	if (!bundleRoot || !role) {
		return "";
	}
	var rel = (roles && roles[role]) || CANONICAL_ROLES[role];
	if (!rel) {
		return "";
	}
	return path.isAbsolute(rel) ? rel : path.join(bundleRoot, rel);
}

/** Bundle-aware active run leaf (no `projectModule()`); used by main process. */
function resolveActiveRunLeafAbsForBundle(bundleRoot, roles, processing, role) {
	var base = resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
	if (!base || !isOutputRole(role)) {
		return base;
	}
	var active = migrateActiveRuns(processing || null);
	var rel = (active && active[role]) || "";
	if (!rel) {
		return base;
	}
	return path.join(base, rel.split("/").join(path.sep));
}

/** Bundle-aware branch-preferring leaf (sharpen sees max branch, collate sees count branch). */
function resolveActiveBranchLeafAbsForBundle(
	bundleRoot,
	roles,
	processing,
	role,
	branch,
) {
	var base = resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
	if (!base || !branch) {
		return base;
	}
	var active = migrateActiveRuns(processing || null);
	var activeRel = (active && active[role]) || "";
	if (activeRel) {
		var parts = activeRel.split("/");
		if (parts[0] === branch) {
			return path.join(base, activeRel.split("/").join(path.sep));
		}
	}
	var stepId = STEP_BY_OUTPUT_ROLE[role];
	var runs = discoverOutputRuns(base, stepId, discoveryMaxDepth(stepId));
	for (var i = 0; i < runs.length; i++) {
		var rel = runs[i].rel;
		if (rel === branch || rel.indexOf(branch + "/") === 0) {
			return path.join(base, rel.split("/").join(path.sep));
		}
	}
	var branchDir = path.join(base, branch);
	if (fs.existsSync(branchDir) && hasRunMarkers(branchDir, stepId)) {
		return branchDir;
	}
	if (hasRunMarkers(base, stepId)) {
		return base;
	}
	return base;
}

/** Bundle-aware input leaf for a given step; mirrors single-tool runtime. */
function resolveInputLeafAbsForStepBundle(
	bundleRoot,
	roles,
	processing,
	stepId,
	inputRole,
) {
	if (INPUT_ROLES.indexOf(inputRole) >= 0) {
		return resolveRoleBaseAbsForBundle(bundleRoot, roles, inputRole);
	}
	if (stepId === "sharpen" && inputRole === "max") {
		// Active max-family leaf may be somata/max/... or somata/sharpen/... —
		// do not force legacy branch name "max".
		return resolveActiveRunLeafAbsForBundle(
			bundleRoot,
			roles,
			processing,
			"max",
		);
	}
	if (stepId === "collate" && inputRole === "quantification") {
		return resolveActiveBranchLeafAbsForBundle(
			bundleRoot,
			roles,
			processing,
			"quantification",
			"count",
		);
	}
	return resolveActiveRunLeafAbsForBundle(
		bundleRoot,
		roles,
		processing,
		inputRole,
	);
}

/**
 * Resolve every script-arg path for a step (e.g. `{indir, outdir, ...}`) given a bundle.
 * Output role keys map to active-run leaf; input roles map via `resolveInputLeafAbsForStepBundle`.
 */
function resolvePathsForBundleStep(bundleRoot, roles, processing, stepId) {
	var cfg = RUN_STEP_CONFIG[stepId];
	if (!cfg || !cfg.scriptRoles) {
		return {};
	}
	var out = {};
	var keys = Object.keys(cfg.scriptRoles);
	for (var i = 0; i < keys.length; i++) {
		var key = keys[i];
		var role = cfg.scriptRoles[key];
		if (key === "outdir") {
			out[key] = resolveActiveRunLeafAbsForBundle(
				bundleRoot,
				roles,
				processing,
				role,
			);
		} else {
			out[key] = resolveInputLeafAbsForStepBundle(
				bundleRoot,
				roles,
				processing,
				stepId,
				role,
			);
		}
	}
	return out;
}

function hasRunMarkers(dirPath, stepId) {
	if (!dirPath || !fs.existsSync(dirPath)) {
		return false;
	}
	if (fs.existsSync(path.join(dirPath, "run_manifest.json"))) {
		return true;
	}
	var entries;
	try {
		entries = fs.readdirSync(dirPath);
	} catch (err) {
		return false;
	}
	for (var i = 0; i < entries.length; i++) {
		var n = entries[i];
		if (stepId === "align" && /^annotation_.*\.pkl$/i.test(n)) {
			return true;
		}
		if (stepId === "detect" && /^predictions_.*\.pkl$/i.test(n)) {
			return true;
		}
		if (
			(stepId === "max" || stepId === "sharpen" || stepId === "tophat" || stepId === "basic") &&
			(IMAGE_EXT_RE.test(n) || n.toLowerCase().indexOf(".ome.") !== -1)
		) {
			return true;
		}
		if (stepId === "intensity" && /\.pkl$/i.test(n) && n.indexOf("_") >= 0) {
			return true;
		}
		if (stepId === "count" && n === "count_results.csv") {
			return true;
		}
		if (stepId === "collate" && n === "count_results.csv") {
			return true;
		}
		if (stepId === "dual" && /_dual\.tif$/i.test(n)) {
			return true;
		}
	}
	return false;
}

function discoveryMaxDepth(stepId) {
	if (stepId === "max" || stepId === "sharpen" || stepId === "tophat" || stepId === "basic") {
		return 3;
	}
	return 2;
}

function discoverOutputRuns(roleDir, stepId, maxDepth) {
	maxDepth = maxDepth == null ? discoveryMaxDepth(stepId) : maxDepth;
	var cfg = RUN_STEP_CONFIG[stepId];
	if (!cfg || !roleDir || !fs.existsSync(roleDir)) {
		return [];
	}
	var branch = cfg.branch;
	var found = {};

	function recordLeaf(absDir) {
		var rel = path.relative(roleDir, absDir).split(path.sep).join("/");
		var mt = 0;
		try {
			var st = fs.statSync(absDir);
			mt = st.mtimeMs;
		} catch (err) {}
		if (!found[rel] || mt > found[rel].mtime) {
			found[rel] = { rel: rel, label: rel || "(flat — role root)", mtime: mt };
		}
	}

	function walk(dir, depth, relParts) {
		if (!dir || depth > maxDepth) {
			return;
		}
		if (hasRunMarkers(dir, stepId)) {
			recordLeaf(dir);
		}
		if (depth >= maxDepth) {
			return;
		}
		var entries;
		try {
			entries = fs.readdirSync(dir, { withFileTypes: true });
		} catch (err) {
			return;
		}
		for (var i = 0; i < entries.length; i++) {
			if (!entries[i].isDirectory()) {
				continue;
			}
			var name = entries[i].name;
			if (name === ".masonjar" || name === ".belljar") {
				continue;
			}
			// Detect QC scout leaves must not appear as Cell detection runs
			if (name === "qc_scout" || (relParts && relParts.indexOf("qc_scout") >= 0)) {
				continue;
			}
			walk(path.join(dir, name), depth + 1, relParts.concat(name));
		}
	}

	if (branch) {
		var branchDir = path.join(roleDir, branch);
		if (fs.existsSync(branchDir)) {
			walk(branchDir, 0, [branch]);
		}
	}
	walk(roleDir, 0, []);

	var keys = Object.keys(found).sort(function (a, b) {
		return found[b].mtime - found[a].mtime;
	});
	return keys.map(function (k) {
		return found[k];
	});
}

// In-memory cache of the parsed runs_catalog.json, invalidated by file mtime.
// Reading the catalog (one small stat + JSON parse) replaces the expensive
// recursive discoverOutputRuns() NAS walk on the hub. The catalog is written by
// the background refreshProjectIndex(), so it stays current.
var _runsCatalogCache = { path: null, mtimeMs: -1, data: null };

function readRunsCatalogCached(bundleRoot) {
	try {
		bundleRoot = bundleRoot || projectModule().getBundleRoot();
		if (!bundleRoot) {
			return null;
		}
		var metaFn = projectModule().metaDirPath;
		if (typeof metaFn !== "function") {
			return null;
		}
		var metaDirPath = metaFn(bundleRoot);
		if (!metaDirPath) {
			return null;
		}
		var p = path.join(metaDirPath, "runs_catalog.json");
		var st;
		try {
			st = fs.statSync(p);
		} catch (_e) {
			return null;
		}
		if (_runsCatalogCache.path === p && _runsCatalogCache.mtimeMs === st.mtimeMs) {
			return _runsCatalogCache.data;
		}
		var data = JSON.parse(fs.readFileSync(p, "utf8"));
		_runsCatalogCache = { path: p, mtimeMs: st.mtimeMs, data: data };
		return data;
	} catch (_e) {
		return null;
	}
}

function readRunChoicesFromCatalog(role, bundleRoot) {
	var cat = readRunsCatalogCached(bundleRoot);
	if (
		cat &&
		cat.roles &&
		Object.prototype.hasOwnProperty.call(cat.roles, role) &&
		Array.isArray(cat.roles[role])
	) {
		return cat.roles[role].slice();
	}
	return null;
}

function listRunChoicesForRole(role) {
	var stepId = STEP_BY_OUTPUT_ROLE[role];
	if (!stepId) {
		return [];
	}
	// Prefer the cached catalog; fall back to a live NAS walk only if absent.
	var choices = readRunChoicesFromCatalog(role);
	if (choices == null) {
		var base = resolveRoleBaseAbs(role);
		if (!base) {
			return [];
		}
		choices = discoverOutputRuns(base, stepId, discoveryMaxDepth(stepId));
	}
	var active = getActiveRunRelForRole(role);
	if (!active) {
		return choices;
	}
	for (var i = 0; i < choices.length; i++) {
		if (choices[i].rel === active) {
			return choices;
		}
	}
	// Active run not present in the (possibly cached) choices: verify its leaf
	// exists with a cheap check and prepend it. resolveRoleBaseAbs is recomputed
	// here since the cached path skips the earlier base lookup.
	var activeBase = resolveRoleBaseAbs(role);
	var leaf = activeBase
		? resolveRunLeaf(activeBase, stepId, active, false)
		: null;
	if (leaf && fs.existsSync(leaf) && hasRunMarkers(leaf, stepId)) {
		choices = choices.slice();
		choices.unshift({
			rel: active,
			label: active,
			mtime: 0,
		});
	}
	return choices;
}

function discoverRunChoicesForBundle(bundleRoot, roles, role) {
	var stepId = STEP_BY_OUTPUT_ROLE[role];
	if (!stepId) {
		return [];
	}
	// Prefer the cached catalog (written by the background refresh) to avoid the
	// recursive discoverOutputRuns() NAS walk on every openProject(). Fall back
	// to a live walk only when the catalog is missing the role.
	var cached = readRunChoicesFromCatalog(role, bundleRoot);
	if (cached != null) {
		return cached;
	}
	var base = resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
	if (!base) {
		return [];
	}
	return discoverOutputRuns(base, stepId, discoveryMaxDepth(stepId));
}

function activeRunLeafAbsForBundle(bundleRoot, roles, role, activeRel) {
	var base = resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
	if (!base) {
		return "";
	}
	activeRel = normalizeRelPath(activeRel);
	if (!activeRel) {
		return base;
	}
	return path.join(base, activeRel.split("/").join(path.sep));
}

function isStoredActiveRunValid(bundleRoot, roles, role, activeRel) {
	var stepId = STEP_BY_OUTPUT_ROLE[role];
	if (!stepId) {
		return false;
	}
	var leaf = activeRunLeafAbsForBundle(bundleRoot, roles, role, activeRel);
	if (!leaf || !fs.existsSync(leaf)) {
		return false;
	}
	return hasRunMarkers(leaf, stepId);
}

function reconcileProjectRunsOnOpen(bundleRoot, roles, processing) {
	var runs = migrateActiveRuns(processing || null);
	var changed = false;
	for (var i = 0; i < OUTPUT_ROLES.length; i++) {
		var role = OUTPUT_ROLES[i];
		var choices = discoverRunChoicesForBundle(bundleRoot, roles, role);
		var active = normalizeRelPath(runs[role] || "");
		if (active) {
			if (!isStoredActiveRunValid(bundleRoot, roles, role, active)) {
				runs[role] = "";
				changed = true;
				active = "";
			}
		}
		if (!active && choices.length === 1) {
			var sole = normalizeRelPath(choices[0].rel);
			if (runs[role] !== sole) {
				runs[role] = sole;
				changed = true;
			}
		}
	}
	return { active_runs: runs, changed: changed };
}

/**
 * Move orphaned 03_max/{max|sharpen|tophat}/{slug} leaves under a signal branch.
 * Rename-only (no EXDEV copy). Returns immediately when no orphans exist.
 */
function primarySignalBranchFromSettings(settings) {
	var czi =
		settings && settings.czi_import && typeof settings.czi_import === "object"
			? settings.czi_import
			: null;
	if (!czi) {
		return "";
	}
	var role = String(czi.primary_signal_role || "");
	if (role.indexOf("other:") === 0) {
		return role.slice(6).replace(/[^a-zA-Z0-9_-]/g, "") || "";
	}
	if (role === "signal_somata") {
		return "somata";
	}
	if (role === "signal_nuclei") {
		return "nuclei";
	}
	if (role === "signal_axons") {
		return "axons";
	}
	return "";
}

function pickOrphanMigrateBranch(bundleRoot, roles, runs, settings) {
	var roleBase = resolveRoleBaseAbsForBundle(bundleRoot, roles, "max");
	var branches = [];
	if (roleBase && fs.existsSync(roleBase)) {
		try {
			var ents = fs.readdirSync(roleBase, { withFileTypes: true });
			for (var bi = 0; bi < ents.length; bi++) {
				if (!ents[bi].isDirectory()) {
					continue;
				}
				var bname = ents[bi].name;
				if (MAX_KIND_DIRS.indexOf(bname) >= 0) {
					continue;
				}
				var child = path.join(roleBase, bname);
				for (var kk = 0; kk < MAX_KIND_DIRS.length; kk++) {
					if (fs.existsSync(path.join(child, MAX_KIND_DIRS[kk]))) {
						branches.push(bname);
						break;
					}
				}
			}
		} catch (_err) {
			branches = [];
		}
	}
	branches.sort();
	if (branches.length === 1) {
		return { branch: branches[0], reason: "sole_signal_branch" };
	}
	var activeRel = normalizeRelPath((runs && runs.max) || "");
	if (activeRel) {
		var parts = activeRel.split("/");
		if (
			parts.length >= 2 &&
			MAX_KIND_DIRS.indexOf(parts[0]) < 0 &&
			MAX_KIND_DIRS.indexOf(parts[1]) >= 0
		) {
			return { branch: parts[0], reason: "active_runs.max" };
		}
	}
	var fromCzi = primarySignalBranchFromSettings(settings);
	if (fromCzi) {
		return { branch: fromCzi, reason: "czi_primary_signal" };
	}
	if (branches.length === 0) {
		return { branch: "", reason: "no_signal_branch" };
	}
	return { branch: "", reason: "ambiguous_signal_branches:" + branches.join(",") };
}

function listOrphanMaxFamilyLeaves(roleBase) {
	var orphans = [];
	if (!roleBase || !fs.existsSync(roleBase)) {
		return orphans;
	}
	for (var k = 0; k < MAX_KIND_DIRS.length; k++) {
		var kind = MAX_KIND_DIRS[k];
		var kindDir = path.join(roleBase, kind);
		if (!fs.existsSync(kindDir) || !fs.statSync(kindDir).isDirectory()) {
			continue;
		}
		var entries;
		try {
			entries = fs.readdirSync(kindDir, { withFileTypes: true });
		} catch (_err) {
			continue;
		}
		for (var i = 0; i < entries.length; i++) {
			if (!entries[i].isDirectory()) {
				continue;
			}
			var leafAbs = path.join(kindDir, entries[i].name);
			if (!hasRunMarkers(leafAbs, kind)) {
				continue;
			}
			orphans.push({
				kind: kind,
				slug: entries[i].name,
				abs: leafAbs,
				orphanRel: kind + "/" + entries[i].name,
			});
		}
	}
	return orphans;
}

function migrateOrphanMaxFamilyLeaves(bundleRoot, roles, processing, settings) {
	var runs = migrateActiveRuns(processing || null);
	var roleBase = resolveRoleBaseAbsForBundle(bundleRoot, roles, "max");
	var orphans = listOrphanMaxFamilyLeaves(roleBase);
	if (!orphans.length) {
		return {
			changed: false,
			moved: [],
			skippedReason: "",
			active_runs: runs,
			messages: [],
		};
	}
	var pick = pickOrphanMigrateBranch(bundleRoot, roles, runs, settings);
	if (!pick.branch) {
		var skipMsg =
			"[pipeline_runs] Skipping orphan max-family migrate (" +
			pick.reason +
			"); re-run sharpen/tophat after updating Mason Jar, or set a single signal branch.";
		console.warn(skipMsg);
		return {
			changed: false,
			moved: [],
			skippedReason: pick.reason,
			active_runs: runs,
			messages: [skipMsg],
		};
	}
	var messages = [
		"[pipeline_runs] Repairing misplaced max/sharpen/tophat folders → " +
			pick.branch +
			" (" +
			pick.reason +
			")…",
	];
	console.log(messages[0]);
	var moved = [];
	var changed = false;
	for (var i = 0; i < orphans.length; i++) {
		var o = orphans[i];
		var destParent = path.join(roleBase, pick.branch, o.kind);
		var destAbs = path.join(destParent, o.slug);
		var destRel = pick.branch + "/" + o.kind + "/" + o.slug;
		if (path.resolve(o.abs) === path.resolve(destAbs)) {
			continue;
		}
		if (fs.existsSync(destAbs)) {
			var clash =
				"[pipeline_runs] Skip orphan " +
				o.orphanRel +
				": destination already exists " +
				destRel;
			console.warn(clash);
			messages.push(clash);
			continue;
		}
		try {
			fs.mkdirSync(destParent, { recursive: true });
			fs.renameSync(o.abs, destAbs);
		} catch (err) {
			var code = err && err.code ? String(err.code) : "";
			var fail =
				"[pipeline_runs] Failed to rename " +
				o.orphanRel +
				" → " +
				destRel +
				(code === "EXDEV"
					? " (cross-volume; not copying — re-run the tool instead)"
					: ": " + (err && err.message ? err.message : String(err)));
			console.warn(fail);
			messages.push(fail);
			continue;
		}
		moved.push({ from: o.orphanRel, to: destRel });
		changed = true;
		var done = "[pipeline_runs] Moved " + o.orphanRel + " → " + destRel;
		console.log(done);
		messages.push(done);
		if (normalizeRelPath(runs.max || "") === o.orphanRel) {
			runs.max = destRel;
		}
	}
	// Remove empty orphan kind dirs when possible
	for (var ki = 0; ki < MAX_KIND_DIRS.length; ki++) {
		var emptyKind = path.join(roleBase, MAX_KIND_DIRS[ki]);
		try {
			if (fs.existsSync(emptyKind) && fs.readdirSync(emptyKind).length === 0) {
				fs.rmdirSync(emptyKind);
			}
		} catch (_rm) {
			/* ignore */
		}
	}
	return {
		changed: changed,
		moved: moved,
		skippedReason: "",
		active_runs: runs,
		messages: messages,
	};
}

function ensureDefaultActiveRunForRole(role) {
	if (!projectModule().isActive() || !isOutputRole(role)) {
		return;
	}
	if (getActiveRunRelForRole(role)) {
		return;
	}
	var choices = listRunChoicesForRole(role);
	if (choices.length) {
		setActiveRunRelForRole(role, choices[0].rel);
	}
}

function buildRunsCatalog(bundleRoot, roles) {
	var catalog = { generated_at: new Date().toISOString(), roles: {} };
	for (var i = 0; i < OUTPUT_ROLES.length; i++) {
		var role = OUTPUT_ROLES[i];
		var stepId = STEP_BY_OUTPUT_ROLE[role];
		var rel = roles[role] || CANONICAL_ROLES[role];
		var dir = path.isAbsolute(rel) ? rel : path.join(bundleRoot, rel);
		catalog.roles[role] = discoverOutputRuns(dir, stepId, discoveryMaxDepth(stepId));
	}
	return catalog;
}

function writeRunsCatalog(bundleRoot, metaDirPath, roles) {
	var catalog = buildRunsCatalog(bundleRoot, roles);
	var outPath = path.join(metaDirPath, "runs_catalog.json");
	fs.writeFileSync(outPath, JSON.stringify(catalog, null, 2), "utf8");
	return outPath;
}

function runRelVariants(role, rel) {
	rel = normalizeRelPath(rel);
	if (!rel) {
		return [];
	}
	var branch = branchForOutputRole(role);
	var variants = [rel];
	if (branch) {
		var collapsed = dedupeBranchRunRel(rel, branch);
		if (collapsed && variants.indexOf(collapsed) < 0) {
			variants.push(collapsed);
		}
		if (collapsed && collapsed.indexOf(branch + "/") === 0) {
			var inner = collapsed.slice(branch.length + 1);
			var doubled = branch + "/" + inner + "/" + branch + "/" + inner;
			if (variants.indexOf(doubled) < 0) {
				variants.push(doubled);
			}
		}
	}
	return variants;
}

function isPathUnderRoot(rootAbs, targetAbs) {
	var root = path.resolve(rootAbs);
	var target = path.resolve(targetAbs);
	if (target === root) {
		return true;
	}
	var prefix = root + path.sep;
	return target.length > prefix.length && target.slice(0, prefix.length) === prefix;
}

function isSafeRunDeleteTarget(bundleRoot, roleBaseAbs, targetAbs) {
	if (!bundleRoot || !roleBaseAbs || !targetAbs) {
		return false;
	}
	var roleBase = path.resolve(roleBaseAbs);
	var target = path.resolve(targetAbs);
	if (target === roleBase) {
		return false;
	}
	if (!isPathUnderRoot(roleBase, target)) {
		return false;
	}
	return isPathUnderRoot(bundleRoot, target);
}

function collectRunDeleteTargets(bundleRoot, roles, role, rel) {
	rel = normalizeRelPath(rel);
	if (!rel || !bundleRoot || !isOutputRole(role)) {
		return [];
	}
	var roleBaseAbs = resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
	if (!roleBaseAbs || !fs.existsSync(roleBaseAbs)) {
		return [];
	}
	var variants = runRelVariants(role, rel);
	var seen = {};
	var targets = [];
	for (var v = 0; v < variants.length; v++) {
		var variant = variants[v];
		var abs = path.join(roleBaseAbs, variant.split("/").join(path.sep));
		if (seen[abs] || !fs.existsSync(abs)) {
			continue;
		}
		try {
			if (!fs.statSync(abs).isDirectory()) {
				continue;
			}
		} catch (err) {
			continue;
		}
		if (!isSafeRunDeleteTarget(bundleRoot, roleBaseAbs, abs)) {
			continue;
		}
		seen[abs] = true;
		targets.push({
			abs: abs,
			rel: variant,
			relToBundle: path.relative(bundleRoot, abs).split(path.sep).join("/"),
		});
	}
	return targets;
}

function buildRunDeleteConfirmMessage(role, rel, targets) {
	if (!targets.length) {
		return "";
	}
	var lines = [
		"Delete this pipeline output folder from the project bundle?",
		"",
		"Role: " + role,
		"Task: " + rel,
		"",
		"Folder(s) to remove:",
	];
	for (var i = 0; i < targets.length; i++) {
		lines.push("  • " + targets[i].relToBundle);
		lines.push("    " + targets[i].abs);
	}
	lines.push("");
	lines.push("This cannot be undone. Files on disk will be deleted.");
	return lines.join("\n");
}

function activeRunMatchesDeleted(activeRel, deletedRels) {
	activeRel = normalizeRelPath(activeRel);
	if (!activeRel) {
		return false;
	}
	for (var i = 0; i < deletedRels.length; i++) {
		if (normalizeRelPath(deletedRels[i]) === activeRel) {
			return true;
		}
	}
	return false;
}

function pruneEmptyRunParents(roleBaseAbs, deletedAbs, branch) {
	var current = path.resolve(deletedAbs);
	var stopAt = path.resolve(roleBaseAbs);
	if (branch) {
		var branchDir = path.join(stopAt, branch);
		if (fs.existsSync(branchDir)) {
			stopAt = path.resolve(branchDir);
		}
	}
	while (current !== stopAt && isPathUnderRoot(stopAt, current)) {
		var parent = path.dirname(current);
		if (parent === current) {
			break;
		}
		try {
			var entries = fs.readdirSync(parent);
			if (entries.length > 0) {
				break;
			}
			fs.rmdirSync(parent);
		} catch (err) {
			break;
		}
		current = parent;
	}
}

function removeRunForRole(role, rel, options) {
	options = options || {};
	if (!projectModule().isActive() || !isOutputRole(role)) {
		return { ok: false, error: "no active project" };
	}
	rel = normalizeRelPath(rel);
	if (!rel) {
		return { ok: false, error: "cannot delete flat role root" };
	}
	var bundleRoot = options.bundleRoot || projectModule().getBundleRoot();
	var proj = projectModule().getProject();
	var roles = (proj && proj.roles) || CANONICAL_ROLES;
	var targets = collectRunDeleteTargets(bundleRoot, roles, role, rel);
	if (!targets.length) {
		return { ok: false, error: "no deletable run folder found for " + rel };
	}
	var roleBaseAbs = resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
	var stepId = STEP_BY_OUTPUT_ROLE[role];
	var cfg = stepId ? RUN_STEP_CONFIG[stepId] : null;
	var branch = cfg && cfg.branch;
	var deletedRels = [];
	for (var t = 0; t < targets.length; t++) {
		try {
			fs.rmSync(targets[t].abs, { recursive: true, force: true });
			deletedRels.push(targets[t].rel);
			pruneEmptyRunParents(roleBaseAbs, targets[t].abs, branch);
		} catch (err) {
			return {
				ok: false,
				error: String(err.message || err),
				deleted: deletedRels,
			};
		}
	}
	var activeRel = getActiveRunRelForRole(role);
	var variantRels = runRelVariants(role, rel);
	if (activeRunMatchesDeleted(activeRel, variantRels)) {
		var remaining = listRunChoicesForRole(role);
		var nextRel = remaining.length ? remaining[0].rel : "";
		setActiveRunRelForRole(role, nextRel);
	}
	var metaPath = options.metaDirPath;
	if (!metaPath && projectModule().metaDirPath) {
		metaPath = projectModule().metaDirPath(bundleRoot);
	}
	if (metaPath) {
		writeRunsCatalog(bundleRoot, metaPath, roles);
	}
	return { ok: true, deleted: deletedRels, targets: targets };
}

function computeFinalOutputPath(stepId, context) {
	context = context || {};
	return resolveStepOutputPath(stepId, context);
}

/**
 * Resolve the absolute output folder for a pipeline step run.
 * Project mode always writes from the role base (never the active-run leaf).
 * @param {string} stepId
 * @param {object} [options]
 * @param {string} [options.slug]
 * @param {boolean} [options.flat]
 * @param {string} [options.runMode] overwrite|merge|skip
 * @param {string} [options.branchOverride] detect model branch
 * @param {string} [options.signalBranch] max-family signal branch (somata, …)
 * @param {string} [options.indirAbs] infer signal branch from original_scans path
 * @param {string} [options.legacyOutBase] non-project DOM outdir fallback
 */
function resolveStepOutputPath(stepId, options) {
	options = options || {};
	var cfg = RUN_STEP_CONFIG[stepId];
	if (!cfg || !cfg.outputRole) {
		return "";
	}
	var slug = options.slug || "";
	var flat = !!options.flat;
	var runMode = options.runMode || "merge";
	var branchOverride =
		options.branchOverride != null ? options.branchOverride : cfg.branch;

	if (!projectModule().isActive()) {
		var legacyBase = options.legacyOutBase || options.outBase || "";
		if (!legacyBase) {
			return "";
		}
		return resolveRunLeaf(legacyBase, branchOverride, slug, flat);
	}

	if (flat) {
		return resolveRoleBaseAbs(cfg.outputRole);
	}

	var writeBase = resolveRoleBaseAbs(cfg.outputRole);
	var signalBranch = options.signalBranch || "";
	if (stepId === "max") {
		signalBranch =
			signalBranch ||
			inferSignalBranchForMaxFamily(
				getActiveRunRelForRole("max"),
				options.indirAbs,
			);
		if (signalBranch) {
			var maxDatasets = require("./max_datasets");
			writeBase = maxDatasets.branchRootAbs(
				projectModule().getBundleRoot(),
				signalBranch,
			);
		}
	}
	if (stepId === "detect") {
		signalBranch =
			signalBranch ||
			inferSignalBranchForMaxFamily("", options.indirAbs);
		if (signalBranch) {
			branchOverride = signalBranch;
		}
	}

	if (runMode === "overwrite") {
		var activeRel = getActiveRunRel(stepId);
		if (
			activeRel &&
			activeRunCompatibleForWrite(
				stepId,
				activeRel,
				branchOverride,
				signalBranch,
			)
		) {
			var activeLeaf = resolveActiveRunLeafAbs(cfg.outputRole);
			if (activeLeaf && fs.existsSync(activeLeaf)) {
				return activeLeaf;
			}
		}
	}

	return resolveRunLeaf(writeBase, branchOverride, slug, false);
}

function relFromRoleBase(stepId, finalOutAbs) {
	var cfg = RUN_STEP_CONFIG[stepId];
	if (!cfg || !finalOutAbs) {
		return "";
	}
	var base = resolveRoleBaseAbs(cfg.outputRole);
	if (!base) {
		return "";
	}
	return normalizeRelPath(path.relative(base, finalOutAbs));
}

module.exports = {
	RUN_STEP_CONFIG: RUN_STEP_CONFIG,
	CANONICAL_ROLES: CANONICAL_ROLES,
	OUTPUT_ROLES: OUTPUT_ROLES,
	INPUT_ROLES: INPUT_ROLES,
	STEP_BY_OUTPUT_ROLE: STEP_BY_OUTPUT_ROLE,
	isOutputRole: isOutputRole,
	sanitizeSlugPart: sanitizeSlugPart,
	sliceSpanToken: sliceSpanToken,
	buildDetectRunSlug: buildDetectRunSlug,
	buildRunSlug: buildRunSlug,
	resolveRunLeaf: resolveRunLeaf,
	resolveStepOutputPath: resolveStepOutputPath,
	dedupeBranchRunRel: dedupeBranchRunRel,
	dedupeModelBranchRunRel: dedupeModelBranchRunRel,
	inferSignalBranchForMaxFamily: inferSignalBranchForMaxFamily,
	discoveryMaxDepth: discoveryMaxDepth,
	discoverOutputRuns: discoverOutputRuns,
	defaultActiveRuns: defaultActiveRuns,
	migrateActiveRuns: migrateActiveRuns,
	getActiveRunRel: getActiveRunRel,
	getActiveRunRelForRole: getActiveRunRelForRole,
	setActiveRunRel: setActiveRunRel,
	setActiveRunRelForRole: setActiveRunRelForRole,
	resolveRoleBaseAbs: resolveRoleBaseAbs,
	resolveActiveRunLeafAbs: resolveActiveRunLeafAbs,
	resolveRoleLeafAbs: resolveRoleLeafAbs,
	resolveInputLeafAbs: resolveInputLeafAbs,
	resolveInputLeafAbsForStep: resolveInputLeafAbsForStep,
	resolveActiveBranchLeafAbs: resolveActiveBranchLeafAbs,
	resolveRoleBaseAbsForBundle: resolveRoleBaseAbsForBundle,
	resolveActiveRunLeafAbsForBundle: resolveActiveRunLeafAbsForBundle,
	resolveActiveBranchLeafAbsForBundle: resolveActiveBranchLeafAbsForBundle,
	resolveInputLeafAbsForStepBundle: resolveInputLeafAbsForStepBundle,
	resolvePathsForBundleStep: resolvePathsForBundleStep,
	listRunChoicesForRole: listRunChoicesForRole,
	listImageSliceStems: listImageSliceStems,
	listImageSliceFiles: listImageSliceFiles,
	listImageSliceRecords: listImageSliceRecords,
	ensureDefaultActiveRunForRole: ensureDefaultActiveRunForRole,
	discoverRunChoicesForBundle: discoverRunChoicesForBundle,
	isStoredActiveRunValid: isStoredActiveRunValid,
	reconcileProjectRunsOnOpen: reconcileProjectRunsOnOpen,
	migrateOrphanMaxFamilyLeaves: migrateOrphanMaxFamilyLeaves,
	listOrphanMaxFamilyLeaves: listOrphanMaxFamilyLeaves,
	buildRunsCatalog: buildRunsCatalog,
	writeRunsCatalog: writeRunsCatalog,
	computeFinalOutputPath: computeFinalOutputPath,
	relFromRoleBase: relFromRoleBase,
	hasRunMarkers: hasRunMarkers,
	collectRunDeleteTargets: collectRunDeleteTargets,
	buildRunDeleteConfirmMessage: buildRunDeleteConfirmMessage,
	removeRunForRole: removeRunForRole,
	isSafeRunDeleteTarget: isSafeRunDeleteTarget,
};
