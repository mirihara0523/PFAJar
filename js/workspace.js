"use strict";

var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;

var branding = require("./branding");
var STORAGE_KEY = branding.WORKSPACE_KEY;

var COUNTING_FOLDER_DEFS = [
	{ key: "dapi", prefix: "00", keywords: ["dapi"] },
	{ key: "slices", prefix: "01", keywords: ["slices", "slice"] },
	{ key: "max", prefix: "03", keywords: ["max"] },
	{ key: "predictions", prefix: "05", keywords: ["predictions", "prediction", "pred"] },
	{ key: "quantification", prefix: "06", keywords: ["quantification", "quant"] },
	{ key: "pkls", prefix: "07", keywords: ["pkl", "pkls"] },
	{ key: "dual", prefix: "08", keywords: ["dual"] },
];

var FOLDER_LABELS = {
	dapi: "00-DAPI",
	slices: "01-slices",
	max: "03-max",
	predictions: "05-predictions",
	quantification: "06-quantification",
	pkls: "07-pkls",
	dual: "08-dual",
};

var TOOL_PRESETS = {
	max: { indir: "originalScans", outdir: "max" },
	sharpen: { indir: "max", outdir: "max" },
	dapi_cleanup: { indir: "dapi" },
	align: { indir: "dapi", outdir: "slices" },
	adjust: { imdir: "dapi", annodir: "slices" },
	detect: { indir: "max", outdir: "predictions" },
	count: { preddir: "predictions", annodir: "slices", outdir: "quantification" },
	collate: { outdir: "quantification" },
	intensity: { indir: "max", annodir: "slices", outdir: "pkls", dapidir: "dapi" },
	dual: { indir: "pkls", outdir: "dual" },
};

var projectModule = null;

function getProjectModule() {
	if (!projectModule) {
		try {
			projectModule = require("./project");
		} catch (err) {
			projectModule = null;
		}
	}
	return projectModule;
}

var workspace = {
	brainRoot: "",
	countingRoot: "",
	originalScans: "",
	paths: {},
};

function normalizeFolderName(name) {
	return name
		.toLowerCase()
		.replace(/[\s\-_]+/g, "");
}

function matchesFolderDef(entryName, def) {
	var norm = normalizeFolderName(entryName);
	if (norm.indexOf(def.prefix) === 0) {
		return true;
	}
	for (var i = 0; i < def.keywords.length; i++) {
		if (norm.indexOf(def.keywords[i]) !== -1) {
			return true;
		}
	}
	return false;
}

function findChildDir(parentDir, pattern) {
	if (!parentDir || !fs.existsSync(parentDir)) {
		return "";
	}
	var entries;
	try {
		entries = fs.readdirSync(parentDir, { withFileTypes: true });
	} catch (err) {
		return "";
	}
	for (var i = 0; i < entries.length; i++) {
		var entry = entries[i];
		if (!entry.isDirectory()) {
			continue;
		}
		if (pattern.test(entry.name.trim())) {
			return path.join(parentDir, entry.name);
		}
	}
	return "";
}

function resolveCountingFolders(countingRoot) {
	var resolved = {};
	if (!countingRoot || !fs.existsSync(countingRoot)) {
		return resolved;
	}
	var entries;
	try {
		entries = fs.readdirSync(countingRoot, { withFileTypes: true });
	} catch (err) {
		return resolved;
	}
	for (var d = 0; d < COUNTING_FOLDER_DEFS.length; d++) {
		var def = COUNTING_FOLDER_DEFS[d];
		if (resolved[def.key]) {
			continue;
		}
		for (var i = 0; i < entries.length; i++) {
			var entry = entries[i];
			if (!entry.isDirectory()) {
				continue;
			}
			if (matchesFolderDef(entry.name, def)) {
				resolved[def.key] = path.join(countingRoot, entry.name);
				break;
			}
		}
	}
	var fi = null;
	try {
		fi = require("./file_index");
	} catch (err) {
		fi = null;
	}
	if (fi && resolved.predictions) {
		var scan = fi.resolvePredictionsScan(resolved.predictions, 2);
		resolved.predictions = scan.resolvedPath;
		if (scan.warning) {
			resolved._predictionsScanWarning = scan.warning;
		}
	}
	return resolved;
}

function loadWorkspace() {
	try {
		var raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) {
			raw = localStorage.getItem(branding.LEGACY_WORKSPACE_KEY);
		}
		if (!raw) {
			return workspace;
		}
		var parsed = JSON.parse(raw);
		workspace.brainRoot = parsed.brainRoot || "";
		workspace.countingRoot = parsed.countingRoot || "";
		workspace.originalScans = parsed.originalScans || "";
		workspace.paths = parsed.paths || {};
	} catch (err) {
		/* keep defaults */
	}
	return workspace;
}

function saveWorkspace() {
	localStorage.setItem(
		STORAGE_KEY,
		JSON.stringify({
			brainRoot: workspace.brainRoot,
			countingRoot: workspace.countingRoot,
			originalScans: workspace.originalScans,
			paths: workspace.paths,
		}),
	);
}

function scanBrainRoot(root) {
	workspace.brainRoot = root || "";
	workspace.countingRoot = "";
	workspace.originalScans = "";
	workspace.paths = {};

	if (!root || !fs.existsSync(root)) {
		saveWorkspace();
		return workspace;
	}

	workspace.countingRoot = findChildDir(root, /^counting$/i);
	workspace.originalScans = findChildDir(root, /^original\s*scans$/i);
	if (workspace.countingRoot) {
		workspace.paths = resolveCountingFolders(workspace.countingRoot);
	}

	saveWorkspace();
	return workspace;
}

