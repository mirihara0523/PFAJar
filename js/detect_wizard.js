"use strict";

var fs = require("fs");
var path = require("path");
var url = require("url");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var pipelineRuns = require("./pipeline_runs");
var maxDatasets = require("./max_datasets");
var maxDatasetPicker = require("./max_dataset_picker");
var detectCommon = require("./detect_common");
var detectQcScout = require("./detect_qc_scout");
var projectIndexBusy = require("./project_index_busy");
var dialogs = require("./dialogs");

var LAST_RUN_KEY = "masonjar.detect.lastRun";
var PER_SLICE_QC_KEY = "masonjar.detect.perSliceQc";
var LOG_MAX = 1500;

var detectionMethod = "somata";
var running = false;
var lastDetectionRunRel = "";
var lastAnalysis = null;
var datasetPicker = null;

function qs(id) {
	return document.getElementById(id);
}

function formRefs() {
	return {
		indir: qs("indir"),
		outdir: qs("outdir"),
		tile: qs("tile"),
		confidence: qs("confidence"),
		area: qs("area"),
		eccentricity: qs("eccentricity"),
		intensityMin: qs("intensityMin"),
		model: qs("model"),
		multichannel: qs("multichannel"),
		flatOutput: qs("flatOutput"),
		perSliceQc: qs("perSliceQc"),
	};
}

function fileUrlForPath(absPath) {
	if (!absPath) {
		return "";
	}
	try {
		return url.pathToFileURL(path.resolve(absPath)).href;
	} catch (_err) {
		return "file://" + String(absPath).replace(/\\/g, "/");
	}
}

function setStep(n) {
	var panels = [qs("step1"), qs("step2"), qs("step3")];
	for (var i = 0; i < panels.length; i++) {
		if (panels[i]) {
			panels[i].classList.toggle("d-none", i + 1 !== n);
		}
	}
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	for (var p = 0; p < pills.length; p++) {
		var pillStep = Number(pills[p].getAttribute("data-step"));
		pills[p].classList.remove("active", "disabled");
		if (pillStep === n) {
			pills[p].classList.add("active");
		} else {
			pills[p].classList.add("disabled");
		}
	}
}

function appendLog(line) {
	var logEl = qs("wizardLog");
	if (!logEl) {
		return;
	}
	var text = String(line || "") + "\n";
	logEl.textContent = (logEl.textContent + text).slice(-LOG_MAX);
	logEl.scrollTop = logEl.scrollHeight;
}

function stashLastRun(payload, runRel) {
	try {
		sessionStorage.setItem(
			LAST_RUN_KEY,
			JSON.stringify({
				outputAbs: payload.finalOut,
				slug: payload.slug,
				params: payload.params,
				runRel: runRel,
			}),
		);
	} catch (_err) {
		/* ignore */
	}
}

function loadLastRun() {
	try {
		var raw = sessionStorage.getItem(LAST_RUN_KEY);
		return raw ? JSON.parse(raw) : null;
	} catch (_err) {
		return null;
	}
}

