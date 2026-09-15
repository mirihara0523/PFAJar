"use strict";

var assert = require("assert");
var evaluate = require("../js/preprocess_batch_completion").evaluatePreprocessBatchResult;

function testExitZeroSuccess() {
	var v = evaluate({ runFailed: false, exitCode: 0, pyFail: "", total: 1, completedCount: 1 });
	assert.strictEqual(v.ok, true);
}

function testExitZeroWithCompletionsNoDoneRace() {
	var v = evaluate({
		runFailed: false,
		exitCode: 0,
		pyFail: "",
		total: 1,
		completedCount: 1,
	});
	assert.strictEqual(v.ok, true);
}

function testExitOneAllCompletedWarnOk() {
	var v = evaluate({
		runFailed: false,
		exitCode: 1,
		pyFail: "Python exited with code 1",
		total: 1,
		completedCount: 1,
	});
	assert.strictEqual(v.ok, true);
	assert.strictEqual(v.warnOnly, true);
	assert.ok(v.message.indexOf("non-zero exit") >= 0);
}

function testSharpenNoOutputFails() {
	var v = evaluate({
		runFailed: true,
		exitCode: 1,
		pyFail: "",
		total: 2,
		completedCount: 0,
		failMessage: "SHARPEN_NO_OUTPUT: 0 of 2 files written.",
	});
	assert.strictEqual(v.ok, false);
}

function testExitOnePartialCompletionFails() {
	var v = evaluate({
		runFailed: false,
		exitCode: 1,
		pyFail: "Python exited with code 1",
		total: 3,
		completedCount: 1,
	});
	assert.strictEqual(v.ok, false);
}

function testExitZeroNoOutputsFails() {
	var v = evaluate({
		runFailed: false,
		exitCode: 0,
		pyFail: "",
		total: 1,
		completedCount: 0,
	});
	assert.strictEqual(v.ok, false);
}

testExitZeroSuccess();
testExitZeroWithCompletionsNoDoneRace();
testExitOneAllCompletedWarnOk();
testSharpenNoOutputFails();
testExitOnePartialCompletionFails();
testExitZeroNoOutputsFails();
console.log("test-preprocess-batch-completion.js: OK");