function resolveLogicalPath(logicalKey, options) {
	if (!logicalKey) {
		return "";
	}
	var proj = getProjectModule();
	if (proj && proj.isActive()) {
		var fromProject = proj.resolveLogicalPath(logicalKey, options);
		if (fromProject) {
			return fromProject;
		}
	}
	if (logicalKey === "originalScans") {
		return workspace.originalScans || "";
	}
	if (logicalKey === "brainRoot") {
		return workspace.brainRoot || "";
	}
	if (logicalKey === "countingRoot") {
		return workspace.countingRoot || "";
	}
	return (workspace.paths && workspace.paths[logicalKey]) || "";
}

function presetFieldPurpose(fieldName) {
	return fieldName === "outdir" ? "output" : "input";
}

function getPreset(toolId) {
	var fieldMap = TOOL_PRESETS[toolId];
	if (!fieldMap) {
		return {};
	}
	var preset = {};
	var keys = Object.keys(fieldMap);
	for (var i = 0; i < keys.length; i++) {
		var field = keys[i];
		var logicalKey = fieldMap[field];
		var purpose = presetFieldPurpose(field);
		var resolved = resolveLogicalPath(logicalKey, { purpose: purpose });
		if (resolved) {
			preset[field] = resolved;
		}
	}
	return preset;
}

function applyPreset(toolId) {
	loadWorkspace();
	var preset = getPreset(toolId);
	var forceProject = !!(getProjectModule() && getProjectModule().isActive());
	var fields = Object.keys(preset);
	for (var i = 0; i < fields.length; i++) {
		var fieldName = fields[i];
		var el = document.getElementById(fieldName);
		if (el && (forceProject || !el.value)) {
			el.value = preset[fieldName];
		}
		if (el && forceProject && preset[fieldName]) {
			el.setAttribute("readonly", "readonly");
			el.classList.add("project-bound");
		}
	}

	if (toolId === "intensity") {
		var usedapi = document.getElementById("usedapi");
		var dapidir = document.getElementById("dapidir");
		if (usedapi && dapidir && preset.dapidir) {
			if (!dapidir.value) {
				usedapi.checked = true;
				dapidir.disabled = false;
			}
		}
	}

	return preset;
}

function getScanStatusMessage() {
	loadWorkspace();
	var proj = getProjectModule();
	if (proj && proj.isActive()) {
		return proj.getStatusMessage();
	}
	if (!workspace.brainRoot) {
		return "No brain folder selected.";
	}
	if (!fs.existsSync(workspace.brainRoot)) {
		return "Brain folder not found on disk.";
	}
	var found = [];
	var missing = [];
	var keys = Object.keys(FOLDER_LABELS);
	for (var i = 0; i < keys.length; i++) {
		var key = keys[i];
		if (workspace.paths[key]) {
			found.push(FOLDER_LABELS[key]);
		} else {
			missing.push(FOLDER_LABELS[key]);
		}
	}
	var parts = [];
	if (workspace.countingRoot) {
		parts.push("counting");
	}
	if (workspace.originalScans) {
		parts.push("original scans");
	}
	if (found.length) {
		parts.push("found " + found.join(", "));
	}
	if (missing.length) {
		return (
			(parts.length ? parts.join("; ") + ". " : "") +
			"Missing: " +
			missing.join(", ") +
			" — set paths manually on each tool."
		);
	}
	if (!workspace.countingRoot) {
		return "No counting/ folder found — set paths manually on each tool.";
	}
	return parts.length ? parts.join("; ") + "." : "Brain folder scanned.";
}

function bindPathPicker(inputEl, tag, logicalKey, isFile) {
	if (!inputEl) {
		return;
	}
	if (getProjectModule() && getProjectModule().isActive()) {
		return;
	}
	inputEl.addEventListener("click", function () {
		if (inputEl.disabled) {
			return;
		}
		var defaultPath = resolveLogicalPath(logicalKey);
		var payload = defaultPath ? { tag: tag, defaultPath: defaultPath } : tag;
		ipc.once("returnPath", function (event, response) {
			var responseTag = response[1];
			if (typeof responseTag === "object" && responseTag !== null && responseTag.tag) {
				responseTag = responseTag.tag;
			}
			if (responseTag === tag) {
				inputEl.value = response[0];
			}
		});
		ipc.send(isFile ? "openFileDialog" : "openDialog", payload);
	});
}

function chooseBrainFolder(callback) {
	var dialogs = require("./dialogs");
	loadWorkspace();
	var defaultPath = workspace.brainRoot || "";
	dialogs
		.pickDirectory({ tag: "brainRoot", defaultPath: defaultPath })
		.then(function (selected) {
			if (selected) {
				scanBrainRoot(selected);
			}
			if (typeof callback === "function") {
				callback(workspace);
			}
		});
}

module.exports = {
	STORAGE_KEY: STORAGE_KEY,
	loadWorkspace: loadWorkspace,
	saveWorkspace: saveWorkspace,
	scanBrainRoot: scanBrainRoot,
	getPreset: getPreset,
	applyPreset: applyPreset,
	bindPathPicker: bindPathPicker,
	resolveLogicalPath: resolveLogicalPath,
	getScanStatusMessage: getScanStatusMessage,
	chooseBrainFolder: chooseBrainFolder,
	getWorkspace: function () {
		return workspace;
	},
};
