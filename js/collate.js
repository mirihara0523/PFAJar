var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRuns = require("./pipeline_runs");
var projectIndexBusy = require("./project_index_busy");
var run = document.getElementById("run");
var indir = document.getElementById("indir");
var outdir = document.getElementById("outdir");
var loadbar = document.getElementById("loadbar");
var loadmessage = document.getElementById("loadmessage");
var back = document.getElementById("back");
var regions = document.getElementById("regions");
var flatOutput = document.getElementById("flatOutput");
var lastRunRel = "";

run.addEventListener("click", function () {
	if (indir && outdir && indir.value && outdir.value) {
		if (project.isActive()) {
			var countLeaf = pipelineRuns.resolveActiveBranchLeafAbs(
				"quantification",
				"count",
			);
			if (countLeaf) {
				indir.value = countLeaf;
			}
		}
		var sourceRel = pipelineRuns.getActiveRunRelForRole("quantification");
		var slug = pipelineRuns.buildRunSlug("collate", {
			sourceRunRel: sourceRel,
		});
		var useFlat = flatOutput && flatOutput.checked;
		var finalOut = pipelineRuns.resolveStepOutputPath("collate", {
			slug: slug,
			flat: useFlat,
			legacyOutBase: outdir.value,
		});
		try {
			fs.mkdirSync(finalOut, { recursive: true });
		} catch (mkdirErr) {
			alert("Could not create output directory: " + (mkdirErr.message || mkdirErr));
			return;
		}
		lastRunRel = useFlat ? "" : pipelineRuns.relFromRoleBase("collate", finalOut);

		run.classList.add("disabled");
		back.classList.remove("btn-warning");
		back.classList.add("btn-danger");
		back.innerHTML = "Cancel";
		run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
		ipc.send("runCollate", [indir.value, finalOut, regions.innerText]);
		loadmessage.innerHTML = "Initializing...";
	}
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("killCollate", []);
		back.classList.add("btn-warning");
		back.classList.remove("btn-danger");
		back.innerHTML = "Back";
		run.innerHTML = "Run";
		run.classList.remove("disabled");
		loadmessage.innerHTML = "";
		loadbar.style.width = "0";
	}
});

ipc.on("collateResult", function (event, response) {
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	loadmessage.innerHTML = "";
	loadbar.style.width = "0";
	if (project.isActive() && lastRunRel) {
		pipelineRuns.setActiveRunRel("collate", lastRunRel);
		project.refreshProjectIndex().catch(function () {});
	}
});

ipc.once("detectError", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
});

ipc.on("updateLoad", function (event, response) {
	loadbar.style.width = String(response[0]) + "%";
	loadmessage.innerHTML = response[1];
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("collate");
	workspace.bindPathPicker(indir, "indir", "quantification", true);
	workspace.bindPathPicker(outdir, "outdir", "quantification");
});
