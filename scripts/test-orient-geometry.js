"use strict";

var assert = require("assert");
var fs = require("fs");
var os = require("os");
var path = require("path");
var cziImport = require("../js/czi_import");
var orientGeometry = require("../js/orient_geometry");

function testResetGeometryMap() {
	var map = {
		A: { ops: ["rot90", "flipX"] },
		B: { ops: ["rot90", "rot90"] },
		C: { ops: [] },
	};
	var out = orientGeometry.resetGeometryMap(map, ["A", "B", "D"]);
	assert.strictEqual(out, map);
	assert.deepStrictEqual(out.A, { ops: [] });
	assert.deepStrictEqual(out.B, { ops: [] });
	assert.deepStrictEqual(out.C, { ops: [] });
	assert.strictEqual(out.D.ops.length, 0);
}

function testCountNonIdentityAfterReset() {
	var map = {
		A: { ops: ["rot90"] },
		B: { ops: ["flipX"] },
	};
	orientGeometry.resetGeometryMap(map, ["A", "B"]);
	assert.strictEqual(orientGeometry.countNonIdentityGeometry(map, ["A", "B"]), 0);
}

function testOrientPreviewHintText() {
	assert.match(
		orientGeometry.orientPreviewHintText(null, 1),
		/CSS preview/,
	);
	assert.match(
		orientGeometry.orientPreviewHintText("2026-01-01T00:00:00.000Z", 0),
		/on-disk previews/,
	);
	assert.match(orientGeometry.orientPreviewHintText(null, 0), /on-disk previews/);
}

function testIsIdentityGeometry() {
	assert.strictEqual(orientGeometry.isIdentityGeometry({ ops: [] }), true);
	assert.strictEqual(
		orientGeometry.isIdentityGeometry({ rotate: 360, flipX: false, flipY: false }),
		true,
	);
	assert.strictEqual(
		orientGeometry.isIdentityGeometry({ ops: ["rot90"] }),
		false,
	);
}

function testLegacyToOpsMigration() {
	assert.deepStrictEqual(orientGeometry.legacyToOps({ rotate: 90, flipX: true }), [
		"rot90",
		"flipX",
	]);
	assert.deepStrictEqual(orientGeometry.legacyToOps({ rotate: 180, flipY: true }), [
		"rot90",
		"rot90",
		"flipY",
	]);
}

function testRot90ThenFlipXAppendsOps() {
	var geom = orientGeometry.defaultGeometry();
	geom = orientGeometry.applyGeometryAction(geom, "rot90");
	geom = orientGeometry.applyGeometryAction(geom, "flipX");
	assert.deepStrictEqual(geom.ops, ["rot90", "flipX"]);
	assert.strictEqual(orientGeometry.isIdentityGeometry(geom), false);
}

function testGeometryCssTransformUsesMatrix() {
	var geom = { ops: ["rot90", "flipX"] };
	var css = orientGeometry.geometryCssTransform(geom);
	assert.match(css, /^matrix\(/);
}

function parseMatrix(css) {
	var m = /^matrix\(([^)]+)\)/.exec(css);
	return m[1].split(",").map(function (v) {
		return Math.round(Number(v.trim()) * 1000) / 1000;
	});
}

// Lock WYSIWYG invariant: the CSS preview must compose ops in the SAME order
// numpy apply_geometry.py applies them (ops[0] applied first to the image).
// Verified against py/apply_geometry.py apply_ops_to_array on an asymmetric
// array: rot90 -> (x+ to screen-down, y+ to screen-left) = 0,1,-1,0;
// [rot90, flipX] -> (x+ down, y+ right) = 0,1,1,0 (a screen left-right flip of
// the rotated image, NOT a flip in the original axes).
function testGeometryCssTransformMatchesNumpyOrder() {
	var rot = parseMatrix(orientGeometry.geometryCssTransform({ ops: ["rot90"] }));
	assert.deepStrictEqual(rot.slice(0, 4), [0, 1, -1, 0]);
	var rotFlip = parseMatrix(orientGeometry.geometryCssTransform({ ops: ["rot90", "flipX"] }));
	assert.deepStrictEqual(rotFlip.slice(0, 4), [0, 1, 1, 0]);
	// User-reported chain: rot90, flipX, then two more rot90 -> 180deg of [rot90,flipX].
	// Verified against py/apply_geometry.py apply_ops_to_array (same 0,-1,-1,0).
	var chain = parseMatrix(
		orientGeometry.geometryCssTransform({ ops: ["rot90", "flipX", "rot90", "rot90"] }),
	);
	assert.deepStrictEqual(chain.slice(0, 4), [0, -1, -1, 0]);
}

function testCloneGeometryDeepCopy() {
	var a = { ops: ["rot90", "flipX"] };
	var b = orientGeometry.cloneGeometry(a);
	b.ops.push("flipY");
	assert.deepStrictEqual(a.ops, ["rot90", "flipX"]);
}

function testCopyFirstTileGeometryToAll() {
	var map = {
		A: { ops: ["rot90", "flipX"] },
		B: { ops: ["flipY"] },
		C: { ops: [] },
	};
	assert.strictEqual(
		orientGeometry.copyFirstTileGeometryToAll(map, ["A", "B", "C"], null, null),
		true,
	);
	assert.deepStrictEqual(map.B, map.A);
	assert.deepStrictEqual(map.C, map.A);
	map.B.ops.push("flipY");
	assert.deepStrictEqual(map.A.ops, ["rot90", "flipX"]);
}

function testOrientPostApplySummaryText() {
	var text = orientGeometry.orientPostApplySummaryText("2026-05-21T12:00:00.000Z", 42);
	assert.match(text, /Last applied:/);
	assert.match(text, /42 file/);
	assert.match(text, /no pending edits/);
}

function testFindGeometryKeysWithoutPreviewFiles() {
	var tmp = fs.mkdtempSync(path.join(os.tmpdir(), "mj-orient-"));
	var bundle = path.join(tmp, "M528_masonjar");
	var dapiDir = path.join(bundle, "data/counting/00_dapi");
	fs.mkdirSync(dapiDir, { recursive: true });
	fs.writeFileSync(path.join(dapiDir, "M528_s001.png"), Buffer.from([0]));
	var geometry = {
		M528_s001: { ops: ["rot90"] },
		M528_orphan: { ops: ["rot90"] },
	};
	var orphans = cziImport.findGeometryKeysWithoutPreviewFiles(bundle, geometry, [
		"M528_s001",
	]);
	assert.deepStrictEqual(orphans, ["M528_orphan"]);
	fs.rmSync(tmp, { recursive: true, force: true });
}

function run() {
	testResetGeometryMap();
	testCountNonIdentityAfterReset();
	testOrientPreviewHintText();
	testIsIdentityGeometry();
	testLegacyToOpsMigration();
	testRot90ThenFlipXAppendsOps();
	testGeometryCssTransformUsesMatrix();
	testGeometryCssTransformMatchesNumpyOrder();
	testCloneGeometryDeepCopy();
	testCopyFirstTileGeometryToAll();
	testOrientPostApplySummaryText();
	testFindGeometryKeysWithoutPreviewFiles();
	console.log("test-orient-geometry.js: all passed");
}

run();
