"use strict";

var ipc = require("electron").ipcRenderer;
var perfLog = require("./perf_log");
var pageInit = require("./page_init");
var navTrail = require("./nav_trail");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var projectFiles = require("./project_files");
var projectIndexBusy = require("./project_index_busy");
var appLogToggle = require("./app_log_toggle");
var ioFairshareSettings = require("./io_fairshare_settings");
var legacyMode = require("./legacy_mode");

var projectChip = document.getElementById("projectChip");

function refreshProjectChip() {
	if (!projectChip) {
		return;
	}
	var ctx = pipelineGate.getContextLabel();
	if (!ctx) {
		projectChip.classList.add("d-none");
		projectChip.innerHTML = "";
		return;
	}
	var closeHtml = "";
	if (ctx.type === "project") {
		closeHtml =
			' <button type="button" class="btn btn-link btn-sm p-0" id="closeProjectChip">Close</button>';
	}
	projectChip.classList.remove("d-none");
	projectChip.innerHTML =
		'<span class="menu-project-chip-label">Current: <strong>' +
		ctx.label +
		"</strong> (" +
		ctx.detail +
		")</span>" +
		closeHtml;
	var closeBtn = document.getElementById("closeProjectChip");
	if (closeBtn) {
		closeBtn.addEventListener("click", function () {
			project.clearActiveProject();
			window.location.replace("./menu.html");
		});
	}
}

function refreshLegacyBanner() {
	var banner = document.getElementById("legacyModeBanner");
	if (!banner) {
		return;
	}
	var ctx = pipelineGate.getContextLabel();
	var isLegacy = !!(ctx && ctx.type === "legacy");
	banner.classList.toggle("d-none", !isLegacy);
}

function refreshLegacyPipelineCardSubs() {
	var ctx = pipelineGate.getContextLabel();
	if (!ctx || ctx.type !== "legacy") {
		return;
	}
	var cards = document.querySelectorAll(".menu-card-pipeline[data-cat]");
	for (var i = 0; i < cards.length; i++) {
		var card = cards[i];
		var cat = card.getAttribute("data-cat");
		var sub = legacyMode.getLegacyPipelineCardSubtitle(cat);
		if (!sub) {
			continue;
		}
		var subEl = card.querySelector(".menu-card-sub");
		if (subEl) {
			subEl.textContent = sub;
		}
	}
}

function bindLegacyLimitationsButton() {
	var btn = document.getElementById("legacyModeViewLimitations");
	if (!btn) {
		return;
	}
	btn.addEventListener("click", function () {
		legacyMode.showLegacyModeConsentModal({ readOnly: true });
	});
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	projectIndexBusy.populatePage(function () {
		perfLog.perfSection("hub.tryRestoreActiveProject", function () {
			project.tryRestoreActiveProject();
		});
		pipelineGate.assertPipelineAccess();
		appLogToggle.bindAppLogToggle(document.getElementById("toggleAppLog"));
		navTrail.renderTrail(
			[
				{ label: "Start", href: "./menu.html" },
				{ label: "Workspace" },
			],
			"navTrail",
		);
		refreshProjectChip();
		refreshLegacyBanner();
		refreshLegacyPipelineCardSubs();
		bindLegacyLimitationsButton();
		project.addProcessingStateListener(function () {
			projectFiles.renderStepFailures();
		});
		perfLog.perfSection("hub.bindProjectFileControls", function () {
			projectFiles.bindProjectFileControls();
		});
		var compact = document.getElementById("ioFairshareStatusCompact");
		if (compact) {
			var ioFairshare = require("../io_fairshare");
			ipc.on("ioFairshareStatus", function (_event, status) {
				if (!status || !status.enabled) {
					compact.textContent = "";
					return;
				}
				compact.textContent = ioFairshare.formatFairshareCompactLine(status);
			});
			ioFairshareSettings.refreshStatus();
		}
	}, { waitForIndex: false });
});
