"use strict";

var assert = require("assert");
var path = require("path");
var fs = require("fs");
var helpers = require("./test-helpers");

helpers.ensureLocalStorage();

var pipelineRuns = require("../js/pipeline_runs");
var project = require("../js/project");
var fileIndex = require("../js/file_index");

function testBuildRunSlugStability() {
	var stems = ["M528_s061", "M528_s062"];
	var slug1 = pipelineRuns.buildRunSlug("align", {
		sortedStems: stems,
		spacing: 10,
		whole: "True",
		legacy: "False",
		subsetCount: 2,
	});
	var slug2 = pipelineRuns.buildRunSlug("align", {
		sortedStems: stems,
		spacing: 10,
		whole: "True",
		legacy: "False",
		subsetCount: 2,
	});
	assert.strictEqual(slug1, slug2);
	assert.ok(slug1.indexOf("M528") >= 0);
	assert.ok(slug1.indexOf("_whole") >= 0);
}

function testBuildRunSlugAlignAuto() {
	var slug = pipelineRuns.buildRunSlug("align", {
		sortedStems: ["M457_s001", "M457_s061"],
		spacing: 10,
		whole: "auto",
		legacy: "False",
		subsetCount: 2,
	});
	assert.ok(slug.indexOf("_auto") >= 0, slug);
}

function testResolveRunLeaf() {
	var base = helpers.tmpDir("mj-leaf-");
	var leaf = pipelineRuns.resolveRunLeaf(base, "align", "M528_s061_sp10", false);
	assert.strictEqual(
		leaf,
		path.join(base, "align", "M528_s061_sp10"),
	);
	assert.strictEqual(pipelineRuns.resolveRunLeaf(base, "align", "x", true), base);
	helpers.rmDir(base);
}

function testDiscoverOutputRuns() {
	var base = helpers.tmpDir("mj-disc-");
	var runDir = path.join(base, "align", "M528_s061_sp10");
	fs.mkdirSync(runDir, { recursive: true });
	fs.writeFileSync(path.join(runDir, "Annotation_M528_s061.pkl"), "x");
	var runs = pipelineRuns.discoverOutputRuns(base, "align", 2);
	assert.ok(runs.length >= 1);
	assert.ok(runs[0].rel.indexOf("align") >= 0);
	helpers.rmDir(base);
}

