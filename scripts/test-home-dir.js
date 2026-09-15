"use strict";

var assert = require("assert");
var fs = require("fs");
var path = require("path");
var os = require("os");
var homeDir = require("../js/home_dir");
var helpers = require("./test-helpers");

function testMasonHomePath() {
	var root = path.join(os.tmpdir(), "mj-home-" + Date.now());
	var mason = homeDir.masonHomePath(root);
	assert.strictEqual(mason, path.join(root, ".masonjar"));
	assert.strictEqual(
		homeDir.legacyHomePath(root),
		path.join(root, ".belljar"),
	);
	helpers.rmDir(root);
}

function testEnvIsReady() {
	var root = helpers.tmpDir("mj-env-ready-");
	var mason = homeDir.masonHomePath(root);
	fs.mkdirSync(path.join(mason, "benv"), { recursive: true });
	assert.strictEqual(homeDir.envIsReady(mason), true);
	assert.strictEqual(homeDir.envIsReady(path.join(root, "empty")), false);
	helpers.rmDir(root);
}

function testNeedsLegacyHomeMigration() {
	var root = helpers.tmpDir("mj-migrate-");
	var mason = homeDir.masonHomePath(root);
	var legacy = homeDir.legacyHomePath(root);
	fs.mkdirSync(path.join(legacy, "python"), { recursive: true });
	assert.strictEqual(homeDir.needsLegacyHomeMigration(root), true);

	fs.mkdirSync(path.join(mason, "benv"), { recursive: true });
	assert.strictEqual(homeDir.needsLegacyHomeMigration(root), false);

	helpers.rmDir(root);
}

function testResolveHomeDirAlwaysMason() {
	var root = helpers.tmpDir("mj-always-mason-");
	var legacy = homeDir.legacyHomePath(root);
	fs.mkdirSync(path.join(legacy, "benv"), { recursive: true });
	assert.strictEqual(homeDir.masonHomePath(root), path.join(root, ".masonjar"));
	assert.strictEqual(homeDir.needsLegacyHomeMigration(root), true);
	helpers.rmDir(root);
}

function main() {
	testMasonHomePath();
	testEnvIsReady();
	testNeedsLegacyHomeMigration();
	testResolveHomeDirAlwaysMason();
	console.log("test-home-dir.js: OK");
}

main();
