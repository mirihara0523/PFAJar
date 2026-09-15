"use strict";

/**
 * Tests for batch Detect QC intensity resolution.
 */

var assert = require("assert");
var path = require("path");

var intensity = require(path.join(__dirname, "..", "js", "batch_detect_intensity"));
var registry = require(path.join(__dirname, "..", "js", "batch_registry"));

function testDefaultParam() {
	assert.strictEqual(
		registry.DEFAULT_PARAMS.detect.useQcIntensityThreshold,
		false,
	);
	assert.ok(
		!Object.prototype.hasOwnProperty.call(
			registry.DEFAULT_PARAMS.detect_qc,
			"useQcIntensityThreshold",
		),
	);
}

function testPlanIntensity() {
	var r = intensity.resolveDetectIntensityMin(
		{ intensityMin: 40, useQcIntensityThreshold: false },
		{ suggestions: { intensity_min: 90 } },
	);
	assert.strictEqual(r.ok, true);
	assert.strictEqual(r.value, 40);
	assert.strictEqual(r.source, "plan");
}

function testPlanZeroOff() {
	var r = intensity.resolveDetectIntensityMin(
		{ intensityMin: 0, useQcIntensityThreshold: false },
		null,
	);
	assert.strictEqual(r.ok, true);
	assert.strictEqual(r.value, 0);
	assert.strictEqual(r.source, "plan");
}

function testQcSuggestion() {
	var r = intensity.resolveDetectIntensityMin(
		{ intensityMin: 0, useQcIntensityThreshold: true },
		{ suggestions: { intensity_min: 73 } },
	);
	assert.strictEqual(r.ok, true);
	assert.strictEqual(r.value, 73);
	assert.strictEqual(r.source, "qc");
}

function testQcMissing() {
	var r = intensity.resolveDetectIntensityMin(
		{ useQcIntensityThreshold: true },
		{ suggestions: {} },
	);
	assert.strictEqual(r.ok, false);
	assert.strictEqual(r.reason, "no QC intensity suggestion");
}

function testQcZeroInvalid() {
	var r = intensity.resolveDetectIntensityMin(
		{ useQcIntensityThreshold: true },
		{ suggestions: { intensity_min: 0 } },
	);
	assert.strictEqual(r.ok, false);
}

function testQcIgnoresPlanCutoff() {
	var r = intensity.resolveDetectIntensityMin(
		{ intensityMin: 12, useQcIntensityThreshold: true },
		{ suggestions: { intensity_min: 55 } },
	);
	assert.strictEqual(r.value, 55);
	assert.strictEqual(r.source, "qc");
}

function run() {
	testDefaultParam();
	testPlanIntensity();
	testPlanZeroOff();
	testQcSuggestion();
	testQcMissing();
	testQcZeroInvalid();
	testQcIgnoresPlanCutoff();
	console.log("test-batch-detect-qc-intensity: PASS");
}

run();
