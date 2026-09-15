"use strict";

var assert = require("assert");
var path = require("path");
var fs = require("fs");
var fileIndex = require("../js/file_index");
var pipelineRuns = require("../js/pipeline_runs");
var helpers = require("./test-helpers");

function testSliceIdFromFilename() {
	assert.strictEqual(
		fileIndex.sliceIdFromFilename("M528_s061.ome.tiff"),
		"M528_s061",
	);
	assert.strictEqual(
		fileIndex.sliceIdFromFilename("M528_s061.tiff"),
		"M528_s061",
	);
	assert.strictEqual(
		fileIndex.sliceIdFromFilename("M528_s061.ome.tif"),
		"M528_s061",
	);
	assert.strictEqual(
		fileIndex.sliceIdFromFilename("brain.section.extra.png"),
		"brain",
	);
}

function testListImageFiles() {
	var dir = helpers.tmpDir("mj-list-");
	helpers.touchImage(dir, "a.tif");
	helpers.touchImage(dir, "b.ome.tiff");
	fs.writeFileSync(path.join(dir, "notes.txt"), "x");
	var listed = fileIndex.listImageFiles(dir).map(function (p) {
		return path.basename(p);
	});
	assert.ok(listed.indexOf("a.tif") >= 0);
	assert.ok(listed.indexOf("b.ome.tiff") >= 0);
	assert.ok(listed.indexOf("notes.txt") < 0);
	helpers.rmDir(dir);
}

function testMatchReport() {
	var index = {
		files: [
			{
				sliceId: "M528_s061",
				role: "dapi",
				metadata: { width: 512, height: 512 },
			},
			{
				sliceId: "M528_s061",
				role: "max",
				metadata: { width: 2048, height: 2048 },
			},
			{
				sliceId: "M528_s099",
				role: "dapi",
				metadata: { width: 512, height: 512 },
			},
		],
	};
	var report = fileIndex.computeMatchReport(index, ["dapi", "max"]);
	assert.deepStrictEqual(report.matchedSliceIds, ["M528_s061"]);
	assert.ok(report.orphansByRole.dapi.indexOf("M528_s099") >= 0);
	assert.ok(
		report.qualityIssues.some(function (q) {
			return q.code === "resolution_mismatch";
		}),
		"expected resolution mismatch warning",
	);
}

function testMatchReportOrientationSwap() {
	var index = {
		files: [
			{
				sliceId: "M001_s001",
				role: "dapi",
				metadata: { width: 1000, height: 500 },
			},
			{
				sliceId: "M001_s001",
				role: "max",
				metadata: { width: 500, height: 1000 },
			},
		],
	};
	var report = fileIndex.computeMatchReport(index, ["dapi", "max"]);
	assert.ok(
		report.qualityIssues.some(function (q) {
			return q.code === "orientation_swap";
		}),
		"expected orientation_swap warning",
	);
}

function testPlanRunModes() {
	var bundle = helpers.tmpDir("mj-plan-");
	var roles = {
		slices: "data/counting/01_slices",
		pkls: "data/counting/07_pkls",
	};
	var slicesDir = path.join(bundle, roles.slices);
	var pklsDir = path.join(bundle, roles.pkls);
	var activeRunDir = path.join(slicesDir, "align", "legacy_run");
	fs.mkdirSync(activeRunDir, { recursive: true });
	fs.mkdirSync(pklsDir, { recursive: true });
	fs.writeFileSync(path.join(activeRunDir, "Annotation_M528_s061.pkl"), "x");
	fs.writeFileSync(path.join(pklsDir, "M528_s061_VISp.pkl"), "x");
	var activeRuns = { slices: "align/legacy_run" };

	var sliceIds = ["M528_s061", "M528_s062"];

	var mergeAlign = fileIndex.planRun(bundle, "align", {
		mode: "merge",
		sliceIds: sliceIds,
		roles: roles,
		activeRuns: activeRuns,
	});
	assert.deepStrictEqual(mergeAlign.toProcess, ["M528_s062"]);
	assert.equal(mergeAlign.skipped.length, 1);

	var skipAlign = fileIndex.planRun(bundle, "align", {
		mode: "skip",
		sliceIds: sliceIds,
		roles: roles,
		activeRuns: activeRuns,
	});
	assert.deepStrictEqual(skipAlign.toProcess, ["M528_s062"]);

	var overwriteAlign = fileIndex.planRun(bundle, "align", {
		mode: "overwrite",
		sliceIds: sliceIds,
		roles: roles,
		activeRuns: activeRuns,
	});
	assert.deepStrictEqual(overwriteAlign.toProcess, sliceIds);
	assert.equal(overwriteAlign.skipped.length, 0);

	var mergeIntensity = fileIndex.planRun(bundle, "intensity", {
		mode: "merge",
		sliceIds: sliceIds,
		roles: roles,
		activeRuns: Object.assign({}, activeRuns, { pkls: "" }),
	});
	assert.deepStrictEqual(mergeIntensity.toProcess, ["M528_s062"]);

	helpers.rmDir(bundle);
}

