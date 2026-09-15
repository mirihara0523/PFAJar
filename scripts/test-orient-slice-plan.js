"use strict";

var assert = require("assert");
var orientSlicePlan = require("../js/orient_slice_plan");
var cziImport = require("../js/czi_import");

function testMergeUnion() {
	var ids = orientSlicePlan.mergeOrientSliceIds(
		["M458_s002", "M458_s001"],
		["M458_s003"],
	);
	assert.deepStrictEqual(ids, ["M458_s001", "M458_s002", "M458_s003"]);
}

function testMergeDoesNotDropWithoutPreview() {
	var order = [];
	for (var i = 1; i <= 60; i++) {
		order.push("M458_s" + String(i).padStart(3, "0"));
	}
	var index = order.slice();
	var merged = orientSlicePlan.mergeOrientSliceIds(order, index);
	assert.strictEqual(merged.length, 60);
}

function testNaturalSort() {
	var ids = orientSlicePlan.mergeOrientSliceIds(["M458_s010", "M458_s002"], []);
	assert.deepStrictEqual(ids, ["M458_s002", "M458_s010"]);
}

function run() {
	testMergeUnion();
	testMergeDoesNotDropWithoutPreview();
	testNaturalSort();
	console.log("test-orient-slice-plan: PASS");
}

run();
