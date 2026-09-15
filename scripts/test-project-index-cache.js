"use strict";

var assert = require("assert");
var project = require("../js/project");

function index(files) {
	return { files: files };
}

function file(relPath, size, mtime, sliceId) {
	return {
		role: "dapi",
		relPath: relPath,
		size: size,
		mtime: mtime,
		sliceId: sliceId,
	};
}

function main() {
	var original = index([
		file("data/counting/00_dapi/a.png", 100, "2026-09-14T10:00:00.000Z", "a"),
		file("data/counting/00_dapi/b.png", 200, "2026-09-14T10:01:00.000Z", "b"),
	]);
	var sameDifferentOrder = index([original.files[1], original.files[0]]);
	assert.strictEqual(project.indexedFilesAreUnchanged(original, sameDifferentOrder), true);
	assert.strictEqual(
		project.indexedFilesAreUnchanged(
			original,
			index([file("data/counting/00_dapi/a.png", 101, "2026-09-14T10:00:00.000Z", "a"), original.files[1]]),
		),
		false,
	);
	assert.strictEqual(
		project.indexedFilesAreUnchanged(
			original,
			index([file("data/counting/00_dapi/a.png", 100, "2026-09-14T10:02:00.000Z", "a"), original.files[1]]),
		),
		false,
	);
	assert.strictEqual(project.indexedFilesAreUnchanged(original, index([original.files[0]])), false);
	console.log("test-project-index-cache.js: OK");
}

main();
