"use strict";

var assert = require("assert");
var path = require("path");
var fs = require("fs");
var helpers = require("./test-helpers");

helpers.ensureLocalStorage();

var project = require("../js/project");
var fileIndex = require("../js/file_index");

function matchedIndexFiles() {
	return [
		{
			sliceId: "M528_s061",
			role: "dapi",
			relPath: "data/counting/00_dapi/M528_s061.png",
			metadata: { width: 512, height: 512 },
		},
		{
			sliceId: "M528_s061",
			role: "max",
			relPath: "data/counting/03_max/M528_s061.tif",
			metadata: { width: 512, height: 512 },
		},
		{
			sliceId: "M528_s061",
			role: "slices",
			relPath: "data/counting/01_slices/Annotation_M528_s061.pkl",
			metadata: {},
		},
		{
			sliceId: "M528_s062",
			role: "dapi",
			relPath: "data/counting/00_dapi/M528_s062.png",
			metadata: { width: 512, height: 512 },
		},
		{
			sliceId: "M528_s062",
			role: "max",
			relPath: "data/counting/03_max/M528_s062.tif",
			metadata: { width: 512, height: 512 },
		},
		{
			sliceId: "M528_s063",
			role: "dapi",
			relPath: "data/counting/00_dapi/M528_s063.png",
			metadata: { width: 512, height: 512 },
		},
		{
			sliceId: "M528_s063",
			role: "max",
			relPath: "data/counting/03_max/M528_s063.tif",
			metadata: { width: 512, height: 512 },
		},
	];
}

function setupBundle() {
	var bundle = helpers.tmpDir("mj-stepfail-");
	project.createProject({ bundleRoot: bundle, name: "StepFailBrain" });
	helpers.writeFileIndex(bundle, matchedIndexFiles());
	return bundle;
}

function testMergeAlignWarpReportRecordsAndClears() {
	var bundle = setupBundle();
	var alignLeaf = path.join(bundle, project.CANONICAL_ROLES.slices, "align", "test_run");
	fs.mkdirSync(path.join(alignLeaf, ".masonjar"), { recursive: true });
	var report = {
		timestamp: "2026-05-26T12:00:00.000Z",
		warp_ok: ["M528_s061"],
		warp_failed: [
			{
				slice_id: "M528_s062",
				file: "M528_s062.png",
				error: "Joint PDF summed to zero",
			},
		],
	};
	fs.writeFileSync(
		path.join(alignLeaf, ".masonjar", "align_warp_report.json"),
		JSON.stringify(report, null, 2),
		"utf8",
	);

	project.recordStepFailure("align", "M528_s061", {
		message: "prior failure on slice that succeeded this run",
		at: "2026-05-25T00:00:00.000Z",
	});
	project.recordStepFailure("align", "M528_s063", {
		message: "unrelated stale failure",
		at: "2026-05-25T00:00:00.000Z",
	});

	var result = project.mergeAlignWarpReport(bundle, alignLeaf);
	assert.equal(result.recorded, 1);
	assert.equal(result.cleared, 1);

	var failures = project.getFailedSliceIds("align");
	assert.deepStrictEqual(failures.sort(), ["M528_s062", "M528_s063"]);

	var proc = project.getProject().processing;
	assert.ok(proc.step_failures.align.M528_s062);
	assert.equal(
		proc.step_failures.align.M528_s062.message,
		"Joint PDF summed to zero",
	);
	assert.ok(proc.step_failures.align.M528_s063);
	assert.equal(proc.step_failures.align.M528_s061, undefined);

	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testGetProcessingSliceIdsExcludesFailedAlign() {
	var bundle = setupBundle();
	project.recordStepFailure("align", "M528_s062", {
		message: "warp failed",
		at: "2026-05-26T12:00:00.000Z",
	});
	var index = project.readProjectFileIndex();
	var report = fileIndex.computeMatchReport(index, fileIndex.INPUT_MATCH_ROLES);
	var ids = fileIndex.getProcessingSliceIds(bundle, project.getProject(), index, report);
	assert.deepStrictEqual(ids.sort(), ["M528_s061", "M528_s063"]);

	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testGetProcessingSliceIdsAllowsAlignRetry() {
	var bundle = setupBundle();
	project.recordStepFailure("align", "M528_s062", {
		message: "warp failed",
		at: "2026-05-26T12:00:00.000Z",
	});
	var index = project.readProjectFileIndex();
	var report = fileIndex.computeMatchReport(index, fileIndex.INPUT_MATCH_ROLES);
	var ids = fileIndex.getProcessingSliceIds(bundle, project.getProject(), index, report, {
		stepId: "align",
	});
	assert.deepStrictEqual(ids.sort(), ["M528_s061", "M528_s062", "M528_s063"]);

	var dapiIds = fileIndex.getProcessingSliceIds(
		bundle,
		project.getProject(),
		index,
		report,
		{ stepId: "dapi_cleanup" },
	);
	assert.deepStrictEqual(dapiIds.sort(), ["M528_s061", "M528_s062", "M528_s063"]);

	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function main() {
	testMergeAlignWarpReportRecordsAndClears();
	testGetProcessingSliceIdsExcludesFailedAlign();
	testGetProcessingSliceIdsAllowsAlignRetry();
	console.log("test-step-failures.js: ok");
}

main();
