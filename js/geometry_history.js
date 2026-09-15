"use strict";

var fs = require("fs");
var path = require("path");

var branding = require("./branding");
var orientGeometry = require("./orient_geometry");
var geometryState = require("./geometry_state");

var HISTORY_FILENAME = "geometry_history.jsonl";

function historyPath(bundleRoot) {
	return path.join(bundleRoot, branding.META_DIR, HISTORY_FILENAME);
}

function parseGeometryHistoryJsonl(text) {
	var entries = [];
	var lines = String(text || "").split("\n");
	for (var i = 0; i < lines.length; i++) {
		var line = lines[i].trim();
		if (!line) {
			continue;
		}
		try {
			entries.push(JSON.parse(line));
		} catch (e) {
			/* skip malformed line */
		}
	}
	return entries;
}

function readGeometryHistory(bundleRoot) {
	var histPath = historyPath(bundleRoot);
	if (!fs.existsSync(histPath)) {
		return [];
	}
	try {
		return parseGeometryHistoryJsonl(fs.readFileSync(histPath, "utf8"));
	} catch (e) {
		return [];
	}
}

function normalizeOpsList(ops) {
	if (!ops || !ops.length) {
		return [];
	}
	var out = [];
	for (var i = 0; i < ops.length; i++) {
		var op = ops[i];
		if (typeof op === "string" && op) {
			out.push(op);
		}
	}
	return out;
}

/**
 * Last non-empty ops list per slice_id from geometry apply history.
 * @param {object[]} entries
 * @param {string[]} sliceIds optional filter
 */
function lastOpsBySliceId(entries, sliceIds) {
	var filter = null;
	if (sliceIds && sliceIds.length) {
		filter = {};
		for (var f = 0; f < sliceIds.length; f++) {
			filter[sliceIds[f]] = true;
		}
	}
	var bySlice = {};
	for (var i = 0; i < (entries || []).length; i++) {
		var entry = entries[i];
		if (!entry || entry.kind !== "file") {
			continue;
		}
		var sid = entry.slice_id;
		if (!sid) {
			continue;
		}
		if (filter && !filter[sid]) {
			continue;
		}
		var ops = normalizeOpsList(entry.ops);
		if (ops.length) {
			bySlice[sid] = ops;
		}
	}
	return bySlice;
}

/**
 * After CZI re-extract: restore pending geometry ops from history for reimported
 * slices and clear stale geometry_applied_at metadata.
 * @returns {{ restored: string[], missing: string[] }}
 */
function reconcileGeometryAfterReextract(bundleRoot, cziImport, sliceIds) {
	cziImport = cziImport || {};
	sliceIds = sliceIds || [];
	var entries = readGeometryHistory(bundleRoot);
	var opsBySlice = lastOpsBySliceId(entries, sliceIds);
	if (!cziImport.geometry) {
		cziImport.geometry = {};
	}
	var restored = [];
	var missing = [];
	for (var i = 0; i < sliceIds.length; i++) {
		var sliceId = sliceIds[i];
		var ops = opsBySlice[sliceId];
		if (ops && ops.length) {
			cziImport.geometry[sliceId] = { ops: ops.slice() };
			restored.push(sliceId);
		} else {
			missing.push(sliceId);
		}
	}
	delete cziImport.geometry_applied_at;
	delete cziImport.geometry_applied_files_total;
	if (bundleRoot) {
		geometryState.clearGeometryApplyMeta(bundleRoot);
	}
	return { restored: restored, missing: missing };
}

/**
 * True when none of the given slice ids have non-empty ops in geometry history.
 */
function reextractSliceIdsLackHistory(bundleRoot, sliceIds) {
	sliceIds = sliceIds || [];
	if (!sliceIds.length) {
		return false;
	}
	var entries = readGeometryHistory(bundleRoot);
	var opsBySlice = lastOpsBySliceId(entries, sliceIds);
	for (var i = 0; i < sliceIds.length; i++) {
		var ops = opsBySlice[sliceIds[i]];
		if (ops && ops.length) {
			return false;
		}
	}
	return true;
}

module.exports = {
	HISTORY_FILENAME: HISTORY_FILENAME,
	historyPath: historyPath,
	parseGeometryHistoryJsonl: parseGeometryHistoryJsonl,
	readGeometryHistory: readGeometryHistory,
	lastOpsBySliceId: lastOpsBySliceId,
	reconcileGeometryAfterReextract: reconcileGeometryAfterReextract,
	reextractSliceIdsLackHistory: reextractSliceIdsLackHistory,
};
