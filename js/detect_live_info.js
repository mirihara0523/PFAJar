"use strict";

function exclusionThresholds(row) {
	const confidence = row[4], area = row[5], eccentricity = row[6], intensity = row[7];
	return {
		confidence: Number.isFinite(confidence) && confidence < 1 ? Math.min(1, (Math.floor(confidence * 10000) + 1) / 10000) : null,
		area: Number.isFinite(area) ? Math.ceil(area) : null,
		eccentricity: Number.isFinite(eccentricity) ? Math.min(1, Math.ceil(eccentricity * 10000) / 10000) : null,
		intensity: Number.isFinite(intensity) && intensity < 255 ? Math.floor(intensity) + 1 : null,
	};
}

function number(value) {
	return Number.isFinite(value) ? String(Number(value.toFixed(6))) : "not measured";
}

function candidateDescription(row, params, index) {
	const cutoffs = exclusionThresholds(row);
	const lines = [
		"BBox #" + (index + 1),
		"Confidence: " + number(row[4]) + " · current ≥ " + number(params.confidence),
		"Area: " + number(row[5]) + " px² · current > " + number(params.area),
		"Eccentricity: " + number(row[6]) + " · current > " + number(params.eccentricity),
		"Intensity p90 (CLAHE): " + number(row[7]) + " · current ≥ " + number(params.intensity) + (params.intensity === 0 ? " (disabled)" : ""),
		"", "To exclude this BBox, raise ONE cutoff to:",
	];
	if (cutoffs.confidence !== null) lines.push("Confidence ≥ " + number(cutoffs.confidence));
	if (cutoffs.area !== null) lines.push("Minimum area ≥ " + number(cutoffs.area) + " px²");
	if (cutoffs.eccentricity !== null) lines.push("Minimum eccentricity ≥ " + number(cutoffs.eccentricity));
	if (cutoffs.intensity !== null) lines.push("Intensity cutoff ≥ " + number(cutoffs.intensity));
	if (row[4] === 1) lines.push("Confidence alone cannot exclude a score of 1.");
	if (row[7] === 255) lines.push("Intensity alone cannot exclude a p90 of 255.");
	lines.push("", "Cutoffs affect all candidates, including real cells.");
	return lines.join("\n");
}

function hitCandidate(records, indices, x, y, tolerance) {
	let selected = null, smallest = Infinity;
	for (const index of indices) {
		const row = records[index];
		if (x < row[0] - tolerance || x > row[2] + tolerance || y < row[1] - tolerance || y > row[3] + tolerance) continue;
		const area = (row[2] - row[0]) * (row[3] - row[1]);
		if (area < smallest) { smallest = area; selected = index; }
	}
	return selected;
}

module.exports = { exclusionThresholds, candidateDescription, hitCandidate };
