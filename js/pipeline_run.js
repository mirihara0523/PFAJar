"use strict";

var path = require("path");
var project = require("./project");
var fileIndex = require("./file_index");
var pipelineRuns = require("./pipeline_runs");

/**
 * Prepare a project-mode pipeline run: plan slices, persist list, return IPC extras.
 * Legacy workspace: returns empty sliceListPath (unchanged IPC).
 *
 * @param {string} stepId
 * @param {string} [runMode]
 * @param {object} [options]
 * @param {string} [options.outputRunRel] Plan merge/skip against this output run
 *   (relative to the step output role), not the active run. Required for detect
 *   when the intensity branch changes so merge does not see the prior channel's PKLs.
 * @param {string[]} [options.sliceIds] Candidate slice ids (default: project match set).
 */
function preparePipelineRun(stepId, runMode, options) {
	options = options || {};
	if (!project.isActive()) {
		return { sliceListPath: "", summary: "", toProcess: [], skipped: [] };
	}
	var bundleRoot = project.getBundleRoot();
	var proj = project.getProject();
	var roles = proj.roles || project.CANONICAL_ROLES;
	var index = project.readProjectFileIndex();
	if (!index) {
		return { sliceListPath: "", summary: "", toProcess: [], skipped: [] };
	}
	var activeRuns =
		(proj.processing && proj.processing.active_runs) ||
		pipelineRuns.migrateActiveRuns(proj.processing);
	if (options.outputRunRel) {
		var outCfg = fileIndex.STEP_OUTPUT[stepId];
		if (outCfg && outCfg.role) {
			activeRuns = Object.assign({}, activeRuns);
			activeRuns[outCfg.role] = options.outputRunRel;
		}
	}
	var report = fileIndex.computeMatchReport(index, fileIndex.INPUT_MATCH_ROLES, {
		activeRuns: activeRuns,
		bundleRoot: bundleRoot,
		roles: roles,
	});
	var candidateIds =
		options.sliceIds && options.sliceIds.length
			? options.sliceIds.slice()
			: fileIndex.getProcessingSliceIds(bundleRoot, proj, index, report, {
					stepId: stepId,
				});
	var modes = (proj.processing && proj.processing.run_modes) || {};
	var mode = runMode || modes[stepId] || "merge";
	var plan = fileIndex.planRun(bundleRoot, stepId, {
		mode: mode,
		sliceIds: candidateIds,
		roles: roles,
		activeRuns: activeRuns,
	});
	if (modes[stepId] !== mode) {
		if (!proj.processing) {
			proj.processing = {};
		}
		if (!proj.processing.run_modes) {
			proj.processing.run_modes = {};
		}
		proj.processing.run_modes[stepId] = mode;
		project.saveProjectJson();
	}
	var metaPath = project.metaDirPath(bundleRoot);
	var sliceListPath = "";
	var countExtra = "";

	if (stepId === "count") {
		var predLeaf = pipelineRuns.resolveActiveRunLeafAbs("predictions");
		var slicesLeaf = pipelineRuns.resolveActiveRunLeafAbs("slices");
		var failedAlign = project.getFailedSliceIds("align");
		var failedAlignSet = {};
		for (var fa = 0; fa < failedAlign.length; fa++) {
			failedAlignSet[failedAlign[fa]] = true;
		}
		var before = plan.toProcess.length;
		plan.toProcess = plan.toProcess.filter(function (sid) {
			return (
				!failedAlignSet[sid] &&
				fileIndex.predictionPklExistsForSlice(predLeaf, sid) &&
				fileIndex.outputExistsForSlice(bundleRoot, "align", sid, roles, activeRuns)
			);
		});
		var dropped = before - plan.toProcess.length;
		if (dropped) {
			countExtra =
				" " +
				dropped +
				" slice(s) skipped (no matching prediction PKL in the selected predictions folder).";
		}
		if (!predLeaf || !slicesLeaf) {
			countExtra += " Choose predictions and slices on the Count page.";
		}
	}

	if (plan.toProcess.length) {
		sliceListPath = fileIndex.writeRunSliceList(metaPath, plan.toProcess);
	}
	var summary =
		"Processing " +
		plan.toProcess.length +
		" slice(s)" +
		(plan.skipped.length
			? "; skipping " + plan.skipped.length + " with existing outputs"
			: "") +
		"." +
		countExtra;
	return {
		sliceListPath: sliceListPath,
		summary: summary,
		toProcess: plan.toProcess,
		skipped: plan.skipped,
		mode: mode,
	};
}

