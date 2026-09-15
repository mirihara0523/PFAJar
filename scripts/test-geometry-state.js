"use strict";

var assert = require("assert");
var fs = require("fs");
var os = require("os");
var path = require("path");

var orientGeometry = require("../js/orient_geometry");
var geometryState = require("../js/geometry_state");

function tempBundle() {
	var root = fs.mkdtempSync(path.join(os.tmpdir(), "mj-geo-state-"));
	var meta = path.join(root, ".masonjar");
	fs.mkdirSync(meta, { recursive: true });
	var prev = path.join(root, "data/counting/_previews");
	fs.mkdirSync(prev, { recursive: true });
	return root;
}

function testHasPendingOps() {
	var czi = {
		geometry: {
			A: { ops: ["rot90"] },
			B: { ops: [] },
		},
	};
	assert.strictEqual(geometryState.hasPendingGeometry(czi, ["A", "B"]), true);
	assert.strictEqual(geometryState.hasPendingGeometry(czi, ["B"]), false);
}

function testInterruptedFromLastResult() {
	var bundle = tempBundle();
	var czi = {
		geometry: { S1: { ops: ["rot90"] } },
		config_fingerprint: "fp1",
	};
	fs.writeFileSync(
		path.join(bundle, ".masonjar", geometryState.META_LAST_RESULT),
		JSON.stringify({
			ok: false,
			changed: 1,
			files_total: 10,
			config_fingerprint: "fp1",
			geometry_hash: geometryState.geometryOnlyHash(czi),
		}),
		"utf8",
	);
	fs.writeFileSync(
		path.join(bundle, ".masonjar", geometryState.META_PROGRESS),
		JSON.stringify({
			config_fingerprint: "fp1",
			geometry_hash: geometryState.geometryOnlyHash(czi),
			completed: 1,
			files_total: 10,
			completed_paths: [],
		}),
		"utf8",
	);
	czi.config_fingerprint = "fp1";
	var st = geometryState.assessGeometryApplyState(bundle, czi, {
		sliceIds: ["S1"],
		previewHealth: { needsRepair: false, canApply: true },
	});
	assert.strictEqual(st.policyState, "interrupted");
	assert.strictEqual(st.allowApply, false);
	assert.strictEqual(st.showRebuildWizard, true);
}

function testFreshPendingAllowsApply() {
	var bundle = tempBundle();
	var czi = {
		geometry: { S1: { ops: ["flipX"] } },
	};
	var st = geometryState.assessGeometryApplyState(bundle, czi, {
		sliceIds: ["S1"],
		previewHealth: { needsRepair: false, canApply: true },
	});
	assert.strictEqual(st.policyState, "healthy");
	assert.strictEqual(st.allowApply, true);
}

function testReapplyStackRisk() {
	var bundle = tempBundle();
	var czi = {
		geometry: {
			S1: { ops: ["rot90"] },
			S2: { ops: [] },
		},
		geometry_applied_at: "2026-01-01T00:00:00.000Z",
	};
	var st = geometryState.assessGeometryApplyState(bundle, czi, {
		sliceIds: ["S1", "S2"],
		previewHealth: { needsRepair: false, canApply: true },
	});
	assert.strictEqual(st.policyState, "interrupted");
	assert.ok(st.signals.indexOf("reapply_stack_risk") >= 0);
	assert.strictEqual(st.allowApply, false);
}

function testReextractPartialApplyAllowsConfirm() {
	var bundle = tempBundle();
	var meta = path.join(bundle, ".masonjar");
	fs.mkdirSync(meta, { recursive: true });
	fs.writeFileSync(
		path.join(meta, "geometry_apply_progress.json"),
		JSON.stringify({
			config_fingerprint: "fp",
			geometry_hash: "gh",
			files_total: 20,
			completed: 20,
		}),
		"utf8",
	);
	var czi = {
		geometry: {
			S1: { ops: ["rot90"] },
			S2: { ops: [] },
		},
		reextract_geometry_scope: {
			S1: ["signal_somata", "signal_nuclei"],
		},
	};
	var st = geometryState.assessGeometryApplyState(bundle, czi, {
		sliceIds: ["S1", "S2"],
		previewHealth: { needsRepair: false, canApply: true },
	});
	assert.strictEqual(st.policyState, "reextract_partial");
	assert.strictEqual(st.allowApply, true);
	assert.strictEqual(st.partialReextractApply, true);
	assert.strictEqual(st.pendingInScope, 1);
	assert.strictEqual(st.reextractScopeIncludesDapi, false);
}

