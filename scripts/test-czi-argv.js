"use strict";

var assert = require("assert");
var cziArgv = require("../js/czi_argv");

function testImportArgsWithSpaces() {
	var args = [];
	cziArgv.appendCziPathArgs(
		args,
		"Z:\\Matt Jacobs\\masonjar_projects\\M465_masonjar",
		"Z:\\Matt Jacobs\\masonjar_projects\\M465_masonjar\\.masonjar\\czi_import_config.json",
	);
	assert.deepStrictEqual(args, [
		"-b",
		"Z:\\Matt Jacobs\\masonjar_projects\\M465_masonjar",
		"-j",
		"Z:\\Matt Jacobs\\masonjar_projects\\M465_masonjar\\.masonjar\\czi_import_config.json",
	]);
	for (var i = 0; i < args.length; i++) {
		assert.notStrictEqual(args[i][0], " ", "token must not start with space: " + args[i]);
		assert.notStrictEqual(args[i][args.length - 1], " ", "token must not end with space");
	}
}

function testProbeArgsWithSpaces() {
	var args = [];
	cziArgv.appendCziInputArg(args, " Z:\\Matt Jacobs\\tape_007\\scans ");
	assert.deepStrictEqual(args, ["-i", "Z:\\Matt Jacobs\\tape_007\\scans"]);
}

function testTrimBundleOnly() {
	var args = [];
	cziArgv.appendCziPathArgs(args, "  C:\\bundle_masonjar  ", "");
	assert.deepStrictEqual(args, ["-b", "C:\\bundle_masonjar"]);
}

function testIntensityArgsWithSpaces() {
	var args = [];
	var anno =
		"Z:\\Matt Jacobs\\masonjar_projects\\M465_masonjar\\data\\counting\\01_slices\\align\\M465_s001-M465_s005_h83c9_sp100_half_sub5";
	cziArgv.appendFlagPathArg(args, "-i", "Z:\\Matt Jacobs\\max\\run");
	cziArgv.appendFlagPathArg(args, "-o", "Z:\\Matt Jacobs\\07_pkls\\intensity\\run");
	cziArgv.appendFlagPathArg(args, "-a", anno);
	args.push("-w", "True");
	assert.deepStrictEqual(args[0], "-i");
	assert.strictEqual(args[4], "-a");
	assert.strictEqual(args[5], anno);
	assert.strictEqual(args[6], "-w");
}

testImportArgsWithSpaces();
testProbeArgsWithSpaces();
testTrimBundleOnly();
testIntensityArgsWithSpaces();
console.log("test-czi-argv.js: OK");
