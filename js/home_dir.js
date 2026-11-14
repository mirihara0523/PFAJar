"use strict";

var fs = require("fs");
var path = require("path");
var os = require("os");
var branding = require("./branding");

/** Data only: never copy another application's interpreter or virtualenv. */
var LEGACY_HOME_COPY_ENTRIES = [
	"models",
	"embeddings",
	"nrrd",
	"manifest.json",
];

function masonHomePath(homeRoot) {
	return path.join(homeRoot || os.homedir(), branding.HOME_DIR);
}

function legacyHomePath(homeRoot) {
	return path.join(homeRoot || os.homedir(), branding.LEGACY_HOME_DIR);
}

function envIsReady(homePath) {
	return (
		fs.existsSync(path.join(homePath, "python")) ||
		fs.existsSync(path.join(homePath, "benv"))
	);
}

function needsLegacyHomeMigration(homeRoot) {
	var mason = masonHomePath(homeRoot);
	return !envIsReady(mason) && !!findResourceSource(homeRoot);
}

function findResourceSource(homeRoot) {
	var root = homeRoot || os.homedir();
	return [".masonjar", ".belljar"].map(function (name) {
		return path.join(root, name);
	}).find(function (source) {
		return ["models", "embeddings", "nrrd"].some(function (name) {
			var p = path.join(source, name);
			return fs.existsSync(p) && fs.statSync(p).isDirectory();
		});
	}) || "";
}

async function copyResourceEntries(source, destination, onProgress) {
	if (path.resolve(source) === path.resolve(destination)) {
		throw new Error("Resource source and destination must differ");
	}
	await fs.promises.mkdir(destination, { recursive: true });
	for (var entry of LEGACY_HOME_COPY_ENTRIES) {
		var src = path.join(source, entry);
		var dest = path.join(destination, entry);
		if (!fs.existsSync(src) || fs.existsSync(dest)) continue;
		// A manifest is valid only after the source's complete data set was copied.
		if (entry === "manifest.json" && !["models", "embeddings", "nrrd"].every(function (n) {
			return fs.existsSync(path.join(source, n)) && fs.existsSync(path.join(destination, n));
		})) continue;
		if (onProgress) onProgress(entry);
		var temp = await fs.promises.mkdtemp(path.join(destination, ".resource-copy-"));
		try {
			var staged = path.join(temp, entry);
			await fs.promises.cp(src, staged, { recursive: true, dereference: true });
			await fs.promises.rename(staged, dest);
		} finally {
			await fs.promises.rm(temp, { recursive: true, force: true });
		}
	}
}

module.exports = {
	LEGACY_HOME_COPY_ENTRIES: LEGACY_HOME_COPY_ENTRIES,
	masonHomePath: masonHomePath,
	legacyHomePath: legacyHomePath,
	envIsReady: envIsReady,
	needsLegacyHomeMigration: needsLegacyHomeMigration,
	findResourceSource: findResourceSource,
	copyResourceEntries: copyResourceEntries,
};