function testDiscoverCziMaxRunDepth() {
	var bundle = helpers.tmpDir("mj-czi-max-");
	var roles = { max: "data/counting/03_max" };
	var runDir = path.join(bundle, roles.max, "somata", "max", "M528_s001-M528_s002");
	fs.mkdirSync(runDir, { recursive: true });
	fs.writeFileSync(path.join(runDir, "M528_s001.tif"), "x");
	fs.mkdirSync(path.join(bundle, ".masonjar"), { recursive: true });
	project.setActiveProject(bundle, {
		name: "test",
		roles: roles,
		processing: { active_runs: pipelineRuns.defaultActiveRuns() },
	});
	var choices = pipelineRuns.listRunChoicesForRole("max");
	assert.ok(
		choices.some(function (c) {
			return c.rel === "somata/max/M528_s001-M528_s002";
		}),
		"CZI nested max run should be discovered at depth 3",
	);
	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testActiveRunScoping() {
	var bundle = helpers.tmpDir("mj-scope-");
	var roles = {
		dapi: "data/counting/00_dapi",
		slices: "data/counting/01_slices",
	};
	var slicesDir = path.join(bundle, roles.slices);
	var nestedA = path.join(slicesDir, "align", "run_a");
	var nestedB = path.join(slicesDir, "align", "run_b");
	fs.mkdirSync(path.join(bundle, roles.dapi), { recursive: true });
	fs.mkdirSync(nestedA, { recursive: true });
	fs.mkdirSync(nestedB, { recursive: true });
	helpers.touchImage(path.join(bundle, roles.dapi), "M528_s027.tif");
	helpers.touchImage(path.join(bundle, roles.dapi), "M528_s028.tif");
	fs.writeFileSync(path.join(nestedA, "Annotation_M528_s027.pkl"), "a");
	fs.writeFileSync(path.join(nestedB, "Annotation_M528_s028.pkl"), "b");

	var activeRuns = pipelineRuns.defaultActiveRuns();
	activeRuns.slices = "align/run_a";

	return fileIndex
		.buildFileIndex(bundle, roles, {
			appRoot: path.join(__dirname, ".."),
			activeRuns: activeRuns,
		})
		.then(function (index) {
			var report = fileIndex.computeMatchReport(index, ["dapi", "slices"]);
			assert.deepStrictEqual(report.matchedSliceIds, ["M528_s027"]);
			assert.ok(report.matchedSliceIds.indexOf("M528_s028") < 0);
			helpers.rmDir(bundle);
		});
}

function testReconcileCziMaxSingleRun() {
	var bundle = helpers.tmpDir("mj-recon-czi-");
	var roles = { max: "data/counting/03_max" };
	var maxRel = "somata/max/M528_s001-M528_s002";
	var runDir = path.join(bundle, roles.max, maxRel);
	fs.mkdirSync(runDir, { recursive: true });
	fs.writeFileSync(path.join(runDir, "M528_s001.tif"), "x");
	var processing = {
		active_runs: pipelineRuns.defaultActiveRuns(),
	};
	var result = pipelineRuns.reconcileProjectRunsOnOpen(bundle, roles, processing);
	assert.strictEqual(result.changed, true);
	assert.strictEqual(result.active_runs.max, maxRel);
	helpers.rmDir(bundle);
}

function testReconcileClearsStaleActiveWithMultipleRuns() {
	var bundle = helpers.tmpDir("mj-recon-stale-");
	var roles = { max: "data/counting/03_max" };
	var runA = path.join(bundle, roles.max, "max", "run_a");
	var runB = path.join(bundle, roles.max, "max", "run_b");
	fs.mkdirSync(runA, { recursive: true });
	fs.mkdirSync(runB, { recursive: true });
	fs.writeFileSync(path.join(runA, "M528_s001.tif"), "x");
	fs.writeFileSync(path.join(runB, "M528_s002.tif"), "x");
	var processing = {
		active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
			max: "max/deleted_run",
		}),
	};
	var result = pipelineRuns.reconcileProjectRunsOnOpen(bundle, roles, processing);
	assert.strictEqual(result.changed, true);
	assert.strictEqual(result.active_runs.max, "");
	var choices = pipelineRuns.discoverRunChoicesForBundle(bundle, roles, "max");
	assert.ok(choices.length >= 2);
	helpers.rmDir(bundle);
}

function testReconcileKeepsValidActive() {
	var bundle = helpers.tmpDir("mj-recon-keep-");
	var roles = { slices: "data/counting/01_slices" };
	var runDir = path.join(bundle, roles.slices, "align", "run_a");
	fs.mkdirSync(runDir, { recursive: true });
	fs.writeFileSync(path.join(runDir, "Annotation_M528_s001.pkl"), "x");
	var processing = {
		active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
			slices: "align/run_a",
		}),
	};
	var result = pipelineRuns.reconcileProjectRunsOnOpen(bundle, roles, processing);
	assert.strictEqual(result.changed, false);
	assert.strictEqual(result.active_runs.slices, "align/run_a");
	helpers.rmDir(bundle);
}

function testReconcileFlatLegacyMax() {
	var bundle = helpers.tmpDir("mj-recon-flat-");
	var roles = { max: "data/counting/03_max" };
	var maxDir = path.join(bundle, roles.max);
	fs.mkdirSync(maxDir, { recursive: true });
	fs.writeFileSync(path.join(maxDir, "M528_s001.tif"), "x");
	var processing = {
		active_runs: pipelineRuns.defaultActiveRuns(),
	};
	var result = pipelineRuns.reconcileProjectRunsOnOpen(bundle, roles, processing);
	assert.strictEqual(result.active_runs.max, "");
	var choices = pipelineRuns.discoverRunChoicesForBundle(bundle, roles, "max");
	assert.ok(
		choices.some(function (c) {
			return c.rel === "";
		}),
		"flat legacy max at role root should be discovered",
	);
	assert.ok(
		pipelineRuns.isStoredActiveRunValid(bundle, roles, "max", ""),
		"flat legacy max is valid when markers are at role root",
	);
	helpers.rmDir(bundle);
}

function testMigrateActivePredictionRun() {
	var runs = pipelineRuns.migrateActiveRuns({
		active_prediction_run: "somata/foo_bar",
	});
	assert.strictEqual(runs.predictions, "somata/foo_bar");
}