function renderAnalysisSummary(summaryJson) {
	var container = qs("qcAnalysisSummary");
	var applyBtn = qs("applyIntensityCutoff");
	if (!container) {
		return;
	}
	container.innerHTML = "";
	lastAnalysis = (summaryJson && summaryJson.analysis) || null;

	if (!applyBtn) {
		applyBtn = null;
	}

	if (!lastAnalysis) {
		container.innerHTML =
			'<p class="text-muted small">QC summary not available for this run.</p>';
		if (applyBtn) {
			applyBtn.classList.add("d-none");
		}
		return;
	}

	var lines = lastAnalysis.summary_lines || [];
	if (lines.length) {
		var ul = document.createElement("ul");
		ul.className = "small";
		for (var i = 0; i < lines.length; i++) {
			var li = document.createElement("li");
			li.textContent = lines[i];
			ul.appendChild(li);
		}
		container.appendChild(ul);
	}

	var intensitySug =
		lastAnalysis.suggestions && lastAnalysis.suggestions.intensity_min != null
			? lastAnalysis.suggestions.intensity_min
			: lastAnalysis.intensity_threshold_estimate;
	var currentIntensity =
		lastAnalysis.current && lastAnalysis.current.intensity_min != null
			? lastAnalysis.current.intensity_min
			: 0;

	if (intensitySug != null && intensitySug > 0) {
		var callout = document.createElement("div");
		callout.className = "alert alert-info py-2 px-3 small mb-0";
		var curText =
			currentIntensity > 0
				? " (current: " + String(currentIntensity) + ")"
				: "";
		callout.textContent =
			"Suggested intensity cutoff: " + String(intensitySug) + curText;
		container.appendChild(callout);
		if (applyBtn) {
			applyBtn.classList.remove("d-none");
		}
	} else if (!lines.length) {
		var p = document.createElement("p");
		p.className = "text-muted small";
		p.textContent = "Not enough data in this run to recommend an intensity cutoff.";
		container.appendChild(p);
		if (applyBtn) {
			applyBtn.classList.add("d-none");
		}
	} else if (applyBtn) {
		applyBtn.classList.add("d-none");
	}
}

function renderQcGallery(outputAbs) {
	var gallery = qs("qcGallery");
	if (!gallery || !outputAbs) {
		return;
	}
	gallery.innerHTML = "";
	var files = [
		"detect_qc_confidence.png",
		"detect_qc_area_px2.png",
		"detect_qc_eccentricity.png",
	];
	for (var i = 0; i < files.length; i++) {
		var abs = path.join(outputAbs, files[i]);
		if (!fs.existsSync(abs)) {
			continue;
		}
		var wrap = document.createElement("div");
		wrap.className = "mb-3";
		var cap = document.createElement("div");
		cap.className = "small text-muted mb-1";
		cap.textContent = files[i];
		var img = document.createElement("img");
		img.alt = files[i];
		img.src = fileUrlForPath(abs) + "?t=" + Date.now();
		wrap.appendChild(cap);
		wrap.appendChild(img);
		gallery.appendChild(wrap);
	}
}

function showSummaryStep(success, message) {
	setStep(3);
	var alertEl = qs("summaryAlert");
	if (alertEl) {
		alertEl.className = "alert text-start " + (success ? "alert-success" : "alert-danger");
		alertEl.textContent = message || (success ? "Cell detection finished." : "Cell detection failed.");
	}

	var lastRun = loadLastRun();
	var outputAbs = lastRun && lastRun.outputAbs;
	var summaryJson = null;
	if (outputAbs) {
		var summaryPath = path.join(outputAbs, "detect_qc_summary.json");
		if (fs.existsSync(summaryPath)) {
			try {
				summaryJson = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
			} catch (_err) {
				summaryJson = null;
			}
		}
		renderQcGallery(outputAbs);
		renderAnalysisSummary(summaryJson);
	}
}

function applyIntensityCutoff() {
	if (!lastAnalysis) {
		return;
	}
	var intensitySug =
		lastAnalysis.suggestions && lastAnalysis.suggestions.intensity_min != null
			? lastAnalysis.suggestions.intensity_min
			: lastAnalysis.intensity_threshold_estimate;
	if (intensitySug == null || intensitySug <= 0) {
		return;
	}
	applyScoutIntensityValue(intensitySug);
}

function applyScoutIntensityValue(intensitySug) {
	var form = formRefs();
	if (form.intensityMin) {
		form.intensityMin.value = String(intensitySug);
	}
	persistAppliedScoutIntensity(intensitySug);
	var collapse = document.getElementById("collapseAdvanced");
	var advance = qs("advance");
	if (collapse && !collapse.classList.contains("show")) {
		if (typeof bootstrap !== "undefined" && bootstrap.Collapse) {
			bootstrap.Collapse.getOrCreateInstance(collapse, { toggle: false }).show();
		} else {
			collapse.classList.add("show");
		}
		if (advance) {
			advance.setAttribute("aria-expanded", "true");
		}
	}
	setStep(1);
	renderDetectScoutBanner();
}

