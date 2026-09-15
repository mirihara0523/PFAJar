"use strict";

var ipc = require("electron").ipcRenderer;
var appPaths = require("./app_paths");
var pageInit = require("./page_init");
var project = require("./project");
var workspace = require("./workspace");
var pipelineGate = require("./pipeline_gate");
var dialogs = require("./dialogs");
var appLogToggle = require("./app_log_toggle");

var WORKSPACE_MENU = "./workspace_menu.html";

var openProjectBtn = null;
var projectChip = null;
var versionEl = null;
var recentProjectsList = null;
var openPipelineBtn = null;
var checkUpdatesBtn = null;
var rescanHubBtn = null;
var toggleAppLogBtn = null;

var coldStartHadContext = false;

function readAppVersion() {
	return appPaths.readPackageVersion();
}

function setVersionText(version) {
	if (!versionEl) {
		return;
	}
	var v = String(version || "").trim();
	if (!v) {
		return;
	}
	versionEl.textContent = "Version " + v;
}

function goToWorkspaceMenu() {
	window.location.href = WORKSPACE_MENU;
}

function setButtonLoading(btn, loading, label) {
	if (!btn) {
		return;
	}
	btn.disabled = !!loading;
	if (loading) {
		btn.dataset.prevLabel = btn.textContent;
		btn.textContent = label || "Opening…";
	} else if (btn.dataset.prevLabel) {
		btn.textContent = btn.dataset.prevLabel;
		delete btn.dataset.prevLabel;
	}
}

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
			coldStartHadContext = false;
			refreshHubState();
		});
	}
}

function refreshContinuePipeline() {
	if (!openPipelineBtn) {
		return;
	}
	var show =
		coldStartHadContext && pipelineGate.hasPipelineAccess();
	openPipelineBtn.classList.toggle("d-none", !show);
}

function refreshRecentList() {
	if (!recentProjectsList) {
		return;
	}
	recentProjectsList.innerHTML = "";
	var recent = project.getRecentProjects();
	if (!recent.length) {
		var empty = document.createElement("li");
		empty.className = "list-group-item text-muted border-0 px-0";
		empty.textContent = "No recent projects.";
		recentProjectsList.appendChild(empty);
		return;
	}
	for (var i = 0; i < recent.length; i++) {
		(function (entry) {
			var li = document.createElement("li");
			li.className =
				"list-group-item d-flex justify-content-between align-items-center border-0 px-0 py-1";
			var label = document.createElement("span");
			label.className = "text-truncate me-2";
			label.textContent = entry.name + " — " + entry.path;
			var btn = document.createElement("button");
			btn.type = "button";
			btn.className = "btn btn-sm btn-outline-primary flex-shrink-0";
			btn.textContent = "Open";
			btn.addEventListener("click", function () {
				setButtonLoading(btn, true, "Opening…");
				try {
					project.openProject(entry.path);
					goToWorkspaceMenu();
				} catch (err) {
					alert(String(err.message || err));
					setButtonLoading(btn, false);
				}
			});
			li.appendChild(label);
			li.appendChild(btn);
			recentProjectsList.appendChild(li);
		})(recent[i]);
	}
}

function refreshHubState() {
	refreshProjectChip();
	refreshRecentList();
	refreshContinuePipeline();
	if (checkUpdatesBtn) {
		checkUpdatesBtn.classList.remove("d-none");
	}
	if (rescanHubBtn) {
		rescanHubBtn.classList.toggle("d-none", !project.isActive());
	}
}

function cacheDom() {
	openProjectBtn = document.getElementById("openProject");
	projectChip = document.getElementById("projectChip");
	versionEl = document.getElementById("version");
	recentProjectsList = document.getElementById("recentProjects");
	openPipelineBtn = document.getElementById("openPipeline");
	checkUpdatesBtn = document.getElementById("checkUpdates");
	rescanHubBtn = document.getElementById("rescanProjectHub");
	toggleAppLogBtn = document.getElementById("toggleAppLog");
}

function bindOpenProject() {
	if (!openProjectBtn) {
		return;
	}
	openProjectBtn.addEventListener("click", function () {
		setButtonLoading(openProjectBtn, true);
		var defaultPath = project.isActive() ? project.getBundleRoot() : "";
		dialogs
			.pickDirectory({
				tag: "projectBundle",
				defaultPath: defaultPath || "",
			})
			.then(function (selected) {
				setButtonLoading(openProjectBtn, false);
				if (!selected) {
					return;
				}
				try {
					project.openProject(selected);
					goToWorkspaceMenu();
				} catch (err) {
					alert(String(err.message || err));
				}
			});
	});
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	cacheDom();
	setVersionText(readAppVersion());
	ipc.on("version", function (_event, response) {
		setVersionText(response);
	});
	ipc.send("getVersion");

	workspace.loadWorkspace();
	var restoredProject = project.tryRestoreActiveProject();
	if (restoredProject || pipelineGate.hasValidLegacyWorkspace()) {
		coldStartHadContext = true;
	}

	bindOpenProject();
	appLogToggle.bindAppLogToggle(toggleAppLogBtn);
	if (checkUpdatesBtn) {
		checkUpdatesBtn.addEventListener("click", function () {
			ipc.send("checkForUpdates", []);
		});
	}
	if (rescanHubBtn) {
		rescanHubBtn.addEventListener("click", function () {
			if (!project.isActive()) {
				return;
			}
			rescanHubBtn.disabled = true;
			var projectIndexBusy = require("./project_index_busy");
			projectIndexBusy.show();
			project
				.refreshProjectIndex()
				.then(function () {
					alert("Project file index refreshed.");
				})
				.catch(function (err) {
					alert(String(err.message || err));
				})
				.finally(function () {
					projectIndexBusy.hide();
					rescanHubBtn.disabled = false;
				});
		});
	}
	refreshHubState();
});
