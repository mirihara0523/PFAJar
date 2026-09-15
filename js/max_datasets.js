"use strict";

var fs = require("fs");
var path = require("path");
var pipelineRuns = require("./pipeline_runs");
var project = require("./project");

var KIND_DIRS = ["max", "sharpen", "tophat", "basic"];
var KIND_LABELS = {
	max: "Max projection",
	sharpen: "Sharpen",
	tophat: "Top-hat",
	basic: "BaSiC shading",
};

var IMAGE_EXT_RE = /\.(tif|tiff|png|jpe?g)$/i;

function maxRoleBase(bundleRoot) {
	if (!bundleRoot) {
		return "";
	}
	var roles = project.isActive() && project.getProject()
		? project.getProject().roles
		: pipelineRuns.CANONICAL_ROLES;
	return pipelineRuns.resolveRoleBaseAbsForBundle(bundleRoot, roles, "max");
}

function branchRootAbs(bundleRoot, branch) {
	var base = maxRoleBase(bundleRoot);
	if (!base) {
		return "";
	}
	if (branch) {
		return path.join(base, branch);
	}
	return base;
}

function dirHasImageMarkers(dirPath, stepId) {
	if (!dirPath || !fs.existsSync(dirPath)) {
		return false;
	}
	if (pipelineRuns.hasRunMarkers(dirPath, stepId)) {
		return true;
	}
	try {
		var entries = fs.readdirSync(dirPath);
		for (var i = 0; i < entries.length; i++) {
			var n = entries[i];
			if (IMAGE_EXT_RE.test(n) || n.toLowerCase().indexOf(".ome.") !== -1) {
				return true;
			}
		}
	} catch (_err) {
		return false;
	}
	return false;
}

function collectKindLeaves(kindDir, stepId, branchPrefix) {
	var out = [];
	if (!kindDir || !fs.existsSync(kindDir)) {
		return out;
	}
	var entries;
	try {
		entries = fs.readdirSync(kindDir, { withFileTypes: true });
	} catch (_err) {
		return out;
	}
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isDirectory()) {
			continue;
		}
		var leafAbs = path.join(kindDir, entries[i].name);
		if (!dirHasImageMarkers(leafAbs, stepId)) {
			continue;
		}
		var relParts = [];
		if (branchPrefix) {
			relParts.push(branchPrefix);
		}
		relParts.push(stepId, entries[i].name);
		var rel = relParts.join("/");
		var mt = 0;
		try {
			mt = fs.statSync(leafAbs).mtimeMs;
		} catch (_st) {}
		var kind = stepId;
		out.push({
			kind: kind,
			rel: rel,
			abs: leafAbs,
			mtime: mt,
			label: formatDatasetLabel(kind, rel),
		});
	}
	return out;
}

function formatDatasetLabel(kind, rel) {
	var prefix = KIND_LABELS[kind] || kind;
	if (!rel) {
		return prefix + " (role root)";
	}
	return prefix + " — " + rel;
}

function listSignalBranches(bundleRoot) {
	var base = maxRoleBase(bundleRoot);
	if (!base || !fs.existsSync(base)) {
		return [];
	}
	var branches = [];
	var entries;
	try {
		entries = fs.readdirSync(base, { withFileTypes: true });
	} catch (_err) {
		return [];
	}
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isDirectory()) {
			continue;
		}
		var name = entries[i].name;
		if (KIND_DIRS.indexOf(name) >= 0) {
			continue;
		}
		var child = path.join(base, name);
		for (var k = 0; k < KIND_DIRS.length; k++) {
			if (fs.existsSync(path.join(child, KIND_DIRS[k]))) {
				branches.push(name);
				break;
			}
		}
	}
	branches.sort();
	return branches;
}

