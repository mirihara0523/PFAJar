/**
 * Env-gated performance logging for renderer (page) code.
 *
 * Mirrors py/perf_log.py. Emits "LOG: perf <label> <ms>" lines which are sent
 * over IPC to the main process ("perfLog" channel) so they flow through the
 * existing console.log pipeline into masonjar.log and the in-app Log window,
 * exactly like Python LOG lines.
 *
 * Enable by launching with the environment variable MASONJAR_PERF=1 (also
 * accepts true/yes/on). No-op when disabled, so it is safe to leave in place.
 *
 * Usage (in a page script):
 *     var perfLog = require("./perf_log");
 *     perfLog.perfLog("page.foo.step", ms);
 *     var t0 = perfLog.now(); ...; perfLog.perfLog("label", perfLog.now() - t0);
 */

var PERF_ENABLED = false;
try {
	var _v = (process && process.env && process.env.MASONJAR_PERF) || "";
	PERF_ENABLED =
		["1", "true", "yes", "on"].indexOf(String(_v).trim().toLowerCase()) >= 0;
} catch (_e) {
	PERF_ENABLED = false;
}

var _ipc = null;
if (PERF_ENABLED) {
	try {
		_ipc = require("electron").ipcRenderer;
	} catch (_e2) {
		_ipc = null;
	}
}

function perfEnabled() {
	return PERF_ENABLED;
}

function _emit(line) {
	try {
		if (_ipc) {
			_ipc.send("perfLog", line);
		} else if (typeof console !== "undefined") {
			// Fallback (e.g. no ipcRenderer): at least reach devtools console.
			console.log(line);
		}
	} catch (_e) {
		// ignore
	}
}

function now() {
	try {
		return performance.now();
	} catch (_e) {
		return Date.now();
	}
}

function perfLog(label, ms) {
	if (!PERF_ENABLED) {
		return;
	}
	_emit("LOG: perf " + label + " " + Number(ms).toFixed(1) + "ms");
}

function perfSection(label, fn) {
	if (!PERF_ENABLED) {
		return fn();
	}
	var t0 = now();
	try {
		return fn();
	} finally {
		perfLog(label, now() - t0);
	}
}

function pageName() {
	try {
		var p = (window.location && window.location.pathname) || "";
		var base = p.split("/").pop() || "page";
		return base.replace(/\.html$/i, "") || "page";
	} catch (_e) {
		return "page";
	}
}

var _pageLoadEmitted = false;
function emitPageLoadOnce() {
	if (!PERF_ENABLED || _pageLoadEmitted) {
		return;
	}
	_pageLoadEmitted = true;
	var emit = function () {
		try {
			var entries =
				(performance.getEntriesByType &&
					performance.getEntriesByType("navigation")) ||
				[];
			var ms = entries[0] ? entries[0].duration : now();
			perfLog("page." + pageName() + ".load", ms);
		} catch (_e) {
			// ignore
		}
	};
	try {
		if (typeof document !== "undefined" && document.readyState === "complete") {
			emit();
		} else if (typeof window !== "undefined") {
			window.addEventListener("load", emit, { once: true });
		}
	} catch (_e) {
		emit();
	}
}

// Auto-run page-load timing whenever this module is loaded in a renderer page.
try {
	emitPageLoadOnce();
} catch (_e) {
	// ignore
}

module.exports = {
	perfEnabled: perfEnabled,
	perfLog: perfLog,
	perfSection: perfSection,
	now: now,
	pageName: pageName,
	emitPageLoadOnce: emitPageLoadOnce,
};
