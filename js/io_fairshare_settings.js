"use strict";

var ipc = require("electron").ipcRenderer;
var ioFairshare = require("../io_fairshare");
var dialogs = require("./dialogs");

var enabledEl = null;
var linkModeEl = null;
var linkManualEl = null;
var statusEl = null;
var nasListEl = null;
var nasEmptyEl = null;
var nasConfiguredEl = null;
var pickNasBtn = null;
var nasFeedbackEl = null;
var refreshTimer = null;
var lastStatus = null;

function qs(id) {
	return document.getElementById(id);
}

function formatMbps(n) {
	var v = Number(n);
	if (!Number.isFinite(v)) {
		return "—";
	}
	if (v >= 1000) {
		return (v / 1000).toFixed(1) + " Gbps budget share";
	}
	return Math.round(v) + " Mbps";
}

function renderNasPrefixes(status) {
	if (!nasListEl) {
		return;
	}
	var prefixes = (status && status.nas_path_prefixes) || [];
	nasListEl.innerHTML = "";
	if (nasEmptyEl) {
		nasEmptyEl.classList.toggle("d-none", prefixes.length > 0);
	}
	if (nasConfiguredEl) {
		nasConfiguredEl.classList.toggle("d-none", prefixes.length === 0);
	}
	for (var i = 0; i < prefixes.length; i++) {
		var li = document.createElement("li");
		li.textContent = prefixes[i];
		nasListEl.appendChild(li);
	}
}

function renderStatus(status) {
	lastStatus = status;
	if (!statusEl || !status) {
		return;
	}
	renderNasPrefixes(status);
	if (!status.enabled) {
		statusEl.textContent = "Network fair-share is off for this user.";
		return;
	}
	var jobs = status.active_jobs || 0;
	var limit = formatMbps(status.limit_mbps);
	var link = formatMbps(status.link_mbps).replace(" budget share", "");
	statusEl.textContent =
		"Link " +
		link +
		" · " +
		jobs +
		" active job" +
		(jobs === 1 ? "" : "s") +
		" on this machine · your share ~" +
		limit;
}

function setNasFeedback(msg, isError) {
	if (!nasFeedbackEl) {
		return;
	}
	if (!msg) {
		nasFeedbackEl.textContent = "";
		nasFeedbackEl.classList.add("d-none");
		return;
	}
	nasFeedbackEl.textContent = msg;
	nasFeedbackEl.classList.remove("d-none");
	nasFeedbackEl.classList.toggle("text-danger", !!isError);
	nasFeedbackEl.classList.toggle("text-success", !isError);
}

function refreshStatus() {
	ipc.send("getIoFairshareStatus");
}

function bindNetworkSharing(rootId) {
	var root = qs(rootId || "ioFairsharePanel");
	if (!root) {
		return;
	}
	enabledEl = qs("ioFairshareEnabled");
	linkModeEl = qs("ioFairshareLinkMode");
	linkManualEl = qs("ioFairshareLinkMbps");
	statusEl = qs("ioFairshareStatus");
	nasListEl = qs("ioFairshareNasList");
	nasEmptyEl = qs("ioFairshareNasEmpty");
	nasConfiguredEl = qs("ioFairshareNasConfigured");
	pickNasBtn = qs("ioFairsharePickNas");
	nasFeedbackEl = qs("ioFairshareNasFeedback");
	if (!enabledEl || !statusEl) {
		return;
	}

	ipc.on("ioFairshareStatus", function (_event, status) {
		renderStatus(status);
		if (enabledEl && typeof status.enabled === "boolean") {
			enabledEl.checked = status.enabled;
		}
	});

	ipc.on("ioFairshareSharedConfigSaved", function () {
		setNasFeedback("Network locations saved for this server.", false);
		refreshStatus();
	});

	ipc.on("ioFairshareSharedConfigError", function (_event, payload) {
		var msg =
			(payload && payload.message) ||
			"Could not save shared network settings.";
		setNasFeedback(
			msg +
				" Ensure %ProgramData%\\MasonJar\\io-fairshare is writable by all users (see lab network guide).",
			true,
		);
	});

	enabledEl.addEventListener("change", function () {
		ipc.send("saveIoFairshareUserConfig", { enabled: enabledEl.checked });
	});

	if (linkModeEl) {
		linkModeEl.addEventListener("change", function () {
			var manualWrap = qs("ioFairshareManualWrap");
			if (manualWrap) {
				manualWrap.classList.toggle("d-none", linkModeEl.value !== "manual");
			}
		});
	}

	var saveLinkBtn = qs("ioFairshareSaveLink");
	if (saveLinkBtn) {
		saveLinkBtn.addEventListener("click", function () {
			var patch = {};
			if (linkModeEl && linkModeEl.value === "manual" && linkManualEl) {
				var mbps = Number(linkManualEl.value);
				if (Number.isFinite(mbps) && mbps > 0) {
					patch.link_mbps = mbps;
				}
			} else {
				patch.link_mbps = "auto";
			}
			ipc.send("saveIoFairshareUserConfig", patch);
		});
	}

	if (pickNasBtn) {
		pickNasBtn.addEventListener("click", function () {
			setNasFeedback("");
			dialogs
				.pickNetworkLocations({ tag: "nasLocations" })
				.then(function (paths) {
					if (!paths || !paths.length) {
						return;
					}
					var normalized = [];
					for (var i = 0; i < paths.length; i++) {
						var root = ioFairshare.normalizeNasPathPrefix(paths[i]);
						if (root) {
							normalized.push(root);
						}
					}
					if (!normalized.length) {
						setNasFeedback(
							"Could not recognize a network drive or UNC share in the selection. Pick a mapped drive (e.g. Z:\\) or \\\\server\\share folder.",
							true,
						);
						return;
					}
					var existing =
						(lastStatus && lastStatus.nas_path_prefixes) || [];
					var merged = ioFairshare.mergeNasPathPrefixes(existing, normalized);
					ipc.send("saveIoFairshareSharedConfig", {
						nas_path_prefixes: merged,
					});
				});
		});
	}

	refreshStatus();
	if (refreshTimer) {
		clearInterval(refreshTimer);
	}
	refreshTimer = setInterval(refreshStatus, 10000);
}

module.exports = {
	bindNetworkSharing: bindNetworkSharing,
	refreshStatus: refreshStatus,
	renderStatus: renderStatus,
};