function testDedupeBranchRunRel() {
	assert.strictEqual(
		pipelineRuns.dedupeBranchRunRel(
			"intensity/foo/intensity/foo",
			"intensity",
		),
		"intensity/foo",
	);
	assert.strictEqual(
		pipelineRuns.dedupeBranchRunRel(
			"intensity/a/intensity/a/intensity/a",
			"intensity",
		),
		"intensity/a",
	);
	assert.strictEqual(
		pipelineRuns.dedupeBranchRunRel("align/run_a", "align"),
		"align/run_a",
	);
	var runs = pipelineRuns.migrateActiveRuns({
		active_runs: { pkls: "intensity/M465_run/intensity/M465_run" },
	});
	assert.strictEqual(runs.pkls, "intensity/M465_run");
}

function testCollectRunDeleteTargetsDoubled() {
	var bundle = helpers.tmpDir("mj-del-");
	var roles = { pkls: "data/counting/07_pkls" };
	var roleBase = path.join(bundle, roles.pkls);
	var doubled = path.join(roleBase, "intensity", "foo", "intensity", "foo");
	fs.mkdirSync(doubled, { recursive: true });
	fs.writeFileSync(path.join(doubled, "run_manifest.json"), "{}");
	var targets = pipelineRuns.collectRunDeleteTargets(
		bundle,
		roles,
		"pkls",
		"intensity/foo/intensity/foo",
	);
	assert.ok(targets.length >= 1);
	assert.ok(
		targets.some(function (t) {
			return t.abs === doubled;
		}),
	);
	assert.ok(
		pipelineRuns.isSafeRunDeleteTarget(bundle, roleBase, doubled),
		"doubled leaf is safe",
	);
	assert.ok(
		!pipelineRuns.isSafeRunDeleteTarget(bundle, roleBase, roleBase),
		"role base is not deletable",
	);
	helpers.rmDir(bundle);
}

function testDedupeModelBranchRunRel() {
	assert.strictEqual(
		pipelineRuns.dedupeModelBranchRunRel("somata/a/somata/b"),
		"somata/b",
	);
	assert.strictEqual(
		pipelineRuns.dedupeModelBranchRunRel("somata/run_only"),
		"somata/run_only",
	);
	var runs = pipelineRuns.migrateActiveRuns({
		active_runs: {
			predictions: "somata/old/somata/new",
		},
	});
	assert.strictEqual(runs.predictions, "somata/new");
}

