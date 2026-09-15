"use strict";

var assert = require("assert");
var path = require("path");
var fs = require("fs");
var helpers = require("./test-helpers");

helpers.ensureLocalStorage();

var importHandoff = require("../js/import_handoff");
var cziImport = require("../js/czi_import");
var pipelineRuns = require("../js/pipeline_runs");
var project = require("../js/project");

function testImportHandoffState() {
	var bundle = helpers.tmpDir("mj-handoff-");
	var roles = {
		dapi: "data/counting/00_dapi",
		max: "data/counting/03_max",
		slices: "data/counting/01_slices",
	};
	var maxRel = "somata/max/M528_s001-M528_s002";
	var maxDir = path.join(bundle, roles.max, maxRel);
	fs.mkdirSync(path.join(bundle, roles.dapi), { recursive: true });
	fs.mkdirSync(maxDir, { recursive: true });
	fs.writeFileSync(path.join(bundle, roles.dapi, "M528_s001.png"), "x");
	fs.writeFileSync(path.join(maxDir, "M528_s001.tif"), "x");
	fs.writeFileSync(
		path.join(maxDir, "run_manifest.json"),
		JSON.stringify({ source: "czi_import" }),
	);
	fs.mkdirSync(path.join(bundle, cziImport.PREVIEWS_REL), { recursive: true });
	fs.writeFileSync(
		path.join(bundle, cziImport.PREVIEWS_REL, "M528_s001_dapi.png"),
		"x",
	);
	fs.mkdirSync(path.join(bundle, ".masonjar"), { recursive: true });

	var projectJson = {
		name: "test",
		roles: roles,
		settings: {
			czi_import: {
				max_runs: { signal_somata: maxRel },
				files: [{ scenes: [{ sliceId: "M528_s001" }] }],
				geometry_applied_at: "2026-01-01T00:00:00.000Z",
			},
		},
		processing: {
			active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
				max: maxRel,
			}),
		},
	};
	project.setActiveProject(bundle, projectJson);

	var state = importHandoff.getImportHandoffState(bundle, projectJson);
	assert.strictEqual(state.complete, true);
	assert.strictEqual(state.dapiCount, 1);
	assert.strictEqual(state.previewCount, 1);
	assert.strictEqual(state.maxRunRel, maxRel);
	assert.strictEqual(state.needsAlignment, true);
	assert.ok(importHandoff.shouldShowImportNextSteps(projectJson, null, bundle));

	var alignDir = path.join(bundle, roles.slices, "align", "run_a");
	fs.mkdirSync(alignDir, { recursive: true });
	fs.writeFileSync(path.join(alignDir, "Annotation_M528_s001.pkl"), "x");
	projectJson.processing.active_runs.slices = "align/run_a";
	project.setActiveProject(bundle, projectJson);
	assert.ok(!importHandoff.shouldShowImportNextSteps(projectJson, null, bundle));

	project.clearActiveProject();
	helpers.rmDir(bundle);
}

var tests = [testImportHandoffState];

function runAll() {
	for (var i = 0; i < tests.length; i++) {
		tests[i]();
	}
	console.log("test-import-handoff.js: OK (" + tests.length + " tests)");
}

runAll();
