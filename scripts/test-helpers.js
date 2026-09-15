"use strict";

var fs = require("fs");
var path = require("path");
var os = require("os");

function ensureLocalStorage() {
	if (typeof global.localStorage !== "undefined") {
		return;
	}
	var store = {};
	global.localStorage = {
		getItem: function (k) {
			return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
		},
		setItem: function (k, v) {
			store[k] = String(v);
		},
		removeItem: function (k) {
			delete store[k];
		},
		clear: function () {
			store = {};
		},
	};
}

function tmpDir(prefix) {
	return fs.mkdtempSync(path.join(os.tmpdir(), prefix || "mj-test-"));
}

function rmDir(dir) {
	fs.rmSync(dir, { recursive: true, force: true });
}

function writeFileIndex(bundleRoot, files, metaDirName) {
	metaDirName = metaDirName || ".masonjar";
	var meta = path.join(bundleRoot, metaDirName);
	fs.mkdirSync(meta, { recursive: true });
	fs.writeFileSync(
		path.join(meta, "file_index.json"),
		JSON.stringify(
			{
				version: 1,
				generated_at: new Date().toISOString(),
				bundle_root: bundleRoot,
				files: files,
			},
			null,
			2,
		),
		"utf8",
	);
}

/** Minimal empty file (no real TIFF header; metadata supplied in index). */
function touchImage(dir, basename) {
	fs.mkdirSync(dir, { recursive: true });
	fs.writeFileSync(path.join(dir, basename), "");
}

module.exports = {
	ensureLocalStorage: ensureLocalStorage,
	tmpDir: tmpDir,
	rmDir: rmDir,
	writeFileIndex: writeFileIndex,
	touchImage: touchImage,
};
