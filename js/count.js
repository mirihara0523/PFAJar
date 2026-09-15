"use strict";

var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var pipelineRuns = require("./pipeline_runs");
var activeRunControls = require("./active_run_controls");
var projectIndexBusy = require("./project_index_busy");
var run = document.getElementById("run");
var preddir = document.getElementById("preddir");
var annodir = document.getElementById("annodir");
var outdir = document.getElementById("outdir");
var loadbar = document.getElementById("loadbar");
var loadmessage = document.getElementById("loadmessage");
var back = document.getElementById("back");
var predictionRunRow = document.getElementById("predictionRunRow");
var predictionRunSelect = document.getElementById("predictionRunSelect");
var slicesRunRow = document.getElementById("slicesRunRow");
var slicesRunSelect = document.getElementById("slicesRunSelect");
var flatOutput = document.getElementById("flatOutput");
var resultLabel = document.getElementById("resultLabel");
var lastRunRel = "";
var lastProgressMessage = "";

function hideResultLabel() {
	if (!resultLabel) {
		return;
	}
	resultLabel.classList.add("d-none");
	resultLabel.textContent = "";
}

function showResultLabel(variant, message) {
	if (!resultLabel) {
		return;
	}
	var cls =
		variant === true || variant === "success"
			? "alert-success"
			: variant === "warning"
			? "alert-warning"
			: "alert-danger";
	resultLabel.className = "alert text-start " + cls;
	resultLabel.textContent = message;
}

pipelineRun.ensureRunModeUi("runModePanel", "count");

function populateRunSelect(selectEl, role, onChange) {
	if (!selectEl) {
		return;
	}
	project.ensureDefaultActiveRunForRole(role);
	var choices = project.listRunChoicesForRole(role);
	selectEl.innerHTML = "";
	if (!choices.length) {
		return;
	}
	var active = pipelineRuns.getActiveRunRelForRole(role);
	for (var i = 0; i < choices.length; i++) {
		var opt = document.createElement("option");
		opt.value = choices[i].rel;
		opt.textContent = choices[i].label;
		if (choices[i].rel === active) {
			opt.selected = true;
		}
		selectEl.appendChild(opt);
	}
	var row = selectEl.parentElement;
	if (row && !row.querySelector(".active-run-delete-btn")) {
		row.classList.add("d-flex", "align-items-center");
		selectEl.classList.add("flex-grow-1");
		var delBtn = activeRunControls.attachRunDeleteButton(selectEl, role, {
			onDeleted: function () {
				refreshPredictionRunUi();
			},
		});
		delBtn.classList.add("active-run-delete-btn");
		row.appendChild(delBtn);
	}
	if (typeof onChange === "function") {
		onChange(active);
	}
}

function refreshPredictionRunUi() {
	if (!preddir || !predictionRunSelect || !predictionRunRow) {
		return;
	}
	if (!project.isActive()) {
		predictionRunRow.classList.add("d-none");
		if (slicesRunRow) {
			slicesRunRow.classList.add("d-none");
		}
		return;
	}
	predictionRunRow.classList.remove("d-none");
	if (slicesRunRow) {
		slicesRunRow.classList.remove("d-none");
	}
	populateRunSelect(predictionRunSelect, "predictions", function () {
		var leaf = pipelineRuns.resolveActiveRunLeafAbs("predictions");
		if (leaf) {
			preddir.value = leaf;
		}
	});
	populateRunSelect(slicesRunSelect, "slices", function () {
		var slicesLeaf = pipelineRuns.resolveActiveRunLeafAbs("slices");
		if (slicesLeaf) {
			annodir.value = slicesLeaf;
		}
	});
}

if (predictionRunSelect) {
	predictionRunSelect.addEventListener("change", function () {
		if (!project.isActive()) {
			return;
		}
		project.setActiveRunForRole("predictions", predictionRunSelect.value);
		refreshPredictionRunUi();
	});
}

if (slicesRunSelect) {
	slicesRunSelect.addEventListener("change", function () {
		if (!project.isActive()) {
			return;
		}
		project.setActiveRunForRole("slices", slicesRunSelect.value);
		refreshPredictionRunUi();
	});
}

run.addEventListener("click", function () {
	if (preddir && annodir && outdir && preddir.value && annodir.value && outdir.value) {
		if (project.isActive()) {
			refreshPredictionRunUi();
		}
		var mode = pipelineRun.getSelectedRunMode("count");
		var plan = pipelineRun.preparePipelineRun("count", mode);
		if (project.isActive() && !plan.toProcess.length) {
			alert("No slices to process (subset empty or all filtered).");
			return;
		}
		var predRel = pipelineRuns.getActiveRunRelForRole("predictions");
		var slicesRel = pipelineRuns.getActiveRunRelForRole("slices");
		var slug = pipelineRuns.buildRunSlug("count", {
			predictionRunRel: predRel,
			slicesRunRel: slicesRel,
			subsetCount: plan.toProcess.length,
		});
		var useFlat = flatOutput && flatOutput.checked;
		var finalOut = pipelineRuns.resolveStepOutputPath("count", {
			slug: slug,
			flat: useFlat,
			runMode: mode,
			legacyOutBase: outdir.value,
		});
		try {
			fs.mkdirSync(finalOut, { recursive: true });
		} catch (mkdirErr) {
			alert("Could not create output directory: " + (mkdirErr.message || mkdirErr));
			return;
		}
		lastRunRel = useFlat ? "" : pipelineRuns.relFromRoleBase("count", finalOut);
		lastProgressMessage = "";
		hideResultLabel();

		run.classList.add("disabled");
		back.classList.remove("btn-warning");
		back.classList.add("btn-danger");
		back.innerHTML = "Cancel";
		run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
		if (plan.summary && loadmessage) {
			loadmessage.innerHTML = plan.summary;
		}
		ipc.send("runCount", [
			preddir.value,
			annodir.value,
			finalOut,
			plan.sliceListPath || "",
		]);
	}
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("killCount", []);
		back.classList.add("btn-warning");
		back.classList.remove("btn-danger");
		back.innerHTML = "Back";
		run.innerHTML = "Run";
		run.classList.remove("disabled");
		loadmessage.innerHTML = "";
		loadbar.style.width = "0";
		showResultLabel("warning", "Count Brain cancelled.");
	}
});

ipc.on("countResult", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	loadmessage.innerHTML = "";
	loadbar.style.width = "0";
	if (project.isActive() && lastRunRel) {
		pipelineRuns.setActiveRunRel("count", lastRunRel);
		project.refreshProjectIndex().catch(function () {});
	}
	var doneMessage = "Count Brain finished.";
	if (lastProgressMessage) {
		doneMessage += " (" + lastProgressMessage + ")";
	}
	showResultLabel(true, doneMessage);
});

ipc.on("countError", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	loadmessage.innerHTML = "";
	loadbar.style.width = "0";
	showResultLabel(false, "Count Brain failed. Check the Application log for details.");
});

ipc.on("updateLoad", function (event, response) {
	loadbar.style.width = String(response[0]) + "%";
	loadmessage.innerHTML = response[1];
	if (response[1]) {
		lastProgressMessage = response[1];
	}
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("count");
	workspace.bindPathPicker(preddir, "preddir", "predictions");
	workspace.bindPathPicker(annodir, "annodir", "slices");
	workspace.bindPathPicker(outdir, "outdir", "quantification");
	refreshPredictionRunUi();
});
