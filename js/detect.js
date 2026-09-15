"use strict";

var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var pipelineRuns = require("./pipeline_runs");
var maxDatasets = require("./max_datasets");
var maxDatasetPicker = require("./max_dataset_picker");
var projectIndexBusy = require("./project_index_busy");
var run = document.getElementById("run");
var indir = document.getElementById("indir");
var outdir = document.getElementById("outdir");
var loadbar = document.getElementById("loadbar");
var tile = document.getElementById("tile");
var confidence = document.getElementById("confidence");
var eccentricity = document.getElementById("eccentricity");
var model = document.getElementById("model");
var loadmessage = document.getElementById("loadmessage");
var back = document.getElementById("back");
var advance = document.getElementById("advance");
var arrow = document.getElementById("arrow");
var multichannel = document.getElementById("multichannel");
var methods = document.querySelector("#methods");
var somata = document.getElementById("somata");
var nuclei = document.getElementById("nuclei");
var area = document.getElementById("area");
var flatOutput = document.getElementById("flatOutput");
var perSliceQc = document.getElementById("perSliceQc");
var detectionMethod = "somata";
var lastDetectionRunRel = "";
var PER_SLICE_QC_KEY = "masonjar.detect.perSliceQc";

pipelineRun.ensureRunModeUi("runModePanel", "detect");

var IMAGE_EXT_RE = /\.(tif|tiff|png|jpe?g)$/i;

function sliceStemFromImageBasename(basename) {
	var stem = path.parse(basename).name;
	if (/\.ome$/i.test(stem)) {
		stem = path.parse(stem).name;
	}
	var dot = stem.indexOf(".");
	return dot >= 0 ? stem.slice(0, dot) : stem;
}

function listInputSliceStems(indirPath) {
	if (!indirPath || !fs.existsSync(indirPath)) {
		return [];
	}
	var entries;
	try {
		entries = fs.readdirSync(indirPath, { withFileTypes: true });
	} catch (err) {
		return [];
	}
	var stems = [];
	for (var i = 0; i < entries.length; i++) {
		if (!entries[i].isFile()) {
			continue;
		}
		var n = entries[i].name;
		if (IMAGE_EXT_RE.test(n) || n.toLowerCase().indexOf(".ome.") !== -1) {
			stems.push(sliceStemFromImageBasename(n));
		}
	}
	stems.sort();
	return stems;
}

function modelBranchForSlug(detectionMethod, modelPath) {
	var m = (modelPath || "").trim();
	if (m) {
		var base = path.basename(m).replace(/\.pt$/i, "");
		return pipelineRuns.sanitizeSlugPart(base) || "custom";
	}
	return detectionMethod === "nuclei" ? "nuclei" : "somata";
}

var datasetPicker = null;

somata.addEventListener("click", function () {
	methods.textContent = "Somata";
	detectionMethod = "somata";
	if (datasetPicker) {
		datasetPicker.refresh();
	}
});

nuclei.addEventListener("click", function () {
	methods.textContent = "Nuclei";
	detectionMethod = "nuclei";
	if (datasetPicker) {
		datasetPicker.refresh();
	}
});

advance.addEventListener("click", function () {
	arrow.classList.toggle("down");
});

if (perSliceQc) {
	try {
		perSliceQc.checked = localStorage.getItem(PER_SLICE_QC_KEY) === "1";
	} catch (_err) {
		/* ignore */
	}
	perSliceQc.addEventListener("change", function () {
		try {
			localStorage.setItem(PER_SLICE_QC_KEY, perSliceQc.checked ? "1" : "0");
		} catch (_err) {
			/* ignore */
		}
	});
}

function checkNumber(value, message) {
	var str = value.toString();
	if (!str.match(/^-?\d*\.?\d*$/)) {
		alert(`${message}`);
		return false;
	}
	return true;
}

