"use strict";

var fs = require("fs");
var path = require("path");
var os = require("os");
var branding = require("./branding");

/** Entries copied read-only from ~/.belljar into ~/.masonjar on first launch. */
var LEGACY_HOME_COPY_ENTRIES = [
	"python",
	"benv",
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
	var legacy = legacyHomePath(homeRoot);
	return !envIsReady(mason) && envIsReady(legacy);
}

module.exports = {
	LEGACY_HOME_COPY_ENTRIES: LEGACY_HOME_COPY_ENTRIES,
	masonHomePath: masonHomePath,
	legacyHomePath: legacyHomePath,
	envIsReady: envIsReady,
	needsLegacyHomeMigration: needsLegacyHomeMigration,
};
