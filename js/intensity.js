"use strict";

var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var pipelineRuns = require("./pipeline_runs");
var branding = require("./branding");
var parcelCtx = require("./parcellation_context");
var labelAudit = require("./annotation_label_audit");
var maxDatasetPicker = require("./max_dataset_picker");

var SETUP_KEY = "masonjar.intensity.setup";
var projectIndexBusy = require("./project_index_busy");

var configureBtn = document.getElementById("run");
var back = document.getElementById("back");
var indir = document.getElementById("indir");
var annodir = document.getElementById("annodir");
var outdir = document.getElementById("outdir");
var dapidir = document.getElementById("dapidir");
var usedapi = document.getElementById("usedapi");
var whole = document.getElementById("whole");
var half = document.getElementById("half");
var flatOutput = document.getElementById("flatOutput");
var alignmentMethod = "True";
var methods = document.querySelector("#methods");

pipelineRun.ensureRunModeUi("runModePanel", "intensity");

if (configureBtn) {
	configureBtn.textContent = "Configure outputs";
}

if (usedapi && dapidir) {
	usedapi.addEventListener("change", function () {
		dapidir.disabled = !usedapi.checked;
		if (!usedapi.checked) {
			dapidir.value = "";
		}
	});
}

function applyWholeChoice(isWhole) {
	alignmentMethod = isWhole ? "True" : "False";
	if (methods) {
		methods.textContent = isWhole ? "Whole Slice" : "Hemisphere Only";
	}
	try {
		localStorage.setItem(branding.INTENSITY_WHOLE_KEY, alignmentMethod);
	} catch (_err) {
		// ignore
	}
}

whole.addEventListener("click", function (event) {
	event.preventDefault();
	applyWholeChoice(true);
});

half.addEventListener("click", function (event) {
	event.preventDefault();
	applyWholeChoice(false);
});

try {
	var savedWhole = localStorage.getItem(branding.INTENSITY_WHOLE_KEY);
	if (savedWhole === "False") {
		applyWholeChoice(false);
	} else if (savedWhole === "True") {
		applyWholeChoice(true);
	}
} catch (_restoreErr) {
	// ignore
}

configureBtn.addEventListener("click", function () {
	if (indir && outdir && indir.value && outdir.value && annodir && annodir.value) {
		if (usedapi && usedapi.checked) {
			if (!dapidir || !dapidir.value) {
				alert("Select a DAPI PNG folder, or uncheck Include DAPI.");
				return;
			}
		}
		if (project.isActive()) {
			var slicesLeaf = pipelineRuns.resolveActiveRunLeafAbs("slices");
			if (slicesLeaf) {
				annodir.value = slicesLeaf;
			}
			if (!slicesLeaf || !pipelineRuns.hasRunMarkers(slicesLeaf, "align")) {
				alert(
					"No alignment output with annotation PKLs. Choose slices on the workspace menu under Completed tasks (01_slices/align/...).",
				);
				return;
			}
		}
		var mode = pipelineRun.getSelectedRunMode("intensity");
		var plan = pipelineRun.preparePipelineRun("intensity", mode);
		if (project.isActive() && !plan.toProcess.length) {
			alert("No slices to process (all outputs exist or subset is empty).");
			return;
		}
		var sortedStems = pipelineRuns.listImageSliceStems(indir.value);
		var dapiPath =
			usedapi && usedapi.checked && dapidir && dapidir.value ? dapidir.value : "";
		var payload = {
			indir: indir.value,
			annodir: annodir.value,
			outdir: outdir.value,
			dapiPath: dapiPath,
			whole: alignmentMethod,
			useDapi: usedapi && usedapi.checked,
			flatOutput: flatOutput && flatOutput.checked,
			runMode: mode,
			sliceListPath: plan.sliceListPath || "",
			subsetCount: plan.toProcess.length,
			sortedStems: sortedStems,
			planSummary: plan.summary || "",
			bundleRoot: project.isActive() ? project.getBundleRoot() : "",
		};
		try {
			sessionStorage.setItem(SETUP_KEY, JSON.stringify(payload));
		} catch (err) {
			alert("Could not save setup: " + (err.message || err));
			return;
		}
		window.location.href = "./intensity_wizard.html";
	}
});

