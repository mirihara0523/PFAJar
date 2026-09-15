"use strict";

var fs = require("fs");
var pageInit = require("./page_init");
var navTrail = require("./nav_trail");
var project = require("./project");
var workspace = require("./workspace");
var dialogs = require("./dialogs");
var legacyMode = require("./legacy_mode");

var WORKSPACE_MENU = "./workspace_menu.html";

var chooserPanel = null;
var freshPanel = null;
var importPanel = null;
var projectNameInput = null;
var parentDirInput = null;
var legacyStatus = null;

function showPanel(panel) {
	var panels = [chooserPanel, freshPanel, importPanel];
	for (var i = 0; i < panels.length; i++) {
		if (panels[i]) {
			panels[i].classList.add("d-none");
			panels[i].classList.remove("project-start-step", "active");
		}
	}
	if (panel) {
		panel.classList.remove("d-none");
		panel.classList.add("project-start-step", "active");
		panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
	}
	updateTrailForPanel(panel);
}

function updateTrailForPanel(panel) {
	var steps = [{ label: "Start", href: "./menu.html" }, { label: "New project" }];
	if (panel === freshPanel) {
		steps.push({ label: "Create blank project (no initial import)" });
	} else if (panel === importPanel) {
		steps.push({ label: "Import existing" });
	}
	navTrail.renderTrail(steps, "navTrail");
}

function handleAction(action, target) {
	if (action === "show-fresh") {
		showPanel(freshPanel);
		return;
	}
	if (action === "show-import") {
		showPanel(importPanel);
		return;
	}
	if (action === "show-chooser") {
		showPanel(chooserPanel);
		return;
	}
	if (action === "choose-parent") {
		dialogs
			.pickDirectory({ tag: "newProjectBundle" })
			.then(function (selected) {
				if (parentDirInput && selected) {
					parentDirInput.value = selected;
				}
			});
		return;
	}
	if (action === "create-fresh") {
		var name = projectNameInput ? projectNameInput.value.trim() : "";
		var parentDir = parentDirInput ? parentDirInput.value.trim() : "";
		if (!name) {
			alert("Enter a project name.");
			return;
		}
		if (!parentDir) {
			alert("Choose a location to store your Mason Jar projects.");
			return;
		}
		var resolved = project.resolveNewBundlePath(parentDir, name);
		if (fs.existsSync(resolved.bundleRoot)) {
			alert("A bundle already exists at:\n" + resolved.bundleRoot);
			return;
		}
		if (target) {
			target.disabled = true;
		}
		try {
			fs.mkdirSync(resolved.bundleRoot, { recursive: true });
			project.createProject({
				bundleRoot: resolved.bundleRoot,
				name: resolved.name,
				projectFilename: resolved.projectFilename,
			});
			project.openProject(resolved.bundleRoot);
			window.location.href = WORKSPACE_MENU;
		} catch (err) {
			alert(String(err.message || err));
			if (target) {
				target.disabled = false;
			}
		}
		return;
	}
	if (action === "legacy-only") {
		if (target) {
			target.disabled = true;
		}
		legacyMode.requestLegacyModeEntry().then(function (result) {
			if (target) {
				target.disabled = false;
			}
			if (!result.ok) {
				if (result.message) {
					if (legacyStatus) {
						legacyStatus.textContent = result.message;
					}
					if (result.workspace && !result.workspace.countingRoot) {
						alert(result.message);
					}
				}
				return;
			}
			var msg = result.message || workspace.getScanStatusMessage();
			if (legacyStatus) {
				legacyStatus.textContent = msg;
			}
			window.location.href = WORKSPACE_MENU;
		});
	}
}

function bindUi() {
	var root = document.getElementById("parent");
	if (!root) {
		return;
	}
	root.addEventListener("click", function (event) {
		var el = event.target.closest("[data-mj-action]");
		if (!el || !root.contains(el)) {
			return;
		}
		var action = el.getAttribute("data-mj-action");
		if (!action) {
			return;
		}
		event.preventDefault();
		handleAction(action, el);
	});
}

function cacheDom() {
	chooserPanel = document.getElementById("chooserPanel");
	freshPanel = document.getElementById("freshPanel");
	importPanel = document.getElementById("importPanel");
	projectNameInput = document.getElementById("projectName");
	parentDirInput = document.getElementById("parentDir");
	legacyStatus = document.getElementById("legacyStatus");
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	cacheDom();
	bindUi();
	showPanel(chooserPanel);
});
