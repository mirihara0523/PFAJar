var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var workspace = require("./workspace");
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRun = require("./pipeline_run");
var pipelineRuns = require("./pipeline_runs");
var alignIpc = require("./align_ipc");
var projectIndexBusy = require("./project_index_busy");
var run = document.getElementById("run");
var indir = document.getElementById("indir");
var outdir = document.getElementById("outdir");
var loadbar = document.getElementById("loadbar");
var loadmessage = document.getElementById("loadmessage");
var back = document.getElementById("back");
var autoMode = document.getElementById("auto");
var whole = document.getElementById("whole");
var half = document.getElementById("half");
var spacing = document.getElementById("spacing");
var legacy = document.getElementById("legacy");
var flatOutput = document.getElementById("flatOutput");
var alignmentMethod = "auto";
var useLegacy = "False";
var methods = document.querySelector("#methods");
var lastRunRel = "";
var alignNapariBannerRow = document.getElementById("alignNapariBannerRow");
var alignSessionRestoreBannerRow = document.getElementById(
	"alignSessionRestoreBannerRow",
);
var alignSetupPanel = document.getElementById("alignSetupPanel");
var alignWarpPanel = document.getElementById("alignWarpPanel");
var alignFinishPanel = document.getElementById("alignFinishPanel");
var alignWarpProgress = document.getElementById("alignWarpProgress");
var alignWarpMessage = document.getElementById("alignWarpMessage");
var alignWarpLog = document.getElementById("alignWarpLog");
var alignFinishAlert = document.getElementById("alignFinishAlert");
var alignFinishSummary = document.getElementById("alignFinishSummary");
var alignPhase = "setup"; // setup | napari | warp | finish

function setAlignmentMethod(mode) {
	if (mode === "hemi" || mode === "False" || mode === false) {
		methods.textContent = "Single hemisphere (all sections)";
		alignmentMethod = "False";
		return true;
	}
	if (mode === "whole" || mode === "True" || mode === true) {
		methods.textContent = "Both hemispheres (all sections)";
		alignmentMethod = "True";
		return true;
	}
	if (mode === "auto") {
		methods.textContent = "Automatic (recommended)";
		alignmentMethod = "auto";
		return true;
	}
	return false;
}

function restoreAlignmentMethodFromSession(dapiDir) {
	if (!dapiDir) {
		return false;
	}
	var sessionPath = path.join(dapiDir, "alignment_session.json");
	if (!fs.existsSync(sessionPath)) {
		return false;
	}
	try {
		var doc = JSON.parse(fs.readFileSync(sessionPath, "utf8"));
		if (!doc || !doc.layout_mode) {
			return false;
		}
		return setAlignmentMethod(String(doc.layout_mode));
	} catch (err) {
		return false;
	}
}

function setAlignSessionRestoreBannerVisible(visible) {
	if (!alignSessionRestoreBannerRow) {
		return;
	}
	if (visible) {
		alignSessionRestoreBannerRow.classList.remove("d-none");
	} else {
		alignSessionRestoreBannerRow.classList.add("d-none");
	}
}

function setAlignNapariBannerVisible(visible) {
	if (!alignNapariBannerRow) {
		return;
	}
	if (visible) {
		alignNapariBannerRow.classList.remove("d-none");
	} else {
		alignNapariBannerRow.classList.add("d-none");
	}
}

function appendWarpLog(line) {
	if (!alignWarpLog) {
		return;
	}
	var text = String(line || "").trim();
	if (!text) {
		return;
	}
	alignWarpLog.textContent +=
		(alignWarpLog.textContent ? "\n" : "") + text;
	alignWarpLog.scrollTop = alignWarpLog.scrollHeight;
}

function showAlignPhase(phase) {
	alignPhase = phase;
	if (alignSetupPanel) {
		alignSetupPanel.classList.toggle("d-none", phase === "warp" || phase === "finish");
	}
	if (alignWarpPanel) {
		alignWarpPanel.classList.toggle("d-none", phase !== "warp");
	}
	if (alignFinishPanel) {
		alignFinishPanel.classList.toggle("d-none", phase !== "finish");
	}
	if (phase !== "napari") {
		setAlignNapariBannerVisible(false);
	}
}

