"use strict";

var path = require("path");
var fs = require("fs");

/** App root (contains pages/ and js/), from current HTML path. */
function getAppRoot() {
	var p = decodeURIComponent(window.location.pathname || "");
	p = p.replace(/\\/g, "/");
	if (/^\/[A-Za-z]:\//.test(p)) {
		p = p.slice(1);
	}
	return path.dirname(path.dirname(p));
}

function getJsDir() {
	return path.join(getAppRoot(), "js");
}

function requireFromJs(moduleFile) {
	return require(path.join(getJsDir(), moduleFile));
}

function readPackageVersion() {
	try {
		var raw = fs.readFileSync(
			path.join(getAppRoot(), "package.json"),
			"utf8",
		);
		return JSON.parse(raw).version || "";
	} catch (_err) {
		return "";
	}
}

module.exports = {
	getAppRoot: getAppRoot,
	getJsDir: getJsDir,
	requireFromJs: requireFromJs,
	readPackageVersion: readPackageVersion,
};
