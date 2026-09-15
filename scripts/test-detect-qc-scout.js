"use strict";

var assert = require("assert");
var fs = require("fs");
var path = require("path");
var helpers = require("./test-helpers");

helpers.ensureLocalStorage();

var detectQcScout = require("../js/detect_qc_scout");
var pipelineRuns = require("../js/pipeline_runs");

function makeBundle() {
	var bundle = helpers.tmpDir("mj-dqs-");
	fs.mkdirSync(path.join(bundle, ".masonjar"), { recursive: true });
	fs.writeFileSync(
		path.join(bundle, "M528.masonjar"),
		JSON.stringify(
			{
				name: "M528",
				roles: pipelineRuns.CANONICAL_ROLES,
				processing: {},
			},
			null,
			2,
		),
		"utf8",
	);
	return bundle;
}

function testShouldPromptLogic() {
	assert.strictEqual(
		detectQcScout.shouldPromptBeforeDetect(null, 0),
		false,
	);
	assert.strictEqual(
		detectQcScout.shouldPromptBeforeDetect(
			{ suggestions: { intensity_min: 40 }, applied_intensity_min: null },
			0,
		),
		true,
	);
	assert.strictEqual(
		detectQcScout.shouldPromptBeforeDetect(
			{ suggestions: { intensity_min: 40 }, applied_intensity_min: 40 },
			0,
		),
		false,
	);
	assert.strictEqual(
		detectQcScout.shouldPromptBeforeDetect(
			{ suggestions: { intensity_min: 40 }, applied_intensity_min: null },
			40,
		),
		false,
	);
}

function testRecordDetectQcOnProject() {
	var bundle = makeBundle();
	try {
		var predBase = path.join(bundle, "data", "counting", "05_predictions");
		var outAbs = path.join(predBase, "signal", "qc_scout", "scout1");
		fs.mkdirSync(outAbs, { recursive: true });
		fs.writeFileSync(
			path.join(outAbs, "detect_qc_summary.json"),
			JSON.stringify({
				analysis: {
					suggestions: { intensity_min: 55 },
					summary_lines: ["ok"],
				},
			}),
			"utf8",
		);
		var record = detectQcScout.recordDetectQcOnProject(bundle, {
			outputAbs: outAbs,
			signalDatasetAbs: path.join(
				bundle,
				"data",
				"counting",
				"03_max",
				"max",
				"run1",
			),
		});
		assert.ok(record.output_rel.indexOf("qc_scout") >= 0);
		assert.strictEqual(record.suggestions.intensity_min, 55);
		assert.strictEqual(record.applied_intensity_min, null);
		var raw = fs.readFileSync(path.join(bundle, "M528.masonjar"), "utf8");
		var data = JSON.parse(raw);
		assert.ok(data.processing.detect_qc);
		assert.strictEqual(data.processing.detect_qc.suggestions.intensity_min, 55);
	} finally {
		helpers.rmDir(bundle);
	}
}

function testDiscoverSkipsQcScout() {
	var bundle = makeBundle();
	try {
		var predBase = path.join(bundle, "data", "counting", "05_predictions");
		var real = path.join(predBase, "signal", "real_run");
		var scout = path.join(predBase, "signal", "qc_scout", "scout1");
		fs.mkdirSync(real, { recursive: true });
		fs.mkdirSync(scout, { recursive: true });
		fs.writeFileSync(path.join(real, "Predictions_s01.pkl"), "x");
		fs.writeFileSync(path.join(scout, "detect_qc_summary.json"), "{}");
		var choices = pipelineRuns.discoverRunChoicesForBundle(
			bundle,
			pipelineRuns.CANONICAL_ROLES,
			"predictions",
		);
		var rels = choices.map(function (c) {
			return c.rel;
		});
		assert.ok(rels.some(function (r) {
			return r.indexOf("real_run") >= 0;
		}));
		assert.ok(
			!rels.some(function (r) {
				return r.indexOf("qc_scout") >= 0;
			}),
			"qc_scout must not appear as a detection run choice: " + rels.join(","),
		);
	} finally {
		helpers.rmDir(bundle);
	}
}

function run() {
	testShouldPromptLogic();
	testRecordDetectQcOnProject();
	testDiscoverSkipsQcScout();
	console.log("test-detect-qc-scout: PASS");
}

run();
