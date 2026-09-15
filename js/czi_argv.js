"use strict";

/** Mirror of appendCziPathArgs / appendCziInputArg in src/main.ts (for tests). */
function appendCziPathArgs(args, bundleRoot, configPath) {
	args.push("-b", String(bundleRoot || "").trim());
	if (configPath != null && String(configPath).trim().length > 0) {
		args.push("-j", String(configPath).trim());
	}
}

function appendCziInputArg(args, inputDir) {
	args.push("-i", String(inputDir || "").trim());
}

/** Mirror of appendFlagPathArg in src/main.ts (Isolate Regions and other pipeline tools). */
function appendFlagPathArg(args, flag, value) {
	args.push(flag, String(value || "").trim());
}

module.exports = {
	appendCziPathArgs: appendCziPathArgs,
	appendCziInputArg: appendCziInputArg,
	appendFlagPathArg: appendFlagPathArg,
};
