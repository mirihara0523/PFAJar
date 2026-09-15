"use strict";

/**
 * Resolve Cell Detection intensity-min for batch jobs.
 * Optional QC mode uses each project's processing.detect_qc suggestion.
 */

function suggestionFromDetectQc(detectQc) {
	if (!detectQc || typeof detectQc !== "object") {
		return null;
	}
	var sug =
		detectQc.suggestions && detectQc.suggestions.intensity_min != null
			? Number(detectQc.suggestions.intensity_min)
			: null;
	if (sug == null || Number.isNaN(sug) || sug <= 0) {
		return null;
	}
	return sug;
}

/**
 * @param {Record<string, unknown>} params detect step params
 * @param {object|null|undefined} detectQc processing.detect_qc from project JSON
 * @returns {{ ok: true, value: number, source: "qc"|"plan" } | { ok: false, reason: string, value: number, source: "qc" }}
 */
function resolveDetectIntensityMin(params, detectQc) {
	var p = params || {};
	if (p.useQcIntensityThreshold) {
		var sug = suggestionFromDetectQc(detectQc);
		if (sug == null) {
			return {
				ok: false,
				reason: "no QC intensity suggestion",
				value: 0,
				source: "qc",
			};
		}
		return { ok: true, value: sug, source: "qc" };
	}
	var v = Number(p.intensityMin != null ? p.intensityMin : 0);
	if (Number.isNaN(v) || v < 0) {
		v = 0;
	}
	return { ok: true, value: v, source: "plan" };
}

module.exports = {
	suggestionFromDetectQc: suggestionFromDetectQc,
	resolveDetectIntensityMin: resolveDetectIntensityMin,
};