function testLiveEditingFreshImportStaysHealthy() {
	// Regression (user-reported): on a freshly extracted multi-channel bundle the
	// preview/DAPI PNGs are written minutes apart (one phase per channel over a
	// slow NAS), and the app persists in-progress geometry to czi_import_config.json.
	// Neither of those is evidence of an interrupted apply. With no apply meta
	// files and no geometry_applied_at, interactive editing must stay "healthy"
	// and allow Apply — whether 1, some, or all slices are rotated.
	var bundle = tempBundle();
	var prev = path.join(bundle, "data/counting/_previews");
	var dapiDir = path.join(bundle, "data/counting/00_dapi");
	fs.mkdirSync(dapiDir, { recursive: true });
	var ids = [];
	var now = Date.now() / 1000;
	for (var i = 1; i <= 5; i++) {
		var sid = "S" + i;
		ids.push(sid);
		var somata = path.join(prev, sid + "_somata.png");
		var dpv = path.join(prev, sid + "_dapi.png");
		var dapi = path.join(dapiDir, sid + ".png");
		[somata, dpv, dapi].forEach(function (p) {
			fs.writeFileSync(p, "png");
		});
		fs.utimesSync(somata, now - 300, now - 300);
		fs.utimesSync(dpv, now, now);
		fs.utimesSync(dapi, now, now);
	}
	var origDir = path.join(bundle, "data/original_scans/somata");
	fs.mkdirSync(origDir, { recursive: true });
	fs.writeFileSync(path.join(origDir, "S1.tif"), "tif");

	// 1 of 5 rotated, with the in-progress geometry persisted to config (as the
	// live app does) — must stay healthy.
	var czi1 = { geometry: { S1: { ops: ["rot90"] } } };
	geometryState.writeCziImportConfig(bundle, czi1);
	var st1 = geometryState.assessGeometryApplyState(bundle, czi1, {
		sliceIds: ids,
		previewHealth: { needsRepair: false, canApply: true },
	});
	assert.strictEqual(st1.policyState, "healthy");
	assert.deepStrictEqual(st1.signals, []);
	assert.strictEqual(st1.allowApply, true);

	// All 5 rotated — still healthy.
	var geomAll = {};
	ids.forEach(function (sid) {
		geomAll[sid] = { ops: ["rot90"] };
	});
	var cziAll = { geometry: geomAll };
	geometryState.writeCziImportConfig(bundle, cziAll);
	var stAll = geometryState.assessGeometryApplyState(bundle, cziAll, {
		sliceIds: ids,
		previewHealth: { needsRepair: false, canApply: true },
	});
	assert.strictEqual(stAll.policyState, "healthy");
	assert.strictEqual(stAll.allowApply, true);
}

function testBuildRepairTargets() {
	var queue = {
		slices: [
			{
				slice_id: "S1",
				pending_ops: ["rot90"],
				confirmed_ops: ["rot90"],
				channels: [
					{
						branch: "dapi",
						rel_path: "data/counting/_previews/S1_dapi.png",
						suggested_strategy: "derivatives_from_original",
					},
				],
			},
		],
	};
	var targets = geometryState.buildRepairTargetsFromQueue(queue);
	assert.strictEqual(targets.length, 1);
	assert.strictEqual(targets[0].slice_id, "S1");
	assert.deepStrictEqual(targets[0].ops, ["rot90"]);
}

