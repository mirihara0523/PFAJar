"use strict";

/**
 * Unit tests for js/dialog_preferences.js (clear-on-version-mismatch).
 * Run: node scripts/test-dialog-preferences.js
 */

var assert = require("assert");
var fs = require("fs");
var os = require("os");
var path = require("path");
var dialogPrefs = require("../js/dialog_preferences");

function tmpHome() {
	return fs.mkdtempSync(path.join(os.tmpdir(), "mj-dialog-prefs-"));
}

function rmrf(dir) {
	fs.rmSync(dir, { recursive: true, force: true });
}

function run() {
	var home = tmpHome();
	try {
		var prefs = dialogPrefs.load(home);
		assert.deepStrictEqual(prefs.suppressed, {});
		assert.strictEqual(prefs.app_version, "");

		prefs.app_version = "6.0.30";
		prefs.suppressed = {};
		prefs.suppressed[dialogPrefs.KEY_MIXED_RESOLUTION_TIER] = true;
		prefs.suppressed[dialogPrefs.KEY_CONFIRM_SAVE_OVERWRITE] = true;
		prefs.suppressed[dialogPrefs.KEY_ISOLATE_LABEL_AUDIT] = true;
		dialogPrefs.save(prefs, home);

		assert.ok(
			dialogPrefs.KEY_LABELS[dialogPrefs.KEY_ISOLATE_LABEL_AUDIT],
			"isolate label audit needs a Settings label",
		);

		var cleared = dialogPrefs.syncAppVersionClearIfChanged("6.0.31", home);
		assert.strictEqual(cleared, true);
		var after = dialogPrefs.load(home);
		assert.strictEqual(after.app_version, "6.0.31");
		assert.deepStrictEqual(after.suppressed, {});

		var again = dialogPrefs.syncAppVersionClearIfChanged("6.0.31", home);
		assert.strictEqual(again, false);

		after.suppressed[dialogPrefs.KEY_MIXED_RESOLUTION_TIER] = true;
		dialogPrefs.save(after, home);
		dialogPrefs.clearSuppressions(home);
		var listed = dialogPrefs.listSuppressed(home);
		assert.strictEqual(listed.length, 0);

		console.log("test-dialog-preferences: ok");
	} finally {
		rmrf(home);
	}
}

run();