function resetAlignRunUi() {
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	if (loadbar) {
		loadbar.style.width = "0";
	}
	setAlignNapariBannerVisible(false);
	showAlignPhase("setup");
}

function fillFinishSummary(summary) {
	var warped = summary && summary.warped != null ? Number(summary.warped) : null;
	var failed = summary && summary.failed != null ? Number(summary.failed) : 0;
	var ok = !(summary && summary.ok === false);
	if (alignFinishAlert) {
		alignFinishAlert.className = ok
			? "alert alert-success"
			: "alert alert-warning";
		alignFinishAlert.textContent = ok
			? "Alignment finished."
			: "Alignment finished with errors.";
	}
	var parts = [];
	if (warped != null && Number.isFinite(warped)) {
		parts.push("Warped " + warped + " section" + (warped === 1 ? "" : "s") + ".");
	}
	if (failed > 0) {
		parts.push(failed + " section" + (failed === 1 ? "" : "s") + " failed.");
		var ids = (summary && summary.failed_slice_ids) || [];
		if (ids.length) {
			parts.push("Failed: " + ids.slice(0, 8).join(", ") + (ids.length > 8 ? "…" : ""));
		}
	}
	if (alignFinishSummary) {
		alignFinishSummary.textContent = parts.join(" ");
	}
}

pipelineRun.ensureRunModeUi("runModePanel", "align");

autoMode.addEventListener("click", function () {
	setAlignmentMethod("auto");
});

whole.addEventListener("click", function () {
	setAlignmentMethod("whole");
});

half.addEventListener("click", function () {
	setAlignmentMethod("hemi");
});