run.addEventListener("click", function () {
	var c = 0.5;
	var e = 0.2;
	var a = 200;
	var t = 640;
	var m = "";
	var mc = false;

	if (indir && outdir && indir.value && outdir.value) {
		if (confidence.value && confidence.value < 1 && confidence.value > 0) {
			c = checkNumber(
				confidence.value,
				"Confidence should be a float between 0-1, using default.",
			)
				? confidence.value
				: 0.5;
		}

		if (
			eccentricity.value &&
			eccentricity.value < 1 &&
			eccentricity.value > 0
		) {
			e = checkNumber(
				eccentricity.value,
				"Eccentricity should be a float between 0-1, using default.",
			)
				? eccentricity.value
				: 0.5;
		}

		if (area.value && area.value > 0) {
			a = checkNumber(area.value, "Area should be an integer, using default.")
				? area.value
				: 200;
		}

		if (tile.value && tile.value > 0) {
			t = checkNumber(tile.value, "Tile should be an integer, using default.")
				? tile.value
				: 640;
		}
		if (model.value) {
			m = model.value;
		}
		if (multichannel.checked) {
			mc = true;
		}

		var mode = pipelineRun.getSelectedRunMode("detect");
		var sortedStems = listInputSliceStems(indir.value);
		if (project.isActive() && !sortedStems.length) {
			alert("No slices to process (input folder has no images).");
			return;
		}
		var modelBranch = modelBranchForSlug(detectionMethod, m);
		var inputDatasetRel = "";
		if (project.isActive() && indir.value) {
			inputDatasetRel =
				pipelineRuns.relFromRoleBase("max", indir.value) || "";
		}
		var signalBranch =
			pipelineRuns.inferSignalBranchForMaxFamily(
				inputDatasetRel,
				indir.value,
			) || modelBranch;
		var slug = pipelineRuns.buildDetectRunSlug({
			confidence: Number(c),
			tile: Number(t),
			area: Number(a),
			eccentricity: Number(e),
			sortedStems: sortedStems,
			subsetCount: sortedStems.length,
			inputDatasetRel: inputDatasetRel,
			modelBranch: modelBranch,
		});
		var useFlat = flatOutput && flatOutput.checked;
		var finalOut = pipelineRuns.resolveStepOutputPath("detect", {
			slug: slug,
			flat: useFlat,
			runMode: mode,
			branchOverride: signalBranch,
			signalBranch: signalBranch,
			indirAbs: indir.value,
			legacyOutBase: outdir.value,
		});
		try {
			fs.mkdirSync(finalOut, { recursive: true });
		} catch (err) {
			alert("Could not create output directory: " + (err.message || err));
			return;
		}

		if (project.isActive() && !useFlat) {
			lastDetectionRunRel = pipelineRuns.relFromRoleBase("detect", finalOut);
		} else {
			lastDetectionRunRel = "";
		}

		var plan = pipelineRun.preparePipelineRun("detect", mode, {
			outputRunRel: lastDetectionRunRel || "",
			sliceIds: sortedStems,
		});
		if (project.isActive() && !plan.toProcess.length) {
			alert("No slices to process (subset empty or all filtered).");
			return;
		}

		run.classList.add("disabled");
		back.classList.remove("btn-warning");
		back.classList.add("btn-danger");
		back.innerHTML = "Cancel";
		run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
		var msg = plan.summary || "";
		if (!useFlat && lastDetectionRunRel) {
			msg =
				(msg ? msg + " " : "") + "Run folder: " + lastDetectionRunRel;
		}
		loadmessage.innerHTML = msg || "Initializing...";

		ipc.send("runDetection", [
			indir.value,
			finalOut,
			c,
			t,
			m,
			mc,
			detectionMethod,
			a,
			e,
			plan.sliceListPath || "",
			!!(perSliceQc && perSliceQc.checked),
		]);
	}
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("killDetect", []);
		back.classList.add("btn-warning");
		back.classList.remove("btn-danger");
		back.innerHTML = "Back";
		run.innerHTML = "Run";
		run.classList.remove("disabled");
		loadmessage.innerHTML = "";
		loadbar.style.width = "0";
	}
});

ipc.on("detectResult", function (event, response) {
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	loadmessage.innerHTML = "";
	loadbar.style.width = "0";
	if (project.isActive() && lastDetectionRunRel) {
		pipelineRuns.setActiveRunRel("detect", lastDetectionRunRel);
		project.refreshProjectIndex().catch(function () {});
	}
});

ipc.on("detectError", function (event, response) {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
});

ipc.on("updateLoad", function (event, response) {
	var pct = Math.min(100, Math.max(0, Number(response[0]) || 0));
	loadbar.style.width = String(pct) + "%";
	loadmessage.innerHTML = response[1];
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("detect");
	datasetPicker = maxDatasetPicker.wireMaxDatasetPicker({
		storageKey: "masonjar.detect.maxDataset",
		indirInput: indir,
		sectionId: "detectDatasetSection",
		branchSelectId: "detectSignalBranch",
		datasetSelectId: "detectMaxDataset",
		defaultBranch: function () {
			return maxDatasets.defaultBranchForDetectMethod(detectionMethod);
		},
	});
	workspace.bindPathPicker(indir, "indir", "max");
	workspace.bindPathPicker(outdir, "outdir", "predictions");
	workspace.bindPathPicker(model, "model", null, true);
});
