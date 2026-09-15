"use strict";

var ipc = require("electron").ipcRenderer;
var branding = require("./branding");

function syncLogToggleButton(buttonEl, state) {
	if (!buttonEl || !state) {
		return;
	}
	var visible = !!state.visible;
	buttonEl.textContent = visible ? "Hide log" : "Show log";
	buttonEl.setAttribute("aria-pressed", visible ? "true" : "false");
	buttonEl.setAttribute(
		"aria-label",
		visible ? "Hide application log" : "Show application log",
	);
}

function bindAppLogToggle(buttonEl) {
	if (!buttonEl) {
		return;
	}
	ipc.on("logWindowState", function (_event, state) {
		syncLogToggleButton(buttonEl, state);
		if (state && state.visible) {
			branding.setLogDismissed(false);
		} else if (state && state.dismissed) {
			branding.setLogDismissed(true);
		}
	});
	buttonEl.addEventListener("click", function () {
		ipc.send("toggleLogWindow");
	});
	if (!branding.readLogDismissed()) {
		ipc.send("showLogWindow");
	} else {
		ipc.send("getLogWindowState");
	}
}

module.exports = {
	bindAppLogToggle: bindAppLogToggle,
};
