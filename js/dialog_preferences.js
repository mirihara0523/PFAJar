"use strict";

/**
 * App-wide dialog suppression prefs: ~/.masonjar/dialog_preferences.json
 * Schema shared with py/dialog_preferences.py
 */

var fs = require("fs");
var path = require("path");
var homeDir = require("./home_dir");

var FILENAME = "dialog_preferences.json";

var KEY_MIXED_RESOLUTION_TIER = "adjust.mixed_resolution_tier";
var KEY_CONFIRM_SAVE_OVERWRITE = "adjust.confirm_save_overwrite";
var KEY_ISOLATE_LABEL_AUDIT = "adjust.isolate_label_audit";

var KEY_LABELS = {};
KEY_LABELS[KEY_MIXED_RESOLUTION_TIER] =
	"Viewer/Editor: mixed-resolution tier change notice";
KEY_LABELS[KEY_CONFIRM_SAVE_OVERWRITE] =
	"Viewer/Editor: confirm before overwriting annotation on Save";
KEY_LABELS[KEY_ISOLATE_LABEL_AUDIT] =
	"Viewer/Editor: Isolate Regions notice after Save";

function prefsPath(homeRoot) {
	return path.join(homeDir.masonHomePath(homeRoot), FILENAME);
}

function defaultPrefs(appVersion) {
	return { app_version: String(appVersion || ""), suppressed: {} };
}

function load(homeRoot) {
	var filePath = prefsPath(homeRoot);
	if (!fs.existsSync(filePath)) {
		return defaultPrefs();
	}
	try {
		var raw = JSON.parse(fs.readFileSync(filePath, "utf8"));
		if (!raw || typeof raw !== "object") {
			return defaultPrefs();
		}
		var suppressed = raw.suppressed && typeof raw.suppressed === "object"
			? raw.suppressed
			: {};
		var out = { app_version: String(raw.app_version || ""), suppressed: {} };
		Object.keys(suppressed).forEach(function (k) {
			out.suppressed[k] = !!suppressed[k];
		});
		return out;
	} catch (_err) {
		return defaultPrefs();
	}
}

function save(prefs, homeRoot) {
	var filePath = prefsPath(homeRoot);
	fs.mkdirSync(path.dirname(filePath), { recursive: true });
	var payload = {
		app_version: String((prefs && prefs.app_version) || ""),
		suppressed: (prefs && prefs.suppressed) || {},
	};
	fs.writeFileSync(filePath, JSON.stringify(payload, null, 2) + "\n", "utf8");
}

function clearSuppressions(homeRoot, appVersion) {
	var prefs = load(homeRoot);
	prefs.suppressed = {};
	if (appVersion != null) {
		prefs.app_version = String(appVersion);
	}
	save(prefs, homeRoot);
	return prefs;
}

function syncAppVersionClearIfChanged(currentVersion, homeRoot) {
	var prefs = load(homeRoot);
	var stored = String(prefs.app_version || "");
	var cur = String(currentVersion || "");
	if (stored === cur && cur) {
		return false;
	}
	prefs.suppressed = {};
	prefs.app_version = cur;
	save(prefs, homeRoot);
	return true;
}

function listSuppressed(homeRoot) {
	var prefs = load(homeRoot);
	var suppressed = prefs.suppressed || {};
	return Object.keys(suppressed)
		.filter(function (k) {
			return !!suppressed[k];
		})
		.map(function (k) {
			return { key: k, label: KEY_LABELS[k] || k };
		});
}

module.exports = {
	FILENAME: FILENAME,
	KEY_MIXED_RESOLUTION_TIER: KEY_MIXED_RESOLUTION_TIER,
	KEY_CONFIRM_SAVE_OVERWRITE: KEY_CONFIRM_SAVE_OVERWRITE,
	KEY_ISOLATE_LABEL_AUDIT: KEY_ISOLATE_LABEL_AUDIT,
	KEY_LABELS: KEY_LABELS,
	prefsPath: prefsPath,
	load: load,
	save: save,
	clearSuppressions: clearSuppressions,
	syncAppVersionClearIfChanged: syncAppVersionClearIfChanged,
	listSuppressed: listSuppressed,
};
