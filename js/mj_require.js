"use strict";

/**
 * Resolve sibling modules from js/ regardless of renderer HTML path (pages/*.html).
 */
var path = require("path");

var jsDir = __dirname;

function mjRequire(name) {
	if (name.charAt(0) === ".") {
		return require(path.join(jsDir, name));
	}
	return require(name);
}

module.exports = mjRequire;
