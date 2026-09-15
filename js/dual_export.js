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
var flatOutput = document.getElementById("flatOutput");
var lastRunRel = "";

run.addEventListener("click", function () {
	if (indir && outdir && indir.value && outdir.value) {
		if (project.isActive()) {
			var pklsLeaf = pipelineRuns.resolveActiveRunLeafAbs("pkls");
			if (pklsLeaf) {
				indir.value = pklsLeaf;
			}
		}
		var pklsRel = pipelineRuns.getActiveRunRelForRole("pkls");
		var sortedStems = pipelineRuns.listImageSliceStems(indir.value);
		var slug = pipelineRuns.buildRunSlug("dual", {
			pklsRunRel: pklsRel,
			sortedStems: sortedStems,
		});
		var useFlat = flatOutput && flatOutput.checked;
		var finalOut = pipelineRuns.resolveStepOutputPath("dual", {
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
		lastRunRel = useFlat ? "" : pipelineRuns.relFromRoleBase("dual", finalOut);

		run.classList.add("disabled");
		back.classList.remove("btn-warning");
		back.classList.add("btn-danger");
		back.innerHTML = "Cancel";
		run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
		ipc.send("runExportDualTif", [indir.value, finalOut]);
		loadmessage.innerHTML = "Initializing...";
	}
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("killExportDualTif", []);
		back.classList.add("btn-warning");
		back.classList.remove("btn-danger");
		back.innerHTML = "Back";
		run.innerHTML = "Run";
		run.classList.remove("disabled");
		loadmessage.innerHTML = "";
		loadbar.style.width = "0";
	}
});

ipc.on("exportDualTifResult", function (_event, errDetail) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	loadbar.style.width = "0";
	if (project.isActive() && lastRunRel) {
		pipelineRuns.setActiveRunRel("dual", lastRunRel);
		project.refreshProjectIndex().catch(function () {});
	}
	if (errDetail) {
		loadmessage.textContent = String(errDetail);
	} else {
		loadmessage.innerHTML = "";
	}
});

ipc.on("updateLoad", function (event, response) {
	loadbar.style.width = String(response[0]) + "%";
	loadmessage.innerHTML = response[1];
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("dual");
	workspace.bindPathPicker(indir, "indir", "pkls");
	workspace.bindPathPicker(outdir, "outdir", "dual");
});