function updateLabelResolutionBanner(audit) {
	var banner = document.getElementById("labelResolutionBanner");
	if (!banner) return;
	var annodirPath = annodir && annodir.value ? annodir.value : "";
	if (!annodirPath) {
		banner.classList.add("d-none");
		return;
	}
	var bundleRoot = project.isActive() ? project.getBundleRoot() : "";
	var stale = labelAudit.annotationsNewerThanIntensity(annodirPath, bundleRoot);
	var html = labelAudit.formatIntensityAuditBanner(audit, null, {
		staleIntensity: stale,
	});
	if (!html) {
		banner.classList.add("d-none");
		return;
	}
	banner.innerHTML = html;
	banner.classList.remove("d-none");
}

function refreshLabelAuditBanners() {
	var annodirPath = annodir && annodir.value ? annodir.value : "";
	if (!annodirPath) {
		updateLabelResolutionBanner(null);
		return;
	}
	labelAudit.ensureAudit(annodirPath, "").then(function (payload) {
		updateLabelResolutionBanner(payload && payload.audit ? payload.audit : null);
		var includeLayersEl = document.getElementById("includeLayers");
		if (
			includeLayersEl &&
			payload &&
			payload.audit &&
			labelAudit.auditSuggestsIncludeLayers(payload.audit)
		) {
			var pSummary = parcelCtx.summarizeParcellationForLeaf(annodirPath);
			if (parcelCtx.includeLayersAllowed(pSummary)) {
				includeLayersEl.checked = true;
			}
		}
	});
}

function updateParcellationBanner() {
	var banner = document.getElementById("parcellationBanner");
	if (!banner) return;
	var annodirPath = annodir && annodir.value ? annodir.value : "";
	if (!annodirPath) {
		banner.classList.add("d-none");
		return;
	}
	var summary = parcelCtx.summarizeParcellationForLeaf(annodirPath);
	if (!summary.hasParcellation) {
		banner.classList.add("d-none");
		return;
	}
	var label = parcelCtx.formatParcellationLabel({
		tier_id: summary.tierId,
		st_level: summary.stLevel,
	});
	var msg =
		"Active align run uses <strong>" +
		label +
		"</strong> parcellation. Region selections in the wizard are rolled up to match annotation labels. " +
		'<a href="./parcellation_wizard.html">Change parcellation</a>. ' +
		"Re-run Isolate Regions after changing parcellation.";
	if (summary.mixedTiers) {
		msg +=
			" <span class=\"text-warning\">Mixed parcellation tiers across slices — results use per-slice context.</span>";
	}
	if (!parcelCtx.includeLayersAllowed(summary)) {
		msg += " Include cortical layers is not available at this parcellation level.";
	}
	banner.innerHTML = msg;
	banner.classList.remove("d-none");
}

if (annodir) {
	annodir.addEventListener("change", function () {
		updateParcellationBanner();
		refreshLabelAuditBanners();
	});
	annodir.addEventListener("input", function () {
		updateParcellationBanner();
		refreshLabelAuditBanners();
	});
}

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("intensity");
	if (project.isActive()) {
		if (outdir) {
			var pklsBase = pipelineRuns.resolveRoleBaseAbs("pkls");
			if (pklsBase) {
				outdir.value = pklsBase;
			}
		}
		if (annodir) {
			var slicesLeafPreset = pipelineRuns.resolveActiveRunLeafAbs("slices");
			if (slicesLeafPreset) {
				annodir.value = slicesLeafPreset;
			}
		}
	}
	workspace.bindPathPicker(indir, "indir", "max");
	workspace.bindPathPicker(outdir, "outdir", "pkls");
	workspace.bindPathPicker(annodir, "annodir", "slices");
	if (dapidir) {
		workspace.bindPathPicker(dapidir, "dapidir", "dapi");
	}
	maxDatasetPicker.wireMaxDatasetPicker({
		storageKey: "masonjar.intensity.maxDataset",
		indirInput: indir,
		sectionId: "intensityDatasetSection",
		branchSelectId: "intensitySignalBranch",
		datasetSelectId: "intensityMaxDataset",
		defaultBranch: function () {
			return "somata";
		},
	});
	updateParcellationBanner();
	refreshLabelAuditBanners();
});
