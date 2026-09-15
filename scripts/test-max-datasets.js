"use strict";

var assert = require("assert");
var path = require("path");
var fs = require("fs");
var helpers = require("./test-helpers");

helpers.ensureLocalStorage();

var maxDatasets = require("../js/max_datasets");
var pipelineRuns = require("../js/pipeline_runs");

function testListBranchesAndDatasets() {
	var bundle = helpers.tmpDir("mj-datasets-");
	var maxBase = path.join(bundle, "data/counting/03_max");
	var runDir = path.join(maxBase, "somata", "max", "M528_s061-s120");
	fs.mkdirSync(runDir, { recursive: true });
	fs.writeFileSync(path.join(runDir, "M528_s061.tif"), Buffer.alloc(16));
	fs.writeFileSync(
		path.join(runDir, "run_manifest.json"),
		JSON.stringify({ step: "max" }),
	);
	var sharpenDir = path.join(maxBase, "somata", "sharpen", "M528_r3_a2");
	fs.mkdirSync(sharpenDir, { recursive: true });
	fs.writeFileSync(path.join(sharpenDir, "M528_s061.tif"), Buffer.alloc(16));

	var branches = maxDatasets.listSignalBranches(bundle);
	assert.ok(branches.indexOf("somata") >= 0);

	var datasets = maxDatasets.listDatasetsForBranch(bundle, "somata");
	assert.ok(datasets.length >= 2);
	var kinds = datasets.map(function (d) {
		return d.kind;
	});
	assert.ok(kinds.indexOf("max") >= 0);
	assert.ok(kinds.indexOf("sharpen") >= 0);

	var def = maxDatasets.defaultDatasetForBranch(bundle, "somata", {
		preferKind: "max",
	});
	assert.strictEqual(def.kind, "max");

	helpers.rmDir(bundle);
}

function testActiveMaxBeatsSavedRelAndPreferKind() {
	var bundle = helpers.tmpDir("mj-datasets-active-");
	var maxBase = path.join(bundle, "data/counting/03_max");
	var maxDir = path.join(maxBase, "somata", "max", "M528_max_run");
	var sharpenDir = path.join(maxBase, "somata", "sharpen", "M528_sh_run");
	fs.mkdirSync(maxDir, { recursive: true });
	fs.mkdirSync(sharpenDir, { recursive: true });
	fs.writeFileSync(path.join(maxDir, "M528_s061.tif"), Buffer.alloc(16));
	fs.writeFileSync(path.join(sharpenDir, "M528_s061.tif"), Buffer.alloc(16));

	var project = require("../js/project");
	project.setActiveProject(bundle, {
		name: "test",
		roles: { max: "data/counting/03_max" },
		processing: {
			active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
				max: "somata/sharpen/M528_sh_run",
			}),
		},
	});

	try {
		global.sessionStorage.setItem(
			"masonjar.detect.maxDataset",
			"somata/max/M528_max_run",
		);
	} catch (_err) {
		/* ignore */
	}

	var def = maxDatasets.defaultDatasetForBranch(bundle, "somata", {
		preferKind: "max",
		savedRel: "somata/max/M528_max_run",
	});
	assert.strictEqual(def.kind, "sharpen");
	assert.ok(def.rel.indexOf("sharpen") >= 0);

	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testDetectSlugIncludesInputDataset() {
	var slugMax = pipelineRuns.buildDetectRunSlug({
		sortedStems: ["M528_s001"],
		confidence: 0.5,
		tile: 640,
		area: 200,
		eccentricity: 0.5,
		inputDatasetRel: "somata/max/M528_run_a",
	});
	var slugSharpen = pipelineRuns.buildDetectRunSlug({
		sortedStems: ["M528_s001"],
		confidence: 0.5,
		tile: 640,
		area: 200,
		eccentricity: 0.5,
		inputDatasetRel: "somata/sharpen/M528_run_b",
	});
	assert.notStrictEqual(slugMax, slugSharpen);
	assert.ok(slugMax.indexOf("from_") >= 0);
	assert.ok(slugSharpen.indexOf("from_") >= 0);
}

function testTophatSlugWithSource() {
	var slug = pipelineRuns.buildRunSlug("tophat", {
		sortedStems: ["M528_s061"],
		radius: 10,
		gamma: 1.25,
		sourceKind: "sharpen",
		sourceRunRel: "somata/sharpen/M528_r3_a2",
	});
	assert.ok(slug.indexOf("top10") === 0);
	assert.ok(slug.indexOf("from_") >= 0);
}

function testParseSourceRunRel() {
	var meta = maxDatasets.parseSourceRunRel("somata/max/M528_run", "somata");
	assert.strictEqual(meta.source_kind, "max");
	assert.strictEqual(meta.source_run_rel, "max/M528_run");
}

testListBranchesAndDatasets();
testActiveMaxBeatsSavedRelAndPreferKind();
testDetectSlugIncludesInputDataset();
testTophatSlugWithSource();
testParseSourceRunRel();
console.log("test-max-datasets: ok");
