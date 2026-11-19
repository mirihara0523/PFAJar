"use strict";
const assert = require("assert");
const { exclusionThresholds, candidateDescription, hitCandidate } = require("../js/detect_live_info");
const row = [5, 5, 20, 20, .8, 225, .5, 100];
assert.deepStrictEqual(exclusionThresholds(row), { confidence: .8001, area: 225, eccentricity: .5, intensity: 101 });
for (const score of [0, .1, .70000001, .99999, 1]) {
	const values = exclusionThresholds([0, 0, 10, 10, score, 100, .20000001, 254.9]);
	if (score < 1) assert.ok(values.confidence > score && values.confidence <= 1);
	else assert.strictEqual(values.confidence, null);
	assert.ok(values.eccentricity >= .20000001 && values.eccentricity <= 1);
	assert.ok(values.intensity > 254.9 && values.intensity <= 255);
}
assert.deepStrictEqual(exclusionThresholds([0, 0, 10, 10, 1, 100.2, null, 255]), { confidence: null, area: 101, eccentricity: null, intensity: null });
const text = candidateDescription(row, { confidence: .5, area: 200, eccentricity: .2, intensity: 0 }, 2);
assert.ok(text.includes("BBox #3") && text.includes("Confidence ≥ 0.8001") && text.includes("Intensity cutoff ≥ 101"));
assert.ok(text.includes("affect all candidates"));
const rows = [[0, 0, 50, 50], row];
assert.strictEqual(hitCandidate(rows, [0, 1], 10, 10, 0), 1, "prefer smaller overlapping box");
assert.strictEqual(hitCandidate(rows, [0], 10, 10, 0), 0, "only displayed candidates may be hit");
assert.strictEqual(hitCandidate(rows, [1], 30, 30, 0), null);
assert.strictEqual(hitCandidate(rows, [1], 21, 10, 2), 1);
console.log("BBox hover info: threshold equality rules, precision, limits, missing measurements and hit testing passed.");