function testResolveLogicalPathOutputVsInput() {
	var bundle = helpers.tmpDir("mj-logical-");
	var roles = {
		dapi: "data/counting/00_dapi",
		slices: "data/counting/01_slices",
		predictions: "data/counting/05_predictions",
	};
	var predRun = path.join(bundle, roles.predictions, "somata", "run_a");
	fs.mkdirSync(predRun, { recursive: true });
	fs.writeFileSync(path.join(predRun, "Predictions_M528_s001.pkl"), "x");
	project.setActiveProject(bundle, {
		name: "test",
		roles: roles,
		processing: {
			active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
				predictions: "somata/run_a",
			}),
		},
	});
	assert.strictEqual(
		project.resolveLogicalPathForOutput("predictions"),
		path.join(bundle, roles.predictions),
	);
	assert.strictEqual(
		project.resolveLogicalPathForInput("predictions"),
		predRun,
	);
	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testResolveStepOutputPathDetectNoNest() {
	var bundle = helpers.tmpDir("mj-detect-out-");
	var roles = {
		predictions: "data/counting/05_predictions",
	};
	var existing = path.join(bundle, roles.predictions, "somata", "run_old");
	fs.mkdirSync(existing, { recursive: true });
	fs.writeFileSync(path.join(existing, "Predictions_M528_s001.pkl"), "x");
	project.setActiveProject(bundle, {
		name: "test",
		roles: roles,
		processing: {
			active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
				predictions: "somata/run_old",
			}),
		},
	});
	var predBase = path.join(bundle, roles.predictions);
	var finalOut = pipelineRuns.resolveStepOutputPath("detect", {
		slug: "run_new",
		branchOverride: "somata",
		runMode: "merge",
	});
	assert.strictEqual(finalOut, path.join(predBase, "somata", "run_new"));
	assert.ok(finalOut.indexOf("run_old" + path.sep + "somata") < 0);
	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testResolveStepOutputPathDetectUsesSignalBranch() {
	var bundle = helpers.tmpDir("mj-detect-sig-");
	var roles = {
		predictions: "data/counting/05_predictions",
		max: "data/counting/03_max",
	};
	project.setActiveProject(bundle, {
		name: "test",
		roles: roles,
		processing: { active_runs: pipelineRuns.defaultActiveRuns() },
	});
	var predBase = path.join(bundle, roles.predictions);
	var startersMax = path.join(
		bundle,
		roles.max,
		"starters",
		"max",
		"M465_s001-M465_s054",
	);
	var finalOut = pipelineRuns.resolveStepOutputPath("detect", {
		slug: "run_starters",
		branchOverride: "somata",
		signalBranch: "starters",
		indirAbs: startersMax,
		runMode: "merge",
	});
	assert.strictEqual(
		finalOut,
		path.join(predBase, "starters", "run_starters"),
		"detect output folder is intensity branch, not model name",
	);
	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testResolveStepOutputPathDetectOverwrite() {
	var bundle = helpers.tmpDir("mj-detect-ow-");
	var roles = { predictions: "data/counting/05_predictions" };
	var existing = path.join(bundle, roles.predictions, "somata", "run_old");
	fs.mkdirSync(existing, { recursive: true });
	fs.writeFileSync(path.join(existing, "Predictions_M528_s001.pkl"), "x");
	project.setActiveProject(bundle, {
		name: "test",
		roles: roles,
		processing: {
			active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
				predictions: "somata/run_old",
			}),
			run_modes: { detect: "overwrite" },
		},
	});
	var finalOut = pipelineRuns.resolveStepOutputPath("detect", {
		slug: "run_new",
		branchOverride: "somata",
		runMode: "overwrite",
	});
	assert.strictEqual(finalOut, existing);
	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testResolveStepOutputPathMaxCziSibling() {
	var bundle = helpers.tmpDir("mj-max-out-");
	var roles = {
		max: "data/counting/03_max",
		original_scans: "data/original_scans",
	};
	var existing = path.join(bundle, roles.max, "somata", "max", "run_old");
	fs.mkdirSync(existing, { recursive: true });
	fs.writeFileSync(path.join(existing, "M528_s001.tif"), "x");
	fs.mkdirSync(path.join(bundle, roles.original_scans, "somata"), {
		recursive: true,
	});
	project.setActiveProject(bundle, {
		name: "test",
		roles: roles,
		processing: {
			active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
				max: "somata/max/run_old",
			}),
		},
	});
	var indir = path.join(bundle, roles.original_scans, "somata");
	var finalOut = pipelineRuns.resolveStepOutputPath("max", {
		slug: "run_new",
		runMode: "merge",
		indirAbs: indir,
	});
	assert.strictEqual(
		finalOut,
		path.join(bundle, roles.max, "somata", "max", "run_new"),
	);
	var rel = pipelineRuns.relFromRoleBase("max", finalOut);
	assert.strictEqual(rel, "somata/max/run_new");
	var choices = pipelineRuns.listRunChoicesForRole("max");
	assert.ok(
		choices.some(function (c) {
			return c.rel === "somata/max/run_new" || c.rel === "somata/max/run_old";
		}),
	);
	project.clearActiveProject();
	helpers.rmDir(bundle);
}

function testDiscoverDetectRelParity() {
	var bundle = helpers.tmpDir("mj-disc-detect-");
	var roles = { predictions: "data/counting/05_predictions" };
	var runDir = path.join(bundle, roles.predictions, "somata", "M528_c0p5");
	fs.mkdirSync(runDir, { recursive: true });
	fs.writeFileSync(path.join(runDir, "Predictions_M528_s001.pkl"), "x");
	var base = path.join(bundle, roles.predictions);
	var runs = pipelineRuns.discoverOutputRuns(base, "detect", 2);
	assert.ok(
		runs.some(function (r) {
			return r.rel === "somata/M528_c0p5";
		}),
	);
	helpers.rmDir(bundle);
}