function testGetProcessingSliceIds() {
	var bundle = helpers.tmpDir("mj-subset-");
	var index = {
		files: [
			{ sliceId: "M528_s061", role: "dapi", metadata: { width: 10, height: 10 } },
			{ sliceId: "M528_s061", role: "max", metadata: { width: 10, height: 10 } },
			{ sliceId: "M528_s062", role: "dapi", metadata: { width: 10, height: 10 } },
			{ sliceId: "M528_s062", role: "max", metadata: { width: 10, height: 10 } },
			{ sliceId: "M528_s099", role: "dapi", metadata: { width: 10, height: 10 } },
		],
	};
	var report = fileIndex.computeMatchReport(index, fileIndex.INPUT_MATCH_ROLES);
	var allIds = fileIndex.getProcessingSliceIds(bundle, {}, index, report);
	assert.deepStrictEqual(allIds, ["M528_s061", "M528_s062"]);

	var subsetProject = {
		processing: {
			subset_enabled: true,
			slice_ids: ["M528_s062"],
		},
	};
	var subsetIds = fileIndex.getProcessingSliceIds(
		bundle,
		subsetProject,
		index,
		report,
	);
	assert.deepStrictEqual(subsetIds, ["M528_s062"]);

	helpers.rmDir(bundle);
}

function testOutputExistsAlignStemMatch() {
	var bundle = helpers.tmpDir("mj-align-");
	var roles = { slices: "data/counting/01_slices" };
	var slicesDir = path.join(bundle, roles.slices);
	var leaf = path.join(slicesDir, "align", "run1");
	fs.mkdirSync(leaf, { recursive: true });
	fs.writeFileSync(
		path.join(leaf, "Annotation_M528_s061.ome.pkl"),
		"x",
	);
	var activeRuns = { slices: "align/run1" };
	assert.strictEqual(
		fileIndex.outputExistsForSlice(bundle, "align", "M528_s061", roles, activeRuns),
		true,
	);
	assert.strictEqual(
		fileIndex.outputExistsForSlice(bundle, "align", "M528_s099", roles, activeRuns),
		false,
	);
	helpers.rmDir(bundle);
}

function testSlicesRoleIndexesActiveRunOnly() {
	var bundle = helpers.tmpDir("mj-slices-pkl-");
	var roles = {
		dapi: "data/counting/00_dapi",
		slices: "data/counting/01_slices",
	};
	var slicesDir = path.join(bundle, roles.slices);
	fs.mkdirSync(path.join(bundle, roles.dapi), { recursive: true });
	helpers.touchImage(path.join(bundle, roles.dapi), "M528_s027.tif");
	helpers.touchImage(path.join(bundle, roles.dapi), "M528_s028.tif");
	var activeLeaf = path.join(slicesDir, "align", "run_a");
	var otherLeaf = path.join(slicesDir, "align", "run_b");
	fs.mkdirSync(activeLeaf, { recursive: true });
	fs.mkdirSync(otherLeaf, { recursive: true });
	fs.writeFileSync(path.join(activeLeaf, "Annotation_M528_s027.pkl"), "x");
	fs.writeFileSync(path.join(otherLeaf, "Annotation_M528_s028.pkl"), "y");
	var activeRuns = pipelineRuns.defaultActiveRuns();
	activeRuns.slices = "align/run_a";
	return fileIndex
		.buildFileIndex(bundle, roles, {
			appRoot: path.join(__dirname, ".."),
			activeRuns: activeRuns,
		})
		.then(function (index) {
			var report = fileIndex.computeMatchReport(index, ["dapi", "slices"]);
			assert.ok(
				report.matchedSliceIds.indexOf("M528_s027") >= 0,
				"active run slice should index",
			);
			assert.ok(
				report.matchedSliceIds.indexOf("M528_s028") < 0,
				"sibling run slice must not leak into match report",
			);
			helpers.rmDir(bundle);
		});
}

