"use strict";

var perfLog = require("./perf_log");
var navTrail = require("./nav_trail");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var legacyMode = require("./legacy_mode");

var CATEGORIES = {
	preprocess: {
		title: "Image preprocessing",
		tools: [
			{ label: "Max Projection", href: "./max.html" },
			{ label: "Sharpen", href: "./sharpen_wizard.html" },
			{ label: "Top-hat filter", href: "./tophat_wizard.html" },
			{
				label: "BaSiCPy Shading Correction",
				href: "./basic_wizard.html",
			},
			{ label: "Seam Correction", href: "./seam_wizard.html" },
			{ label: "Re-import sections from CZI", href: "./czi_wizard.html?flow=reextract" },
			{
				label: "Semi-manual tissue edge cleanup",
				href: "./tissue_cleanup_wizard.html",
			},
			{
				group: "Deprecated & Experimental",
				tools: [
					{ label: "Orient slices", href: "./orient.html" },
					{ label: "DAPI cleanup", href: "./dapi_cleanup.html" },
				],
			},
		],
	},
	alignment: {
		title: "Atlas alignment",
		tools: [
			{ label: "Align Sections", href: "./align.html" },
			{ label: "Viewer/Editor", href: "./adjust.html" },
			{ label: "Parcellation (bulk)", href: "./parcellation_wizard.html" },
		],
	},
	detection: {
		title: "Cell detection",
		tools: [
			{ label: "Cell Detection", href: "./detect_wizard.html" },
			{ label: "Count Brain", href: "./count.html" },
			{ label: "Collate Counts", href: "./collate.html", secondary: true },
		],
	},
	exports: {
		title: "Image and atlas exports",
		tools: [
			{ label: "Isolate Regions", href: "./intensity.html" },
			{ label: "Export dual-channel ROI TIFs", href: "./dual_export.html" },
		],
	},
};

function legacyStatusForTool(tool) {
	if (!tool || !tool.href) {
		return "full";
	}
	return legacyMode.getLegacyStatusForHref(tool.href);
}

function appendToolLink(container, tool, legacyContext) {
	var status = legacyStatusForTool(tool);
	var wrapper = document.createElement("div");
	wrapper.className = "text-start w-100";

	if (legacyContext && status === "blocked") {
		var blocked = document.createElement("button");
		blocked.type = "button";
		blocked.className = tool.secondary
			? "btn btn-secondary w-100"
			: "btn btn-primary w-100";
		blocked.disabled = true;
		blocked.textContent = tool.label;
		wrapper.appendChild(blocked);
		var blockedHint = document.createElement("p");
		blockedHint.className = "small text-muted mb-2";
		blockedHint.textContent = "Requires a .masonjar project.";
		wrapper.appendChild(blockedHint);
		container.appendChild(wrapper);
		return;
	}

	var link = document.createElement("a");
	link.role = "button";
	link.className = tool.secondary
		? "btn btn-secondary w-100"
		: "btn btn-primary w-100";
	link.href = tool.href;
	link.textContent = tool.label;
	wrapper.appendChild(link);

	if (legacyContext && status === "partial") {
		var partialHint = document.createElement("p");
		partialHint.className = "small text-warning mb-2";
		partialHint.textContent = "Limited in legacy mode — some project features unavailable.";
		wrapper.appendChild(partialHint);
	}

	container.appendChild(wrapper);
}

function appendToolGroup(container, groupDef, groupIndex, legacyContext) {
	var collapseId = "toolGroup" + groupIndex;
	var wrapper = document.createElement("div");
	wrapper.className = "mb-2 text-start";

	var toggle = document.createElement("button");
	toggle.type = "button";
	toggle.className = "btn btn-outline-secondary w-100";
	toggle.setAttribute("data-bs-toggle", "collapse");
	toggle.setAttribute("data-bs-target", "#" + collapseId);
	toggle.setAttribute("aria-expanded", "false");
	toggle.setAttribute("aria-controls", collapseId);
	toggle.textContent = groupDef.group;

	var collapse = document.createElement("div");
	collapse.id = collapseId;
	collapse.className = "collapse mt-2";

	var inner = document.createElement("div");
	inner.className = "d-grid gap-2";
	for (var j = 0; j < groupDef.tools.length; j++) {
		appendToolLink(inner, groupDef.tools[j], legacyContext);
	}
	collapse.appendChild(inner);
	wrapper.appendChild(toggle);
	wrapper.appendChild(collapse);
	container.appendChild(wrapper);
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
	var projectIndexBusy = require("./project_index_busy");
	projectIndexBusy.populatePage(function () {
		perfLog.perfSection("cat.tryRestoreActiveProject", function () {
			project.tryRestoreActiveProject();
		});
		perfLog.perfSection("cat.assertPipelineAccess", function () {
			pipelineGate.assertPipelineAccess();
		});
		var _catRenderT0 = perfLog.now();

		var isLegacyContext =
			!project.isActive() && pipelineGate.hasValidLegacyWorkspace();

		var params = new URLSearchParams(window.location.search);
		var cat = params.get("cat") || "preprocess";
		var config = CATEGORIES[cat] || CATEGORIES.preprocess;

		var categoryTitle = document.getElementById("categoryTitle");
		var toolLinks = document.getElementById("toolLinks");
		var legacyBanner = document.getElementById("legacyModeCategoryBanner");

		navTrail.renderTrail(
			[
				{ label: "Start", href: "./menu.html" },
				{ label: "Workspace", href: "./workspace_menu.html" },
				{ label: config.title },
			],
			"navTrail",
		);

		if (legacyBanner) {
			legacyBanner.classList.toggle("d-none", !isLegacyContext);
		}

		if (categoryTitle) {
			categoryTitle.textContent = config.title;
		}
		if (toolLinks) {
			toolLinks.innerHTML = "";
			var groupIndex = 0;
			for (var i = 0; i < config.tools.length; i++) {
				var tool = config.tools[i];
				if (tool.group && tool.tools && tool.tools.length) {
					appendToolGroup(toolLinks, tool, groupIndex, isLegacyContext);
					groupIndex++;
				} else if (tool.href) {
					appendToolLink(toolLinks, tool, isLegacyContext);
				}
			}
		}
		perfLog.perfLog("cat.render", perfLog.now() - _catRenderT0);
		// This page renders only static tool links (no index-derived data), so
		// don't block on the full file-index build. openProject() still kicks
		// off the refresh in the background to warm the cache for step pages.
	}, { waitForIndex: false });
}

module.exports = { CATEGORIES: CATEGORIES };