function listDatasetsForBranch(bundleRoot, branch) {
	var root = branchRootAbs(bundleRoot, branch);
	if (!root || !fs.existsSync(root)) {
		return [];
	}
	var out = [];
	for (var k = 0; k < KIND_DIRS.length; k++) {
		var kind = KIND_DIRS[k];
		var kindDir = path.join(root, kind);
		var leaves = collectKindLeaves(kindDir, kind, branch || "");
		for (var i = 0; i < leaves.length; i++) {
			out.push(leaves[i]);
		}
	}
	if (!branch && dirHasImageMarkers(root, "max")) {
		var mtFlat = 0;
		try {
			mtFlat = fs.statSync(root).mtimeMs;
		} catch (_st) {}
		out.push({
			kind: "max",
			rel: "",
			abs: root,
			mtime: mtFlat,
			label: formatDatasetLabel("max", "(flat — role root)"),
		});
	}
	out.sort(function (a, b) {
		if (a.kind !== b.kind) {
			return KIND_DIRS.indexOf(a.kind) - KIND_DIRS.indexOf(b.kind);
		}
		return (b.mtime || 0) - (a.mtime || 0);
	});
	return out;
}

function defaultDatasetForBranch(bundleRoot, branch, opts) {
	opts = opts || {};
	var preferKind = opts.preferKind || "max";
	var datasets = listDatasetsForBranch(bundleRoot, branch);
	if (!datasets.length) {
		return null;
	}
	var savedRel = opts.savedRel || "";
	var activeRel = "";
	if (project.isActive()) {
		var proj = project.getProject();
		var processing = proj ? proj.processing : null;
		activeRel = pipelineRuns.migrateActiveRuns(processing || null).max || "";
	}
	if (activeRel) {
		for (var j = 0; j < datasets.length; j++) {
			if (datasets[j].rel === activeRel) {
				return datasets[j];
			}
		}
		if (branch && activeRel.indexOf(branch + "/") === 0) {
			for (var a = 0; a < datasets.length; a++) {
				if (datasets[a].rel === activeRel) {
					return datasets[a];
				}
			}
		}
	}
	if (savedRel) {
		for (var i = 0; i < datasets.length; i++) {
			if (datasets[i].rel === savedRel) {
				return datasets[i];
			}
		}
	}
	var preferred = [];
	for (var p = 0; p < datasets.length; p++) {
		if (datasets[p].kind === preferKind) {
			preferred.push(datasets[p]);
		}
	}
	var pool = preferred.length ? preferred : datasets;
	var best = pool[0];
	for (var b = 1; b < pool.length; b++) {
		if ((pool[b].mtime || 0) > (best.mtime || 0)) {
			best = pool[b];
		}
	}
	return best;
}

function defaultBranchForDetectMethod(method) {
	if (method === "nuclei") {
		return "nuclei";
	}
	return "somata";
}

function parseSourceRunRel(datasetRel, branch) {
	if (!datasetRel) {
		return { source_kind: "max", source_run_rel: "" };
	}
	var parts = datasetRel.split("/").filter(Boolean);
	if (!parts.length) {
		return { source_kind: "max", source_run_rel: "" };
	}
	var kind = parts[0];
	if (KIND_DIRS.indexOf(kind) >= 0) {
		return {
			source_kind: kind,
			source_run_rel: datasetRel,
		};
	}
	if (branch && parts[0] === branch) {
		return {
			source_kind: parts[1] || "max",
			source_run_rel: parts.slice(1).join("/"),
		};
	}
	return { source_kind: "max", source_run_rel: datasetRel };
}

module.exports = {
	KIND_DIRS: KIND_DIRS,
	KIND_LABELS: KIND_LABELS,
	maxRoleBase: maxRoleBase,
	branchRootAbs: branchRootAbs,
	listSignalBranches: listSignalBranches,
	listDatasetsForBranch: listDatasetsForBranch,
	defaultDatasetForBranch: defaultDatasetForBranch,
	defaultBranchForDetectMethod: defaultBranchForDetectMethod,
	formatDatasetLabel: formatDatasetLabel,
	parseSourceRunRel: parseSourceRunRel,
};
