"use strict";

var ipc = null;
try {
	ipc = require("electron").ipcRenderer;
} catch (_err) {
	ipc = null;
}

function reportRendererError(msg) {
	if (ipc) {
		ipc.send("reportRendererError", [String(msg || "Unknown error")]);
	}
}

function onReady(fn) {
	if (typeof fn !== "function") {
		return;
	}
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", fn);
	} else {
		fn();
	}
}

function installGlobalErrorHandler() {
	if (window.__masonjarErrorHandlerInstalled) {
		return;
	}
	window.__masonjarErrorHandlerInstalled = true;
	window.onerror = function (_message, _source, _lineno, _colno, err) {
		var msg = err && err.message ? err.message : String(_message || "Unknown error");
		reportRendererError(msg);
	};
	window.addEventListener("unhandledrejection", function (event) {
		var reason = event.reason;
		var msg =
			reason && reason.message
				? reason.message
				: String(reason || "Unhandled promise rejection");
		reportRendererError(msg);
	});
}

module.exports = {
	onReady: onReady,
	installGlobalErrorHandler: installGlobalErrorHandler,
};
