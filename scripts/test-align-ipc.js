"use strict";

var assert = require("assert");
var alignIpc = require("../js/align_ipc");

function testClassifyDone() {
	assert.strictEqual(alignIpc.classifyAlignStdoutMessage("Done!"), "done");
}

function testClassifyViewerClosed() {
	assert.strictEqual(
		alignIpc.classifyAlignStdoutMessage("Viewer closed"),
		"viewer_closed",
	);
}

function testClassifyWarping() {
	assert.strictEqual(
		alignIpc.classifyAlignStdoutMessage("ALIGN_WARPING"),
		"warping",
	);
	assert.strictEqual(alignIpc.ALIGN_MSG_WARPING, "ALIGN_WARPING");
}

function testClassifyProgress() {
	assert.strictEqual(
		alignIpc.classifyAlignStdoutMessage("Awaiting fine tuning..."),
		"other",
	);
}

function testParseAlignResultLine() {
	assert.strictEqual(alignIpc.parseAlignResultLine("LOG: nope"), null);
	assert.deepStrictEqual(
		alignIpc.parseAlignResultLine('RESULT:{"warped":3,"failed":0,"ok":true}'),
		{ warped: 3, failed: 0, ok: true },
	);
}

function testAlignResultPayloads() {
	assert.deepStrictEqual(alignIpc.alignResultPayloadForKind("done"), {
		cancelled: false,
	});
	assert.deepStrictEqual(alignIpc.alignResultPayloadForKind("viewer_closed"), {
		cancelled: true,
	});
	assert.deepStrictEqual(
		alignIpc.alignResultPayloadForKind("done", { warped: 2, failed: 0 }),
		{ cancelled: false, summary: { warped: 2, failed: 0 } },
	);
}

function testSideEffects() {
	assert.strictEqual(alignIpc.shouldApplyAlignRunSideEffects(null), true);
	assert.strictEqual(alignIpc.shouldApplyAlignRunSideEffects({ cancelled: false }), true);
	assert.strictEqual(alignIpc.shouldApplyAlignRunSideEffects({ cancelled: true }), false);
}

function testAlignCloseFallback() {
	assert.strictEqual(
		alignIpc.shouldTreatAlignCloseAsCancelled({ exitCode: 0 }),
		true,
	);
	assert.strictEqual(
		alignIpc.shouldTreatAlignCloseAsCancelled({
			exitCode: 0,
			warpingStarted: true,
		}),
		false,
	);
	assert.strictEqual(
		alignIpc.shouldTreatAlignCloseAsCancelled({ exitCode: 1, viewerClosedHandshake: true }),
		true,
	);
	assert.strictEqual(
		alignIpc.shouldTreatAlignCloseAsCancelled({ exitCode: 1, sessionSavedOnClose: true }),
		true,
	);
	assert.strictEqual(
		alignIpc.shouldTreatAlignCloseAsCancelled({ exitCode: 1 }),
		false,
	);
	assert.strictEqual(
		alignIpc.shouldReportAlignCloseFailure({ exitCode: 1, viewerClosedHandshake: true }),
		false,
	);
	assert.strictEqual(
		alignIpc.shouldReportAlignCloseFailure({ exitCode: 1 }),
		true,
	);
}

function testViewerToolHelpers() {
	assert.strictEqual(
		alignIpc.classifyViewerToolStdoutMessage("Viewer closed"),
		"viewer_closed",
	);
	assert.deepStrictEqual(
		alignIpc.viewerToolResultPayloadForKind("viewer_closed"),
		{ cancelled: true },
	);
	assert.strictEqual(alignIpc.shouldApplyViewerToolSideEffects({ cancelled: true }), false);
}

testClassifyDone();
testClassifyViewerClosed();
testClassifyWarping();
testClassifyProgress();
testParseAlignResultLine();
testAlignResultPayloads();
testViewerToolHelpers();
testSideEffects();
testAlignCloseFallback();
console.log("test-align-ipc.js: OK");