function ensureRunModeUi(containerId, stepId, onModeChange) {
	if (!project.isActive()) {
		return null;
	}
	var container = document.getElementById(containerId);
	if (!container) {
		return null;
	}
	container.classList.remove("d-none");
	var proj = project.getProject();
	var modes = (proj.processing && proj.processing.run_modes) || {};
	var current = modes[stepId] || "merge";
	var fieldset = document.createElement("fieldset");
	fieldset.className = "text-start border rounded p-3 mb-3";
	var html =
		'<legend class="float-none w-auto px-2 fs-6">Run mode (project)</legend>';
	var options = [
		{ id: "overwrite", label: "Overwrite all" },
		{ id: "skip", label: "Skip existing" },
		{ id: "merge", label: "Merge (process missing only)" },
	];
	for (var i = 0; i < options.length; i++) {
		var opt = options[i];
		var checked = current === opt.id ? " checked" : "";
		html +=
			'<div class="form-check">' +
			'<input class="form-check-input run-mode-radio" type="radio" name="runMode_' +
			stepId +
			'" id="runMode_' +
			stepId +
			"_" +
			opt.id +
			'" value="' +
			opt.id +
			'"' +
			checked +
			"/>" +
			'<label class="form-check-label" for="runMode_' +
			stepId +
			"_" +
			opt.id +
			'">' +
			opt.label +
			"</label></div>";
	}
	fieldset.innerHTML = html;
	container.innerHTML = "";
	container.appendChild(fieldset);
	var radios = fieldset.querySelectorAll(".run-mode-radio");
	for (var r = 0; r < radios.length; r++) {
		radios[r].addEventListener("change", function (ev) {
			var mode = ev.target.value;
			var p = project.getProject();
			if (!p.processing) {
				p.processing = {};
			}
			if (!p.processing.run_modes) {
				p.processing.run_modes = {};
			}
			p.processing.run_modes[stepId] = mode;
			project.saveProjectJson();
			if (typeof onModeChange === "function") {
				onModeChange(mode);
			}
		});
	}
	return fieldset;
}

function getSelectedRunMode(stepId) {
	var selected = document.querySelector(
		'input[name="runMode_' + stepId + '"]:checked',
	);
	return selected ? selected.value : "merge";
}

/**
 * Project-mode Viewer/Editor: slice list from matched DAPI + slices IDs (no skip/merge).
 */
function prepareAdjustSession() {
	if (!project.isActive()) {
		return { sliceListPath: "", summary: "", sliceIds: [] };
	}
	var bundleRoot = project.getBundleRoot();
	var proj = project.getProject();
	var index = project.readProjectFileIndex();
	if (!index) {
		return { sliceListPath: "", summary: "", sliceIds: [] };
	}
	var report = fileIndex.computeMatchReport(index, ["dapi", "slices"]);
	var ids = fileIndex.getProcessingSliceIds(bundleRoot, proj, index, report, {
		stepId: "adjust",
	});
	var sliceListPath = "";
	if (ids.length) {
		sliceListPath = fileIndex.writeRunSliceList(
			project.metaDirPath(bundleRoot),
			ids,
		);
	}
	var summary = ids.length
		? "Viewing " + ids.length + " matched slice(s) (DAPI + annotations)."
		: "No matched DAPI/annotation pairs in project index.";
	return { sliceListPath: sliceListPath, summary: summary, sliceIds: ids };
}

module.exports = {
	preparePipelineRun: preparePipelineRun,
	prepareAdjustSession: prepareAdjustSession,
	ensureRunModeUi: ensureRunModeUi,
	getSelectedRunMode: getSelectedRunMode,
};