function persistAppliedScoutIntensity(intensitySug) {
	if (!project.isActive()) {
		return;
	}
	try {
		var proj = project.getProject();
		detectQcScout.markSuggestionApplied(proj, intensitySug);
		project.saveProjectJson();
	} catch (_err) {
		/* ignore */
	}
}

function renderDetectScoutBanner() {
	var banner = qs("detectScoutBanner");
	if (!banner) {
		return;
	}
	if (!project.isActive()) {
		banner.classList.add("d-none");
		banner.innerHTML = "";
		return;
	}
	var qc = detectQcScout.readDetectQc(project.getProject());
	var sug = detectQcScout.suggestionIntensityMin(qc);
	if (!qc || !qc.output_rel) {
		banner.classList.add("d-none");
		banner.innerHTML = "";
		return;
	}
	var abs = detectQcScout.resolveScoutOutputAbs(
		project.getBundleRoot(),
		project.getProject().roles,
		qc,
	);
	var lines =
		"<strong>Detect QC scout available</strong> — full QC graphs and summary were gathered for this project.";
	if (sug != null) {
		lines +=
			" Suggested intensity cutoff: <strong>" +
			String(sug) +
			"</strong>.";
	}
	if (detectQcScout.isSuggestionApplied(qc)) {
		lines += " (threshold already applied.)";
	}
	banner.innerHTML =
		'<div class="d-flex flex-wrap align-items-center gap-2">' +
		'<div class="flex-grow-1">' +
		lines +
		"</div>" +
		(sug != null && !detectQcScout.isSuggestionApplied(qc)
			? '<button type="button" class="btn btn-sm btn-primary" id="applyScoutThreshold">Apply threshold</button>'
			: "") +
		(abs
			? '<button type="button" class="btn btn-sm btn-outline-secondary" id="browseScoutQc">Browse QC</button>'
			: "") +
		"</div>";
	banner.classList.remove("d-none");
	var applyBtn = qs("applyScoutThreshold");
	if (applyBtn) {
		applyBtn.addEventListener("click", function () {
			applyScoutIntensityValue(sug);
		});
	}
	var browseBtn = qs("browseScoutQc");
	if (browseBtn && abs) {
		browseBtn.addEventListener("click", function () {
			ipc.send("openPathInShell", abs);
		});
	}
}

function startDetection() {
	var form = formRefs();
	if (!form.indir || !form.outdir || !form.indir.value || !form.outdir.value) {
		alert("Input and output paths are required.");
		return;
	}

	var qc = project.isActive()
		? detectQcScout.readDetectQc(project.getProject())
		: null;
	var formIntensity = form.intensityMin ? form.intensityMin.value : 0;
	if (detectQcScout.shouldPromptBeforeDetect(qc, formIntensity)) {
		var sug = detectQcScout.suggestionIntensityMin(qc);
		dialogs
			.confirmThreeWay({
				title: "Detect QC scout",
				message:
					"Would you like to apply the intensity threshold discovered in the scouting run?" +
					(sug != null ? " (suggested: " + String(sug) + ")" : ""),
				buttons: [
					{ id: "apply", label: "Apply and run", primary: true },
					{ id: "skip", label: "Skip and run" },
					{ id: "cancel", label: "Cancel" },
				],
			})
			.then(function (choice) {
				if (choice === "cancel" || choice == null) {
					return;
				}
				if (choice === "apply" && sug != null) {
					if (form.intensityMin) {
						form.intensityMin.value = String(sug);
					}
					persistAppliedScoutIntensity(sug);
					renderDetectScoutBanner();
				}
				launchDetectionRun();
			})
			.catch(function () {
				/* dialog cancelled */
			});
		return;
	}

	launchDetectionRun();
}

