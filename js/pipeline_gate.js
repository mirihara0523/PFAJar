"use strict";

var fs = require("fs");
var path = require("path");
var project = require("./project");
var workspace = require("./workspace");

var PIPELINE_GATE_MSG =
	"Open or create a project, or use legacy mode, to access the pipeline.";

function hasValidLegacyWorkspace() {
	var ws = workspace.loadWorkspace();
	if (!ws.brainRoot || !fs.existsSync(ws.brainRoot)) {
		return false;
	}
	return !!ws.countingRoot;
}

function hasPipelineAccess() {
	if (project.isActive()) {
		return true;
	}
	return hasValidLegacyWorkspace();
}

function assertPipelineAccess() {
	if (!hasPipelineAccess()) {
		window.location.replace("menu.html");
	}
}

function getContextLabel() {
	if (project.isActive()) {
		var name =
			(project.getProject() && project.getProject().name) ||
			path.basename(project.getBundleRoot());
		return {
			type: "project",
			label: name,
			detail: path.basename(project.getBundleRoot()),
		};
	}
	if (hasValidLegacyWorkspace()) {
		var ws = workspace.loadWorkspace();
		return {
			type: "legacy",
			label: "Legacy workspace",
			detail: path.basename(ws.brainRoot),
		};
	}
	return null;
}

function applyHubGate(gridEl, hintEl) {
	var access = hasPipelineAccess();
	if (hintEl) {
		hintEl.classList.toggle("d-none", access);
	}
	if (!gridEl) {
		return access;
	}
	var cards = gridEl.querySelectorAll(".menu-card-pipeline");
	for (var i = 0; i < cards.length; i++) {
		var card = cards[i];
		var cat = card.getAttribute("data-cat");
		var targetHref = cat ? "./menu_category.html?cat=" + cat : "#";
		if (access) {
			card.classList.remove("menu-card-disabled");
			card.setAttribute("href", targetHref);
			card.removeAttribute("aria-disabled");
			card.style.pointerEvents = "";
		} else {
			card.classList.add("menu-card-disabled");
			card.setAttribute("href", "#");
			card.setAttribute("aria-disabled", "true");
			card.style.pointerEvents = "none";
		}
	}
	return access;
}

module.exports = {
	PIPELINE_GATE_MSG: PIPELINE_GATE_MSG,
	hasPipelineAccess: hasPipelineAccess,
	hasValidLegacyWorkspace: hasValidLegacyWorkspace,
	assertPipelineAccess: assertPipelineAccess,
	getContextLabel: getContextLabel,
	applyHubGate: applyHubGate,
};