function testBuildRepairTargetsUpgradesSkipWhenConfirmed() {
	var queue = {
		slices: [
			{
				slice_id: "S1",
				issue: "low_confidence",
				confirmed_ops: ["rot90", "rot90"],
				channels: [
					{
						branch: "dapi",
						rel_path: "data/counting/_previews/S1_dapi.png",
						suggested_strategy: "skip",
					},
					{
						branch: "somata",
						rel_path: "data/counting/_previews/S1_somata.png",
						suggested_strategy: "skip",
					},
				],
			},
		],
	};
	var targets = geometryState.buildRepairTargetsFromQueue(queue);
	assert.strictEqual(targets.length, 2);
	assert.strictEqual(targets[0].strategy, "derivatives_from_original");
	assert.strictEqual(targets[1].strategy, "derivatives_from_original");
}

function testBuildConfirmedGeometryFromQueue() {
	var queue = {
		slices: [
			{ slice_id: "S1", confirmed_ops: ["rot90", "rot90"] },
			{ slice_id: "S2", confirmed_ops: null },
			{ slice_id: "S3", confirmed_ops: [] },
		],
	};
	var geometry = geometryState.buildConfirmedGeometryFromQueue(queue);
	assert.deepStrictEqual(Object.keys(geometry).sort(), ["S1"]);
	assert.deepStrictEqual(geometry.S1.ops, ["rot90", "rot90"]);
}

function testSlicesAwaitingReviewConfirmation() {
	var queue = {
		slices: [
			{ slice_id: "S1", needs_manual_review: true, confirmed_ops: null },
			{ slice_id: "S2", needs_manual_review: true, confirmed_ops: ["rot90"] },
			{ slice_id: "S3", needs_manual_review: false, confirmed_ops: null },
			{ slice_id: "S4", needs_manual_review: true, confirmed_ops: [] },
		],
	};
	var awaiting = geometryState.slicesAwaitingReviewConfirmation(queue);
	assert.strictEqual(awaiting.length, 1);
	assert.strictEqual(awaiting[0].slice_id, "S1");
}

function testBuildAutoRepairTargetsExcludesConfirmed() {
	var queue = {
		slices: [
			{
				slice_id: "S1",
				confirmed_ops: ["rot90", "rot90"],
				issue: "low_confidence",
				channels: [
					{
						branch: "dapi",
						rel_path: "data/counting/_previews/S1_dapi.png",
						suggested_strategy: "skip",
					},
				],
			},
			{
				slice_id: "S2",
				issue: "cross_channel_mismatch",
				channels: [
					{
						branch: "dapi",
						rel_path: "data/counting/_previews/S2_dapi.png",
						suggested_strategy: "derivatives_from_original",
					},
				],
			},
		],
	};
	var auto = geometryState.buildAutoRepairTargetsFromQueue(queue);
	assert.strictEqual(auto.length, 1);
	assert.strictEqual(auto[0].slice_id, "S2");
}

function testBuildAutoRepairTargetsExcludesNoRotationSettled() {
	var queue = {
		slices: [
			{
				slice_id: "S1",
				confirmed_ops: [],
				issue: "low_confidence",
				channels: [
					{
						branch: "dapi",
						rel_path: "data/counting/_previews/S1_dapi.png",
						suggested_strategy: "derivatives_from_original",
					},
				],
			},
		],
	};
	assert.strictEqual(geometryState.buildAutoRepairTargetsFromQueue(queue).length, 0);
	assert.strictEqual(geometryState.buildRepairTargetsFromQueue(queue).length, 0);
}

function testBuildRepairGeometryFromQueue() {
	var queue = {
		slices: [
			{
				slice_id: "S1",
				auto_repairable: true,
				suggested_ops: ["rot90", "rot90"],
				issue: "consistent_reorient",
			},
			{
				slice_id: "S2",
				confirmed_ops: ["flipX"],
				needs_manual_review: false,
			},
			{
				slice_id: "S3",
				confirmed_ops: [],
				issue: "low_confidence",
			},
		],
	};
	var geometry = geometryState.buildRepairGeometryFromQueue(queue);
	assert.deepStrictEqual(Object.keys(geometry).sort(), ["S1", "S2"]);
	assert.deepStrictEqual(geometry.S1.ops, ["rot90", "rot90"]);
	assert.deepStrictEqual(geometry.S2.ops, ["flipX"]);
}

