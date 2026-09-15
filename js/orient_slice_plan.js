"use strict";

var cziImport = require("./czi_import");

/**
 * Merge slice IDs from CZI import config and DAPI file index (natural sort).
 * Display channel does not affect membership — only which preview loads per tile.
 */
function mergeOrientSliceIds(orderIds, indexIds) {
	var seen = {};
	var out = [];
	function add(id) {
		if (id && !seen[id]) {
			seen[id] = true;
			out.push(id);
		}
	}
	for (var i = 0; i < (orderIds || []).length; i++) {
		add(orderIds[i]);
	}
	for (var j = 0; j < (indexIds || []).length; j++) {
		add(indexIds[j]);
	}
	out.sort(function (a, b) {
		return cziImport.naturalCompare({ sliceId: a }, { sliceId: b });
	});
	return out;
}

module.exports = {
	mergeOrientSliceIds: mergeOrientSliceIds,
};
