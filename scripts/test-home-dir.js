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
	assert.strictEqual(mason, path.join(root, ".pfajar"));
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
	fs.mkdirSync(path.join(legacy, "models"), { recursive: true });
	assert.strictEqual(homeDir.needsLegacyHomeMigration(root), true);

	fs.mkdirSync(path.join(mason, "benv"), { recursive: true });
	assert.strictEqual(homeDir.needsLegacyHomeMigration(root), false);

	helpers.rmDir(root);
}

function testResolveHomeDirAlwaysMason() {
	var root = helpers.tmpDir("mj-always-mason-");
	var legacy = homeDir.legacyHomePath(root);
	fs.mkdirSync(path.join(legacy, "models"), { recursive: true });
	assert.strictEqual(homeDir.masonHomePath(root), path.join(root, ".pfajar"));
	assert.strictEqual(homeDir.needsLegacyHomeMigration(root), true);
	helpers.rmDir(root);
}

async function testCopyDataNotRuntime() {
	var root = helpers.tmpDir("pfa-isolation-");
	try {
		var original = path.join(root, ".masonjar");
		var pfa = homeDir.masonHomePath(root);
		for (var dir of ["models", "embeddings", "nrrd", "python", "benv/Lib/site-packages"]) {
			fs.mkdirSync(path.join(original, dir), { recursive: true });
			fs.writeFileSync(path.join(original, dir, "marker"), dir);
		}
		var guard = path.join(original, "benv/Lib/site-packages/sitecustomize.py");
		fs.writeFileSync(guard, "raise SystemExit('Mason Jar only')");
		fs.writeFileSync(path.join(original, "manifest.json"), '{"models":"v952","nrrd":"v91","embeddings":"v6"}');
		assert.strictEqual(homeDir.findResourceSource(root), original);
		assert.ok(homeDir.needsLegacyHomeMigration(root));
		await homeDir.copyResourceEntries(original, pfa);
		for (var dir of ["models", "embeddings", "nrrd"]) {
			assert.strictEqual(fs.readFileSync(path.join(pfa, dir, "marker"), "utf8"), dir);
			assert.strictEqual(fs.readFileSync(path.join(original, dir, "marker"), "utf8"), dir);
		}
		assert.ok(!fs.existsSync(path.join(pfa, "python")));
		assert.ok(!fs.existsSync(path.join(pfa, "benv")));
		assert.ok(fs.existsSync(path.join(pfa, "manifest.json")));
		assert.strictEqual(fs.readFileSync(guard, "utf8"), "raise SystemExit('Mason Jar only')");
		fs.writeFileSync(path.join(pfa, "models/marker"), "PFA-owned");
		await homeDir.copyResourceEntries(original, pfa);
		assert.strictEqual(fs.readFileSync(path.join(pfa, "models/marker"), "utf8"), "PFA-owned");
		assert.ok(!fs.readdirSync(pfa).some(function (n) { return n.startsWith(".resource-copy-"); }));
		// Project formats are intentionally unchanged.
		assert.strictEqual(require("../js/branding").META_DIR, ".masonjar");
		assert.strictEqual(require("../js/branding").BUNDLE_SUFFIX, ".masonjar");
	} finally { helpers.rmDir(root); }
}

async function main() {
	testMasonHomePath();
	testEnvIsReady();
	testNeedsLegacyHomeMigration();
	testResolveHomeDirAlwaysMason();
	await testCopyDataNotRuntime();
	console.log("test-home-dir.js: OK");
}

main().catch(function (err) { console.error(err); process.exitCode = 1; });