function testRemoveRunForRoleClearsActive() {
	var bundle = helpers.tmpDir("mj-rm-");
	var roles = {
		dapi: project.CANONICAL_ROLES.dapi,
		slices: project.CANONICAL_ROLES.slices,
	};
	var slicesDir = path.join(bundle, roles.slices);
	var runDir = path.join(slicesDir, "align", "stuck_run");
	fs.mkdirSync(runDir, { recursive: true });
	fs.writeFileSync(path.join(runDir, "run_manifest.json"), '{"status":"pending"}');
	fs.mkdirSync(path.join(bundle, ".masonjar"), { recursive: true });
	project.setActiveProject(bundle, {
		name: "test",
		roles: roles,
		processing: {
			active_runs: Object.assign(pipelineRuns.defaultActiveRuns(), {
				slices: "align/stuck_run",
			}),
		},
	});
	var result = pipelineRuns.removeRunForRole("slices", "align/stuck_run");
	assert.strictEqual(result.ok, true);
	assert.ok(!fs.existsSync(runDir));
	assert.strictEqual(pipelineRuns.getActiveRunRelForRole("slices"), "");
	project.clearActiveProject();
	helpers.rmDir(bundle);
}

var tests = [
	testBuildRunSlugStability,
	testBuildRunSlugAlignAuto,
	testResolveRunLeaf,
	testDiscoverOutputRuns,
	testDiscoverCziMaxRunDepth,
	testReconcileCziMaxSingleRun,
	testReconcileClearsStaleActiveWithMultipleRuns,
	testReconcileKeepsValidActive,
	testReconcileFlatLegacyMax,
	testMigrateActivePredictionRun,
	testDedupeBranchRunRel,
	testDedupeModelBranchRunRel,
	testResolveLogicalPathOutputVsInput,
	testResolveStepOutputPathDetectNoNest,
	testResolveStepOutputPathDetectUsesSignalBranch,
	testResolveStepOutputPathDetectOverwrite,
	testResolveStepOutputPathMaxCziSibling,
	testDiscoverDetectRelParity,
	testActiveRunScoping,
	testCollectRunDeleteTargetsDoubled,
	testRemoveRunForRoleClearsActive,
	testMigrateOrphanMaxFamilyLeaves,
];

function testMigrateOrphanMaxFamilyLeaves() {
	var bundle = helpers.tmpDir("mj-orphan-");
	try {
		fs.mkdirSync(path.join(bundle, ".masonjar"), { recursive: true });
		var roleBase = path.join(bundle, "data", "counting", "03_max");
		var somataMax = path.join(roleBase, "somata", "max", "M528_max1");
		fs.mkdirSync(somataMax, { recursive: true });
		fs.writeFileSync(path.join(somataMax, "M528_s001.tif"), "max");
		var orphanSharpen = path.join(roleBase, "sharpen", "M528_sharp1");
		fs.mkdirSync(orphanSharpen, { recursive: true });
		fs.writeFileSync(path.join(orphanSharpen, "M528_s001.tif"), "sharp");
		var processing = {
			active_runs: { max: "sharpen/M528_sharp1" },
		};
		var result = pipelineRuns.migrateOrphanMaxFamilyLeaves(
			bundle,
			pipelineRuns.CANONICAL_ROLES,
			processing,
			{},
		);
		assert.ok(result.changed, "should migrate orphan");
		assert.strictEqual(result.moved.length, 1);
		assert.strictEqual(result.moved[0].to, "somata/sharpen/M528_sharp1");
		assert.ok(
			fs.existsSync(path.join(roleBase, "somata", "sharpen", "M528_sharp1", "M528_s001.tif")),
		);
		assert.ok(!fs.existsSync(orphanSharpen));
		assert.strictEqual(result.active_runs.max, "somata/sharpen/M528_sharp1");
		// Idempotent
		var again = pipelineRuns.migrateOrphanMaxFamilyLeaves(
			bundle,
			pipelineRuns.CANONICAL_ROLES,
			{ active_runs: result.active_runs },
			{},
		);
		assert.ok(!again.changed);
		assert.strictEqual(again.moved.length, 0);
	} finally {
		helpers.rmDir(bundle);
	}
}

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
		console.log("test-pipeline-runs.js: OK (" + tests.length + " tests)");
	});
}

runAll().catch(function (err) {
	console.error(err);
	process.exit(1);
});
