"use strict";

var fs = require("fs");
var path = require("path");

var CANONICAL_REL = {
	dapi: "data/counting/00_dapi",
	previews: "data/counting/_previews",
	original_scans: "data/original_scans",
	max: "data/counting/03_max",
};

var MAX_DATASET_KINDS = ["max", "sharpen", "tophat"];
var ROLE_DAPI = "dapi";
var ROLE_UNUSED = "unused";

function branchForChannel(ch) {
	if (!ch) return "";
	if (ch.role === "signal_somata") return "somata";
	if (ch.role === "signal_nuclei") return "nuclei";
	if (ch.role === "signal_axons") return "axons";
	if (ch.role && String(ch.role).indexOf("other:") === 0) {
		return String(ch.role).slice("other:".length);
	}
	return "";
}

function branchForRoleKey(roleKey) {
	if (!roleKey) return "";
	if (roleKey === "signal_somata") return "somata";
	if (roleKey === "signal_nuclei") return "nuclei";
	if (roleKey === "signal_axons") return "axons";
	if (roleKey.indexOf("other:") === 0) return roleKey.slice("other:".length);
	return "";
}

function roleKeyForChannel(ch) {
	if (!ch || !ch.role) return "";
	if (ch.role === "other" && ch.other_name) return "other:" + ch.other_name;
	return ch.role;
}

function signalBranchDirsFromCfg(cfg) {
	var branches = { somata: true, nuclei: true, axons: true };
	var channels = (cfg && cfg.channels) || [];
	for (var i = 0; i < channels.length; i++) {
		var b = branchForChannel(channels[i]);
		if (b) branches[b] = true;
	}
	return Object.keys(branches);
}

function branchesForEnumeration(bundleRoot, cfg) {
	var branches = {};
	var keys = signalBranchDirsFromCfg(cfg);
	for (var i = 0; i < keys.length; i++) branches[keys[i]] = true;
	var maxBase = path.join(bundleRoot, CANONICAL_REL.max);
	if (fs.existsSync(maxBase)) {
		var subs = fs.readdirSync(maxBase);
		for (var s = 0; s < subs.length; s++) {
			try {
				if (fs.statSync(path.join(maxBase, subs[s])).isDirectory()) {
					branches[subs[s]] = true;
				}
			} catch (_e) {
				/* skip */
			}
		}
	}
	return Object.keys(branches);
}

function globDatasetKindPaths(branchRoot, sliceId) {
	var out = [];
	for (var ki = 0; ki < MAX_DATASET_KINDS.length; ki++) {
		var kindRoot = path.join(branchRoot, MAX_DATASET_KINDS[ki]);
		if (!fs.existsSync(kindRoot)) continue;
		walkTiffForSlice(kindRoot, sliceId, out);
	}
	return out;
}

function walkTiffForSlice(dir, sliceId, out) {
	var entries;
	try {
		entries = fs.readdirSync(dir, { withFileTypes: true });
	} catch (_e) {
		return;
	}
	for (var i = 0; i < entries.length; i++) {
		var full = path.join(dir, entries[i].name);
		if (typeof entries[i].isDirectory === "function" ? entries[i].isDirectory() : entries[i].isDirectory) {
			walkTiffForSlice(full, sliceId, out);
		} else if (typeof entries[i].isFile === "function" ? entries[i].isFile() : entries[i].isFile) {
			var lower = entries[i].name.toLowerCase();
			var sidLower = sliceId.toLowerCase();
			if (
				(lower === sidLower + ".tif" || lower === sidLower + ".tiff") &&
				out.indexOf(full) < 0
			) {
				out.push(full);
			}
		}
	}
}

function readMaxRuns(bundleRoot, cfg) {
	var maxRuns = (cfg && cfg.max_runs) || {};
	if (Object.keys(maxRuns).length) return maxRuns;
	var statePath = path.join(bundleRoot, ".masonjar", "czi_import_state.json");
	if (!fs.existsSync(statePath)) return maxRuns;
	try {
		var st = JSON.parse(fs.readFileSync(statePath, "utf8"));
		return st.max_runs || {};
	} catch (_e) {
		return maxRuns;
	}
}

