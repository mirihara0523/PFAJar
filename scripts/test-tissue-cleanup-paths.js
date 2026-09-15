"use strict";

var assert = require("assert");
var path = require("path");
var fs = require("fs");
var helpers = require("./test-helpers");

var tissuePaths = require("../js/bundle_slice_paths");

function testSharpenTophatEnumeration() {
	var bundle = helpers.tmpDir("mj-tissue-paths-");
	var sliceId = "M528_s001";
	var sharpen = path.join(
		bundle,
		"data/counting/03_max/somata/sharpen/M528_r3_a2",
		sliceId + ".tif",
	);
	var tophat = path.join(
		bundle,
		"data/counting/03_max/somata/tophat/top10_from_max",
		sliceId + ".tif",
	);
	var maxRun = path.join(
		bundle,
		"data/counting/03_max/somata/max/M528_run",
		sliceId + ".tif",
	);
	fs.mkdirSync(path.dirname(sharpen), { recursive: true });
	fs.mkdirSync(path.dirname(tophat), { recursive: true });
	fs.mkdirSync(path.dirname(maxRun), { recursive: true });
	fs.writeFileSync(sharpen, Buffer.alloc(8));
	fs.writeFileSync(tophat, Buffer.alloc(8));
	fs.writeFileSync(maxRun, Buffer.alloc(8));

	var cfg = { channels: [{ role: "signal_somata", keep: true, branch: "somata" }] };
	var paths = tissuePaths.pathsForSlice(bundle, sliceId, cfg);
	var rels = paths.map(function (p) {
		return path.relative(bundle, p).split(path.sep).join("/");
	});
	assert.ok(rels.indexOf("data/counting/03_max/somata/sharpen/M528_r3_a2/M528_s001.tif") >= 0);
	assert.ok(rels.indexOf("data/counting/03_max/somata/tophat/top10_from_max/M528_s001.tif") >= 0);
	assert.ok(rels.indexOf("data/counting/03_max/somata/max/M528_run/M528_s001.tif") >= 0);

	helpers.rmDir(bundle);
}

testSharpenTophatEnumeration();
console.log("test-tissue-cleanup-paths: ok");
