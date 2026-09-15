var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var pipelineRuns = require("./pipeline_runs");
var importHandoff = require("./import_handoff");
var projectIndexBusy = require("./project_index_busy");
var run = document.getElementById("run");
var indir = document.getElementById("indir");
var outdir = document.getElementById("outdir");
var loadbar = document.getElementById("loadbar");
var loadmessage = document.getElementById("loadmessage");
var back = document.getElementById("back");
var flatOutput = document.getElementById("flatOutput");
var lastRunRel = "";

pipelineRun.ensureRunModeUi("runModePanel", "max");

run.addEventListener("click", function () {
	if (indir && outdir && indir.value && outdir.value) {
		var mode = pipelineRun.getSelectedRunMode("max");
		var plan = pipelineRun.preparePipelineRun("max", mode);
		if (project.isActive() && !plan.toProcess.length) {
			alert("No slices to process (subset empty or all filtered).");
			return;
		}
		var sortedStems = pipelineRuns.listImageSliceStems(indir.value);
		var slug = pipelineRuns.buildRunSlug("max", {
			sortedStems: sortedStems,
			subsetCount: plan.toProcess.length,
		});
		var useFlat = flatOutput && flatOutput.checked;
		var finalOut = pipelineRuns.resolveStepOutputPath("max", {
			slug: slug,
			flat: useFlat,
			runMode: mode,
			indirAbs: indir.value,
			legacyOutBase: outdir.value,
		});
		try {
			fs.mkdirSync(finalOut, { recursive: true });
		} catch (mkdirErr) {
			alert("Could not create output directory: " + (mkdirErr.message || mkdirErr));
			return;
		}
		lastRunRel = useFlat ? "" : pipelineRuns.relFromRoleBase("max", finalOut);

		run.classList.add("disabled");
		back.classList.remove("btn-warning");
		back.classList.add("btn-danger");
		back.innerHTML = "Cancel";
		run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
		if (loadmessage) {
			loadmessage.innerHTML = plan.summary || "";
		}
		ipc.send("runMax", [indir.value, finalOut, false, false]);
	}
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("killMax", []);
		back.classList.add("btn-warning");
		back.classList.remove("btn-danger");
		back.innerHTML = "Back";
		run.innerHTML = "Run";
		run.classList.remove("disabled");
		loadmessage.innerHTML = "";
		loadbar.style.width = "0";
	}
});

ipc.on("maxResult", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	loadmessage.innerHTML = "";
	loadbar.style.width = "0";
	if (!response || response.ok !== true) {
		loadmessage.textContent = (response && response.message) || "MAX failed. Check the log for failed files.";
		return;
	}
	if (project.isActive() && lastRunRel) {
		pipelineRuns.setActiveRunRel("max", lastRunRel);
		project.refreshProjectIndex().catch(function () {});
	}
});

ipc.once("maxError", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
});

ipc.on("updateLoad", function (event, response) {
	loadbar.style.width = String(response[0]) + "%";
	loadmessage.innerHTML = response[1];
});

function renderMaxImportHandoffAlert() {
	var alertEl = document.getElementById("maxImportHandoffAlert");
	if (!alertEl) {
		return;
	}
	if (!project.isActive() || !importHandoff.isMaxFromCziImport(project.getBundleRoot(), project.getProject())) {
		alertEl.classList.add("d-none");
		alertEl.innerHTML = "";
		return;
	}
	var handoff = importHandoff.getImportHandoffState(
		project.getBundleRoot(),
		project.getProject(),
	);
	alertEl.classList.remove("d-none");
	alertEl.innerHTML =
		"<strong>Max projection already completed during CZI import</strong> (" +
		(handoff.maxRunLabel || "active run") +
		"). You usually do not need to run this step again unless you add new z-stacks. " +
		'Next step: <a href="./menu_category.html?cat=alignment">Atlas alignment</a>. ' +
		'If alignment is difficult, try <a href="./menu_category.html?cat=preprocess">counterstain cleanup tools</a>.';
}

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("max");
	workspace.bindPathPicker(indir, "indir", "originalScans");
	workspace.bindPathPicker(outdir, "outdir", "max");
	renderMaxImportHandoffAlert();
});
