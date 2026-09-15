"use strict";

var assert = require("assert");
var fs = require("fs");
var path = require("path");
var helpers = require("./test-helpers");
var branding = require("../js/branding");
var ctx = require("../js/parcellation_context");

function testIncludeLayersAllowed() {
	assert.strictEqual(ctx.includeLayersAllowed(null), true);
	assert.strictEqual(
		ctx.includeLayersAllowed({ hasParcellation: true, isFullDetail: false, tierId: "areas" }),
		false,
	);
	assert.strictEqual(
		ctx.includeLayersAllowed({ hasParcellation: true, tierId: "layers" }),
		true,
	);
}

function testSummarizeEmpty() {
	var s = ctx.summarizeParcellationForLeaf("/nonexistent", []);
	assert.strictEqual(s.hasParcellation, false);
	assert.strictEqual(s.isFullDetail, true);
}

function testReadMeta(tmp) {
	var leaf = path.join(tmp, "align");
	fs.mkdirSync(path.join(leaf, branding.META_DIR), { recursive: true });
	fs.writeFileSync(
		path.join(leaf, branding.META_DIR, ctx.META_FILENAME),
		JSON.stringify({
			M528_s061: { tier_id: "areas", st_level: null, applied_at: "2026-01-01" },
		}),
		"utf8",
	);
	var s = ctx.summarizeParcellationForLeaf(leaf, ["M528_s061"]);
	assert.strictEqual(s.hasParcellation, true);
	assert.strictEqual(s.tierId, "areas");
	assert.strictEqual(ctx.includeLayersAllowed(s), false);
	assert.ok(ctx.formatParcellationLabel({ tier_id: "areas" }).indexOf("Functional") >= 0);
}

function run() {
	var tmp = helpers.tmpDir("mj-parcel-ctx-");
	testIncludeLayersAllowed();
	testSummarizeEmpty();
	testReadMeta(tmp);
	helpers.rmDir(tmp);
	console.log("test-parcellation-context: PASS");
}

run();