function pathsForSlice(bundleRoot, sliceId, cfg) {
	cfg = cfg || {};
	var paths = [];
	var dapiPng = path.join(bundleRoot, CANONICAL_REL.dapi, sliceId + ".png");
	if (fs.existsSync(dapiPng)) paths.push(dapiPng);
	var prevDir = path.join(bundleRoot, CANONICAL_REL.previews);
	if (fs.existsSync(prevDir)) {
		var prevEntries = fs.readdirSync(prevDir);
		for (var pi = 0; pi < prevEntries.length; pi++) {
			if (prevEntries[pi].indexOf(sliceId + "_") === 0 && /\.png$/i.test(prevEntries[pi])) {
				paths.push(path.join(prevDir, prevEntries[pi]));
			}
		}
	}
	var origBase = path.join(bundleRoot, CANONICAL_REL.original_scans);
	var cfgBranches = signalBranchDirsFromCfg(cfg);
	for (var bi = 0; bi < cfgBranches.length; bi++) {
		var cand = path.join(origBase, cfgBranches[bi], sliceId + ".tif");
		if (fs.existsSync(cand)) paths.push(cand);
	}
	var flat = path.join(origBase, sliceId + ".tif");
	if (fs.existsSync(flat)) paths.push(flat);

	var maxRuns = readMaxRuns(bundleRoot, cfg);
	var roleKeys = Object.keys(maxRuns);
	var channels = cfg.channels || [];
	for (var ci = 0; ci < channels.length; ci++) {
		var ch = channels[ci];
		if (ch.keep && ch.role !== ROLE_DAPI && ch.role !== ROLE_UNUSED) {
			var rk = roleKeyForChannel(ch);
			if (rk && roleKeys.indexOf(rk) < 0) roleKeys.push(rk);
		}
	}
	for (var ri = 0; ri < roleKeys.length; ri++) {
		var roleKey = roleKeys[ri];
		var rel = maxRuns[roleKey];
		if (rel) {
			var activeCand = path.join(bundleRoot, CANONICAL_REL.max, rel, sliceId + ".tif");
			if (fs.existsSync(activeCand)) paths.push(activeCand);
			continue;
		}
		var branch = branchForRoleKey(roleKey);
		if (!branch) continue;
		var maxRoot = path.join(bundleRoot, CANONICAL_REL.max, branch, "max");
		if (!fs.existsSync(maxRoot)) continue;
		var runs = fs.readdirSync(maxRoot);
		for (var runi = 0; runi < runs.length; runi++) {
			var runDir = path.join(maxRoot, runs[runi]);
			try {
				if (!fs.statSync(runDir).isDirectory()) continue;
			} catch (_e) {
				continue;
			}
			var runCand = path.join(runDir, sliceId + ".tif");
			if (fs.existsSync(runCand)) paths.push(runCand);
		}
	}

	var enumBranches = branchesForEnumeration(bundleRoot, cfg);
	for (var eb = 0; eb < enumBranches.length; eb++) {
		var branchRoot = path.join(bundleRoot, CANONICAL_REL.max, enumBranches[eb]);
		if (fs.existsSync(branchRoot)) {
			var globbed = globDatasetKindPaths(branchRoot, sliceId);
			for (var gi = 0; gi < globbed.length; gi++) {
				if (paths.indexOf(globbed[gi]) < 0) paths.push(globbed[gi]);
			}
		}
	}

	var seen = {};
	var unique = [];
	for (var ui = 0; ui < paths.length; ui++) {
		var key = path.resolve(paths[ui]);
		if (!seen[key]) {
			seen[key] = true;
			unique.push(paths[ui]);
		}
	}
	return unique;
}

module.exports = {
	CANONICAL_REL: CANONICAL_REL,
	pathsForSlice: pathsForSlice,
	signalBranchDirsFromCfg: signalBranchDirsFromCfg,
	branchesForEnumeration: branchesForEnumeration,
};
