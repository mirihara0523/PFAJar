var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var pipelineRuns = require("./pipeline_runs");
var alignIpc = require("./align_ipc");
var projectIndexBusy = require("./project_index_busy");
var run = document.getElementById("run");
var imdir = document.getElementById("imdir");
var annodir = document.getElementById("annodir");
var loadbar = document.getElementById("loadbar");
var loadmessage = document.getElementById("loadmessage");
var back = document.getElementById("back");

run.addEventListener("click", function () {
	if (imdir && imdir.value && annodir && annodir.value) {
		if (project.isActive()) {
			var slicesLeaf = pipelineRuns.resolveActiveRunLeafAbs("slices");
			if (slicesLeaf) {
				annodir.value = slicesLeaf;
			}
		}
		var session = pipelineRun.prepareAdjustSession();
		if (project.isActive() && !session.sliceIds.length) {
			alert(
				"No matched DAPI/annotation pairs in the project index. Check paths and file names.",
			);
			return;
		}

		run.classList.add("disabled");
		back.classList.remove("btn-warning");
		back.classList.add("btn-danger");
		back.innerHTML = "Cancel";
		run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
		if (session.summary && loadmessage) {
			loadmessage.innerHTML = session.summary;
		}
		ipc.send("runAdjust", [
			imdir.value,
			annodir.value,
			session.sliceListPath || "",
		]);
	}
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("saveAndExitAdjust", []);
	}
});

ipc.on("adjustResult", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	loadbar.style.width = "0";
	if (response && response.cancelled) {
		loadmessage.textContent = "Viewer/Editor closed.";
		return;
	}
	loadmessage.innerHTML = "";
});

ipc.on("adjustError", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	loadbar.style.width = "0";
	if (response && response[0]) {
		loadmessage.textContent = String(response[0]);
	}
});

ipc.on("updateLoad", function (event, response) {
	loadbar.style.width = String(response[0]) + "%";
	loadmessage.innerHTML = response[1];
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("adjust");
	workspace.bindPathPicker(imdir, "imdir", "dapi");
	workspace.bindPathPicker(annodir, "annodir", "slices");
});