function testSlicesRoleIgnoresWarpedTiffs() {
	var bundle = helpers.tmpDir("mj-slices-warp-");
	var roles = {
		dapi: "data/counting/00_dapi",
		slices: "data/counting/01_slices",
	};
	var activeLeaf = path.join(bundle, roles.slices, "align", "run_warp");
	fs.mkdirSync(path.join(bundle, roles.dapi), { recursive: true });
	fs.mkdirSync(activeLeaf, { recursive: true });
	helpers.touchImage(path.join(bundle, roles.dapi), "M457_s001.png");
	fs.writeFileSync(path.join(activeLeaf, "Annotation_M457_s001.pkl"), "x");
	for (var i = 0; i < 80; i++) {
		helpers.touchImage(activeLeaf, "Composite_M457_s" + String(i + 1).padStart(3, "0") + ".tif");
		helpers.touchImage(activeLeaf, "Atlas_M457_s" + String(i + 1).padStart(3, "0") + ".tif");
	}
	var activeRuns = pipelineRuns.defaultActiveRuns();
	activeRuns.slices = "align/run_warp";
	return fileIndex
		.buildFileIndex(bundle, roles, {
			appRoot: path.join(__dirname, ".."),
			activeRuns: activeRuns,
		})
		.then(function (index) {
			var sliceRows = index.files.filter(function (row) {
				return row.role === "slices";
			});
			assert.strictEqual(sliceRows.length, 1, "only annotation PKLs should index");
			assert.strictEqual(sliceRows[0].sliceId, "M457_s001");
			var report = fileIndex.computeMatchReport(index, ["dapi", "slices"]);
			assert.deepStrictEqual(report.matchedSliceIds, ["M457_s001"]);
			helpers.rmDir(bundle);
		});
}

function testOutputExistsSharpenTophat() {
	var bundle = helpers.tmpDir("mj-sharp-");
	var roles = { max: "data/counting/03_max" };
	var maxBase = path.join(bundle, roles.max);
	var sharpenLeaf = path.join(maxBase, "rabies", "sharpen", "run1");
	fs.mkdirSync(sharpenLeaf, { recursive: true });
	helpers.touchImage(sharpenLeaf, "M528_s001.tif");
	assert.strictEqual(
		fileIndex.outputExistsForSlice(bundle, "sharpen", "M528_s001", roles, {}),
		true,
	);
	assert.strictEqual(
		fileIndex.outputExistsForSlice(bundle, "sharpen", "M528_s099", roles, {}),
		false,
	);
	var tophatLeaf = path.join(maxBase, "somata", "tophat", "run1");
	fs.mkdirSync(tophatLeaf, { recursive: true });
	helpers.touchImage(tophatLeaf, "M528_s002.ome.tiff");
	assert.strictEqual(
		fileIndex.outputExistsForSlice(bundle, "tophat", "M528_s002", roles, {}),
		true,
	);
	helpers.rmDir(bundle);
}

var tests = [
	testSliceIdFromFilename,
	testListImageFiles,
	testMatchReport,
	testMatchReportOrientationSwap,
	testPlanRunModes,
	testGetProcessingSliceIds,
	testOutputExistsAlignStemMatch,
	testOutputExistsSharpenTophat,
	testSlicesRoleIndexesActiveRunOnly,
	testSlicesRoleIgnoresWarpedTiffs,
];

function runAll() {
	var chain = Promise.resolve();
	for (var j = 0; j < tests.length; j++) {
		(function (fn) {
			chain = chain.then(function () {
				var ret = fn();
				return ret && typeof ret.then === "function" ? ret : undefined;
			});
		})(tests[j]);
	}
	return chain.then(function () {
		console.log("test-file-index.js: OK (" + tests.length + " tests)");
	});
}

runAll().catch(function (err) {
	console.error(err);
	process.exit(1);
});
