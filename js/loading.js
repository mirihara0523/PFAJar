"use strict";

var ipc = require("electron").ipcRenderer;
var lastUpdateTimestamp = 0;
var setupFailed = false;
var packageLines = [];
var packageStartedAt = 0;
var packageId = null;

function duration(ms) {
	var seconds = Math.max(0, Math.floor(Number(ms || 0) / 1000));
	return String(Math.floor(seconds / 60)).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");
}

ipc.on("packageInstallProgress", function (_event, progress) {
	if (setupFailed || !progress || progress.startedAt < packageStartedAt) return;
	if (progress.id !== packageId) {
		packageLines = [];
		packageId = progress.id;
		packageStartedAt = progress.startedAt;
	}
	var panel = document.getElementById("packageProgress");
	if (panel) panel.hidden = false;
	var activity = document.getElementById("packageActivity");
	if (activity) activity.textContent = "Current activity: " + (progress.activity || "Starting pip…");
	var timing = document.getElementById("packageTiming");
	if (timing) timing.textContent = "Elapsed: " + duration(progress.elapsedMs) + " · Last output: " + duration(progress.quietMs) + " ago";
	var state = document.getElementById("packageProcessState");
	if (state) state.textContent = progress.state === "complete" ? "Package installation completed."
		: progress.state === "failed" ? "Package installation failed — stopped."
		: progress.quietMs >= 15000 ? "No new output for " + Math.floor(progress.quietMs / 1000) + " seconds; the process is still running."
		: "Process running.";
	if (Array.isArray(progress.lines) && progress.lines.length) {
		packageLines = packageLines.concat(progress.lines).slice(-150);
		var output = document.getElementById("packageOutput");
		if (output) {
			var follow = output.scrollHeight - output.scrollTop - output.clientHeight < 40;
			output.textContent = packageLines.join("\n");
			if (follow) output.scrollTop = output.scrollHeight;
		}
	}
	if (progress.state === "failed") {
		var status = document.getElementById("status");
		if (status) status.textContent = "Package installation failed — updating has stopped.";
		var spinner = document.getElementById("loadingSpinner");
		if (spinner) spinner.hidden = true;
	}
});

ipc.on("updateStatus", function (_event, response) {
	if (setupFailed || response == null) return;
	if (!response.timestamp || response.timestamp > lastUpdateTimestamp) {
		if (response.timestamp) {
			lastUpdateTimestamp = response.timestamp;
		}
		var status = document.getElementById("status");
		if (status) {
			status.textContent = response.message || response;
		}
	}
});

ipc.on("bootstrapFailed", function (_event, failure) {
	setupFailed = true;
	var status = document.getElementById("status");
	if (status) status.textContent = "Setup failed — updating has stopped.";
	var spinner = document.getElementById("loadingSpinner");
	if (spinner) spinner.hidden = true;
	var panel = document.getElementById("setupFailure");
	if (panel) panel.hidden = false;
	var details = document.getElementById("setupFailureDetails");
	if (details) details.textContent = "Environment: " + (failure.environment || "") + "\n\n" + (failure.detail || "Unknown setup error");
});

var showSetupLog = document.getElementById("showSetupLog");
if (showSetupLog) showSetupLog.addEventListener("click", function () {
	ipc.send("showLogWindow");
});

ipc.send("getVersion");
ipc.on("version", function (_event, response) {
	var version = document.getElementById("version");
	if (version) {
		version.textContent = response;
	}
});

var guide = document.getElementById("guide");
if (guide) {
	guide.addEventListener("click", function (event) {
		event.preventDefault();
		ipc.send("openGuide");
	});
}