run.addEventListener("click", function () {
	if (indir && outdir && indir.value && outdir.value) {
		var a = spacing.value;
		try {
			a = Number(a);
		} catch (err) {
			alert("Spacing must be a integer!");
			return;
		}

		if (a % 1 != 0) {
			a = Math.round(a);
		}

		if (legacy.checked) {
			useLegacy = "True";
		} else {
			useLegacy = "False";
		}

		var mode = pipelineRun.getSelectedRunMode("align");
		var plan = pipelineRun.preparePipelineRun("align", mode);
		if (project.isActive() && !plan.toProcess.length) {
			alert("No slices to process (all outputs exist or subset is empty).");
			return;
		}

		var sortedStems = pipelineRuns.listImageSliceStems(indir.value);
		var slug = pipelineRuns.buildRunSlug("align", {
			sortedStems: sortedStems,
			spacing: a,
			whole: alignmentMethod,
			legacy: useLegacy,
			subsetCount: plan.toProcess.length,
		});
		var useFlat = flatOutput && flatOutput.checked;
		var finalOut = pipelineRuns.resolveStepOutputPath("align", {
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
		lastRunRel = useFlat ? "" : pipelineRuns.relFromRoleBase("align", finalOut);

		run.classList.add("disabled");
		back.classList.remove("btn-warning");
		back.classList.add("btn-danger");
		back.innerHTML = "Cancel";
		run.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
		var msg = plan.summary || "";
		if (!useFlat && lastRunRel) {
			msg = (msg ? msg + " " : "") + "Run folder: " + lastRunRel;
		}
		if (loadmessage) {
			loadmessage.innerHTML = msg;
		}
		showAlignPhase("napari");
		setAlignNapariBannerVisible(true);
		ipc.send("runAlign", [
			indir.value,
			finalOut,
			alignmentMethod,
			a,
			useLegacy,
			plan.sliceListPath || "",
			project.isActive() ? project.getBundleRoot() || "" : "",
		]);
	}
});

back.addEventListener("click", function (event) {
	if (back.classList.contains("btn-danger")) {
		event.preventDefault();
		ipc.send("saveAndExitAlign", []);
		return;
	}
	if (alignPhase === "warp" || back.classList.contains("disabled")) {
		event.preventDefault();
	}
});

ipc.on("alignWarping", function () {
	showAlignPhase("warp");
	if (alignWarpProgress) {
		alignWarpProgress.style.width = "0%";
	}
	if (alignWarpMessage) {
		alignWarpMessage.textContent = "Warping sections…";
	}
	if (alignWarpLog) {
		alignWarpLog.textContent = "";
	}
	appendWarpLog("Finish confirmed — warping sections to the atlas…");
	if (loadmessage) {
		loadmessage.innerHTML = "";
	}
	// Cancel no longer applies once warping has started.
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	back.classList.add("disabled");
});

ipc.on("alignResult", function (event, response) {
	back.classList.remove("disabled");
	if (response && response.cancelled) {
		resetAlignRunUi();
		if (loadmessage) {
			loadmessage.textContent =
				"Tuning saved. Run Align again and click Finish to warp sections.";
		}
		return;
	}
	if (loadmessage) {
		loadmessage.innerHTML = "";
	}
	if (loadbar) {
		loadbar.style.width = "0";
	}
	run.innerHTML = "Run";
	run.classList.remove("disabled");
	back.classList.add("btn-warning");
	back.classList.remove("btn-danger");
	back.innerHTML = "Back";
	setAlignNapariBannerVisible(false);

	if (project.isActive() && lastRunRel && alignIpc.shouldApplyAlignRunSideEffects(response)) {
		pipelineRuns.setActiveRunRel("align", lastRunRel);
		var alignLeaf = pipelineRuns.resolveActiveRunLeafAbs("slices");
		project.mergeAlignWarpReport(project.getBundleRoot(), alignLeaf);
		project.refreshProjectIndex().catch(function () {});
		project.notifyProcessingStateChanged();
	}

	fillFinishSummary((response && response.summary) || null);
	if (alignWarpProgress) {
		alignWarpProgress.style.width = "100%";
	}
	showAlignPhase("finish");
});

ipc.on("alignError", function (event, response) {
	resetAlignRunUi();
	back.classList.remove("disabled");
	if (response && response[0] && loadmessage) {
		loadmessage.textContent = String(response[0]);
	}
});

ipc.on("updateLoad", function (event, response) {
	var pct = response && response[0] != null ? Number(response[0]) : 0;
	var detail = String((response && response[1]) || "");
	if (loadbar && alignPhase !== "warp" && alignPhase !== "finish") {
		loadbar.style.width = String(pct) + "%";
	}
	if (loadmessage && alignPhase !== "warp" && alignPhase !== "finish") {
		loadmessage.innerHTML = detail;
	}
	if (
		detail.indexOf("align_session_discarded") >= 0 ||
		detail.indexOf("cleared a bad autosave") >= 0
	) {
		if (loadmessage) {
			loadmessage.textContent =
				"Refreshed alignment predictions (cleared a bad autosave from a prior run).";
		}
	}
	if (alignPhase === "warp") {
		if (alignWarpProgress && Number.isFinite(pct)) {
			alignWarpProgress.style.width = String(pct) + "%";
		}
		if (alignWarpMessage && detail) {
			alignWarpMessage.textContent = detail;
		}
		appendWarpLog(detail);
	}
});

projectIndexBusy.populatePage(
	function () {
		project.tryRestoreActiveProject();
		pipelineGate.assertPipelineAccess();
		workspace.applyPreset("align");
		workspace.bindPathPicker(indir, "indir", "dapi");
		workspace.bindPathPicker(outdir, "outdir", "slices");
		if (indir && indir.value && restoreAlignmentMethodFromSession(indir.value)) {
			setAlignSessionRestoreBannerVisible(true);
		}
	},
	// Align resolves its input/output paths from active_runs (project JSON) and
	// only enumerates slices at Run time, so don't block the page on a full
	// recursive NAS re-index. The refresh still runs in the background.
	{ waitForIndex: false },
);
