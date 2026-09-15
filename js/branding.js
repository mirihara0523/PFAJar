"use strict";

/** Shared Mason Jar branding and dual Bell Jar compatibility constants. */
module.exports = {
	PRODUCT_NAME: "Mason Jar",
	LEGACY_PRODUCT_NAME: "Bell Jar",
	HOME_DIR: ".masonjar",
	LEGACY_HOME_DIR: ".belljar",
	LOG_FILE: "masonjar.log",
	LEGACY_LOG_FILE: "belljar.log",
	BUNDLE_SUFFIX: ".masonjar",
	LEGACY_BUNDLE_SUFFIX: ".belljar",
	PROJECT_FILENAME: "project.masonjar",
	LEGACY_PROJECT_FILENAME: "project.belljar",
	PROJECT_FILENAMES: ["project.masonjar", "project.belljar"],
	META_DIR: ".masonjar",
	LEGACY_META_DIR: ".belljar",
	META_DIRS: [".masonjar", ".belljar"],
	LAYOUT_ID: "masonjar_v1",
	LEGACY_LAYOUT_ID: "belljar_v1",
	LAYOUT_IDS: ["masonjar_v1", "belljar_v1"],
	RECENT_KEY: "masonjar.recentProjects",
	LEGACY_RECENT_KEY: "belljar.recentProjects",
	ACTIVE_KEY: "masonjar.activeProject",
	LEGACY_ACTIVE_KEY: "belljar.activeProject",
	WORKSPACE_KEY: "masonjar.workspace",
	LEGACY_WORKSPACE_KEY: "belljar.workspace",
	SHOW_LOG_WINDOW_KEY: "masonjar.showLogWindow",
	LEGACY_SHOW_LOG_WINDOW_KEY: "belljar.showLogWindow",
	LOG_DISMISSED_KEY: "masonjar.logDismissed",
	INTENSITY_WHOLE_KEY: "masonjar.intensity.whole",
	readLogDismissed: function () {
		try {
			var dismissed = localStorage.getItem(module.exports.LOG_DISMISSED_KEY);
			if (dismissed !== null) {
				return dismissed === "1";
			}
			var show = localStorage.getItem(module.exports.SHOW_LOG_WINDOW_KEY);
			if (show === null) {
				show = localStorage.getItem(module.exports.LEGACY_SHOW_LOG_WINDOW_KEY);
			}
			if (show === "1" || show === "true") {
				return false;
			}
			return true;
		} catch (_err) {
			return true;
		}
	},
	setLogDismissed: function (dismissed) {
		try {
			localStorage.setItem(
				module.exports.LOG_DISMISSED_KEY,
				dismissed ? "1" : "0",
			);
			if (!dismissed) {
				localStorage.removeItem(module.exports.SHOW_LOG_WINDOW_KEY);
				localStorage.removeItem(module.exports.LEGACY_SHOW_LOG_WINDOW_KEY);
			}
		} catch (_err) {
			// ignore
		}
	},
	/** Per-process log UI session; new id each app launch. */
	LOG_SESSION_KEY: "masonjar.logSession",
	LEGACY_LOG_UI_KEY: "log",
	LEGACY_LOG_TIME_KEY: "logTime",
	GITHUB_REPO: "matsojr22/masonjar",
};