function testWriteCziImportConfigIncludesGeometryHash() {
	var bundle = tempBundle();
	var czi = {
		geometry: { S1: { ops: ["rot90"] } },
		files: [],
	};
	geometryState.writeCziImportConfig(bundle, czi, { apply_source: "test" });
	var cfgPath = path.join(bundle, ".masonjar", "czi_import_config.json");
	var raw = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
	assert.strictEqual(raw.czi_import.apply_source, "test");
	assert.strictEqual(raw.czi_import.geometry_hash, geometryState.geometryOnlyHash(czi));
	assert.ok(raw.czi_import.config_fingerprint);
}

function testFinalizeGeometryAfterApplyClearsPending() {
	var bundle = tempBundle();
	var czi = {
		geometry: {
			S1: { ops: ["rot90"] },
			S2: { ops: ["flipX"] },
		},
	};
	geometryState.finalizeGeometryAfterApply(bundle, czi, {
		sliceIds: ["S1", "S2"],
		payload: { files_total: 4 },
		applySource: "batch",
	});
	assert.strictEqual(orientGeometry.countNonIdentityGeometry(czi.geometry, ["S1", "S2"]), 0);
	assert.ok(czi.geometry_applied_at);
	assert.strictEqual(czi.geometry_applied_files_total, 4);
	var cfg = JSON.parse(
		fs.readFileSync(path.join(bundle, ".masonjar", "czi_import_config.json"), "utf8"),
	);
	assert.strictEqual(cfg.czi_import.apply_source, "batch");
	assert.strictEqual(cfg.czi_import.geometry_hash, geometryState.geometryOnlyHash(czi));
}

function testReconcileClearsStalePendingAfterSuccessfulApply() {
	var bundle = tempBundle();
	var dapiDir = path.join(bundle, "data/counting/00_dapi");
	fs.mkdirSync(dapiDir, { recursive: true });
	fs.writeFileSync(path.join(dapiDir, "S1.png"), "png");
	var czi = {
		geometry: { S1: { ops: ["rot90"] } },
		geometry_applied_at: "2026-01-01T00:00:00.000Z",
		slice_order: [{ ordinal: 1, sliceId: "S1", path: "", scene_index: 0 }],
	};
	var hash = geometryState.geometryOnlyHash(czi);
	fs.writeFileSync(
		path.join(bundle, ".masonjar", geometryState.META_LAST_RESULT),
		JSON.stringify({ ok: true, geometry_hash: hash, files_total: 2 }),
		"utf8",
	);
	var projectData = { settings: { czi_import: czi } };
	var result = geometryState.reconcileGeometryOnOpen(bundle, projectData);
	assert.strictEqual(result.changed, true);
	assert.strictEqual(geometryState.hasPendingGeometry(czi, ["S1"]), false);
}

function testBatchFinalizeHelperPath() {
	var batchQueuePath = path.join(__dirname, "..", "batch_queue.js");
	assert.ok(fs.existsSync(batchQueuePath), "batch_queue.js must exist for batch finalize wiring");
	var src = fs.readFileSync(batchQueuePath, "utf8");
	assert.ok(src.indexOf("finalizeBatchApplyGeometry") >= 0);
	assert.ok(src.indexOf('path.join(__dirname, "js", "geometry_state")') >= 0);
}

function run() {
	testHasPendingOps();
	testInterruptedFromLastResult();
	testFreshPendingAllowsApply();
	testReapplyStackRisk();
	testReextractPartialApplyAllowsConfirm();
	testLiveEditingFreshImportStaysHealthy();
	testBuildRepairTargets();
	testBuildRepairTargetsUpgradesSkipWhenConfirmed();
	testBuildConfirmedGeometryFromQueue();
	testSlicesAwaitingReviewConfirmation();
	testBuildAutoRepairTargetsExcludesConfirmed();
	testBuildAutoRepairTargetsExcludesNoRotationSettled();
	testBuildRepairGeometryFromQueue();
	testWriteCziImportConfigIncludesGeometryHash();
	testFinalizeGeometryAfterApplyClearsPending();
	testReconcileClearsStalePendingAfterSuccessfulApply();
	testBatchFinalizeHelperPath();
	console.log("test-geometry-state: PASS");
}

run();
