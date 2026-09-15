var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var projectIndexBusy = require("./project_index_busy");

var run = document.getElementById("run");
var indir = document.getElementById("indir");
var outdir = document.getElementById("outdir");
var loadbar = document.getElementById("loadbar");
var loadmessage = document.getElementById("loadmessage");
var back = document.getElementById("back");
var isolateCheckbox = document.getElementById("isolate");
var claheCheckbox = document.getElementById("clahe");
var reBackupCheckbox = document.getElementById("reBackup");
var saturationInput = document.getElementById("saturation");
var bgValueInput = document.getElementById("bgValue");
var outputInPlace = document.getElementById("outputInPlace");
var outputSeparate = document.getElementById("outputSeparate");
var outdirRow = document.getElementById("outdirRow");
var outdirInputRow = document.getElementById("outdirInputRow");

pipelineRun.ensureRunModeUi("runModePanel", "dapi_cleanup");

function dapiCleanDirForInput(inputDir) {
	if (!inputDir) {
		return "";
	}
	var parent = path.dirname(inputDir);
	if (path.basename(inputDir) === "00_dapi") {
		return path.join(parent, "00_dapi_clean");
	}
	return path.join(inputDir + "_clean");
}

function dapiBackupDirForInput(inputDir) {
	if (!inputDir) {
		return "";
	}
	var parent = path.dirname(inputDir);
	if (path.basename(inputDir) === "00_dapi") {
		return path.join(parent, "00_dapi_backup");
	}
	return path.join(inputDir + "_backup");
}

function isInPlaceMode() {
	return outputInPlace && outputInPlace.checked;
}

function syncOutputModeUi() {
	var separate = outputSeparate && outputSeparate.checked;
	if (outdirRow) {
		outdirRow.classList.toggle("d-none", !separate);
	}
	if (outdirInputRow) {
		outdirInputRow.classList.toggle("d-none", !separate);
	}
	if (separate && indir && indir.value && outdir) {
		if (!outdir.value || outdir.value === indir.value) {
			outdir.value = dapiCleanDirForInput(indir.value);
		}
	}
}

function resolveOutputDir(inputDir) {
	if (isInPlaceMode()) {
		return inputDir;
	}
	if (outdir && outdir.value) {
		return outdir.value;
	}
	return dapiCleanDirForInput(inputDir);
}

function checkNumber(value, message, min, max) {
	var str = String(value).trim();
	if (!str.match(/^-?\d*\.?\d+$/)) {
		alert(message);
		return false;
	}
	var num = parseFloat(str);
	if (min != null && num < min) {
		alert(message);
		return false;
	}
	if (max != null && num > max) {
		alert(message);
		return false;
	}
	return true;
}

if (outputInPlace) {
	outputInPlace.addEventListener("change", syncOutputModeUi);
}
if (outputSeparate) {
	outputSeparate.addEventListener("change", syncOutputModeUi);
}
if (indir) {
	indir.addEventListener("change", syncOutputModeUi);
	indir.addEventListener("blur", syncOutputModeUi);
}

run.addEventListener("click", function () {
	if (!indir || !indir.value) {
		alert("Select an input directory.");
		return;
	}
	var dapiInput = indir.value;
	if (project.isActive()) {
		var preset = workspace.getPreset("dapi_cleanup");
		if (preset && preset.indir) {
			dapiInput = preset.indir;
			indir.value = dapiInput;
		}
	}

	var finalOut = resolveOutputDir(dapiInput);
	if (!finalOut) {
		alert("Could not resolve output directory.");
		return;
	}

	var mode = pipelineRun.getSelectedRunMode("dapi_cleanup");
	var plan = pipelineRun.preparePipelineRun("dapi_cleanup", mode);
	if (project.isActive() && !plan.toProcess.length) {
		alert("No slices to process (subset empty or all filtered).");
		return;
	}

	if (!checkNumber(saturationInput.value, "Saturation must be a number between 0 and 49.", 0, 49)) {
		return;
	}
	var bgStr = bgValueInput ? String(bgValueInput.value).trim() : "";
	if (bgStr && !checkNumber(bgStr, "Background level must be 0–255.", 0, 255)) {
		return;
	}

	try {
		fs.mkdirSync(finalOut, { recursive: true });
	} catch (mkdirErr) {
		alert("Could not create output directory: " + (mkdirErr.message || mkdirErr));
		return;
	}

	var backupDir = isInPlaceMode() ? dapiBackupDirForInput(dapiInput) : "";
	if (backupDir) {
		try {
			fs.mkdirSync(backupDir, { recursive: true });
		} catch (backupErr) {
			alert("Could not create backup directory: " + (backupErr.message || backupErr));
			return;
		}
	}

	run.classList.add("disabled");
	back.classList.remove("btn-warning");
	back.classList.add("btn-danger");
	back.innerHTML = "Cancel";
	run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";

	var data = [
		dapiInput,
		finalOut,
		isolateCheckbox.checked,
		claheCheckbox.checked,
		parseFloat(saturationInput.value),
		backupDir || "",
		plan.sliceListPath || "",
		reBackupCheckbox.checked,
		bgStr,
	];
	if (loadmessage) {
		loadmessage.innerHTML = plan.summary || "Initializing...";
	}
	ipc.send("runDapiCleanup", data);
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("killDapiCleanup", []);
		back.classList.add("btn-warning");
		back.classList.remove("btn-danger");
		back.innerHTML = "Back";
		run.innerHTML = "Run";
		run.classList.remove("disabled");
		loadmessage.innerHTML = "";
		loadbar.style.width = "0";
	}
});

ipc.on("dapiCleanupResult", function () {
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	loadmessage.innerHTML = "";
	loadbar.style.width = "0";
	if (project.isActive()) {
		var bundleRoot = project.getBundleRoot();
		project.refreshProjectIndex(bundleRoot).catch(function () {});
	}
});

ipc.on("updateLoad", function (event, response) {
	loadbar.style.width = String(response[0]) + "%";
	loadmessage.innerHTML = response[1];
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("dapi_cleanup");
	workspace.bindPathPicker(indir, "indir", "dapi");
	workspace.bindPathPicker(outdir, "outdir", "dapi");
	syncOutputModeUi();
});