function launchDetectionRun() {
	var form = formRefs();
	var payload = detectCommon.buildRunPayload({
		form: form,
		detectionMethod: detectionMethod,
	});
	if (payload.error) {
		alert(payload.error);
		return;
	}

	lastDetectionRunRel = payload.lastDetectionRunRel;
	stashLastRun(payload, lastDetectionRunRel);
	running = true;
	setStep(2);

	var prog = qs("processProgress");
	var msg = qs("processMessage");
	if (prog) {
		prog.style.width = "0%";
		prog.style.minWidth = "";
		prog.textContent = "";
		prog.setAttribute("aria-valuenow", "0");
	}
	if (msg) {
		var planMsg = payload.plan.summary || "";
		if (!payload.useFlat && lastDetectionRunRel) {
			planMsg =
				(planMsg ? planMsg + " " : "") + "Run folder: " + lastDetectionRunRel;
		}
		msg.textContent = planMsg || "Launching cell detection…";
	}
	var logEl = qs("wizardLog");
	if (logEl) {
		logEl.textContent = "";
	}

	ipc.send("runDetection", payload.ipcArgs);
}

pipelineRun.ensureRunModeUi("runModePanel", "detect");

var somata = qs("somata");
var nuclei = qs("nuclei");
var methods = qs("methods");
var advance = qs("advance");
var arrow = qs("arrow");
var perSliceQc = qs("perSliceQc");

if (somata) {
	somata.addEventListener("click", function () {
		if (methods) {
			methods.textContent = "Somata";
		}
		detectionMethod = "somata";
		if (datasetPicker) {
			datasetPicker.refresh();
		}
	});
}

if (nuclei) {
	nuclei.addEventListener("click", function () {
		if (methods) {
			methods.textContent = "Nuclei";
		}
		detectionMethod = "nuclei";
		if (datasetPicker) {
			datasetPicker.refresh();
		}
	});
}

if (advance && arrow) {
	advance.addEventListener("click", function () {
		arrow.classList.toggle("down");
	});
}

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

var step1Next = qs("step1Next");
if (step1Next) {
	step1Next.addEventListener("click", startDetection);
}

var step2Cancel = qs("step2Cancel");
if (step2Cancel) {
	step2Cancel.addEventListener("click", function () {
		if (running) {
			ipc.send("killDetect", []);
		}
	});
}

var applyBtn = qs("applyIntensityCutoff");
if (applyBtn) {
	applyBtn.addEventListener("click", applyIntensityCutoff);
}

function setProcessProgress(pct, text) {
	var n = Math.min(100, Math.max(0, Number(pct) || 0));
	var prog = qs("processProgress");
	var msg = qs("processMessage");
	if (prog) {
		prog.style.width = String(n) + "%";
		prog.style.minWidth = n > 0 && n < 12 ? "2.5rem" : "";
		prog.textContent = n > 0 ? String(n) + "%" : "";
		prog.setAttribute("aria-valuenow", String(n));
	}
	if (msg && text) {
		msg.textContent = text;
		appendLog(text);
	}
}

ipc.on("detectResult", function () {
	running = false;
	setProcessProgress(100, "Done!");
	if (project.isActive() && lastDetectionRunRel) {
		pipelineRuns.setActiveRunRel("detect", lastDetectionRunRel);
		project.refreshProjectIndex().catch(function () {});
	}
	showSummaryStep(true, "Cell detection finished.");
});

ipc.on("detectError", function () {
	running = false;
	showSummaryStep(false, "Cell detection failed. Check the Application log for details.");
});

ipc.on("updateLoad", function (_event, response) {
	if (!running) {
		return;
	}
	setProcessProgress(response[0], response[1]);
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	workspace.applyPreset("detect");
	renderDetectScoutBanner();
	datasetPicker = maxDatasetPicker.wireMaxDatasetPicker({
		storageKey: "masonjar.detect.maxDataset",
		indirInput: qs("indir"),
		sectionId: "detectDatasetSection",
		branchSelectId: "detectSignalBranch",
		datasetSelectId: "detectMaxDataset",
		defaultBranch: function () {
			return maxDatasets.defaultBranchForDetectMethod(detectionMethod);
		},
	});
	workspace.bindPathPicker(qs("indir"), "indir", "max");
	workspace.bindPathPicker(qs("outdir"), "outdir", "predictions");
	workspace.bindPathPicker(qs("model"), "model", null, true);
	setStep(1);
});
