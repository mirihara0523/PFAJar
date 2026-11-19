"use strict";

const { sliceStemFromImageBasename } = require("./detect_common");
function filenameCompare(a, b) { return a.localeCompare(b, undefined, { numeric: true }); }
function sortSections(files, mode, positions, savedIds) {
	const ranks = new Map((savedIds || []).map((id, index) => [id, index]));
	return files.slice().sort((a, b) => {
		const aId = sliceStemFromImageBasename(a), bId = sliceStemFromImageBasename(b);
		if (mode === "custom") {
			const rankA = ranks.has(aId) ? ranks.get(aId) : ranks.size;
			const rankB = ranks.has(bId) ? ranks.get(bId) : ranks.size;
			if (rankA !== rankB) return rankA - rankB;
		}
		if (mode === "ap_ascending" || mode === "ap_descending") {
			const apA = positions[aId], apB = positions[bId];
			const knownA = Number.isFinite(apA), knownB = Number.isFinite(apB);
			if (knownA !== knownB) return knownA ? -1 : 1;
			if (knownA && apA !== apB) return (apA - apB) * (mode === "ap_descending" ? -1 : 1);
		}
		return filenameCompare(a, b);
	});
}
module.exports = { sliceId: sliceStemFromImageBasename, sortSections };
