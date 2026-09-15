"use strict";

/**
 * Detect QC scout metadata helpers (processing.detect_qc on project JSON).
 */

var fs = require("fs");
var path = require("path");

function readDetectQc(projectData) {
	if (!projectData || !projectData.processing) {
		return null;
	}
	var qc = projectData.processing.detect_qc;
	if (!qc || typeof qc !== "object") {
		return null;
	}
	return qc;
}

function resolveScoutOutputAbs(bundleRoot, roles, qc) {
	if (!bundleRoot || !qc) {
		return "";
	}
	var pipelineRuns = require("./pipeline_runs");
	var r = roles || pipelineRuns.CANONICAL_ROLES;
	var predBase = pipelineRuns.resolveRoleBaseAbsForBundle(
		bundleRoot,
		r,
		"predictions",
	);
	if (!predBase) {
		return "";
	}
	var rel = String(qc.output_rel || "").replace(/\\/g, "/");
	if (!rel) {
		return "";
	}
	return path.join(predBase, rel.split("/").join(path.sep));
}

function suggestionIntensityMin(qc) {
	if (!qc) {
		return null;
	}
	var sug =
		qc.suggestions && qc.suggestions.intensity_min != null
			? Number(qc.suggestions.intensity_min)
			: null;
	if (sug == null || Number.isNaN(sug) || sug <= 0) {
		return null;
	}
	return sug;
}

function isSuggestionApplied(qc) {
	if (!qc) {
		return false;
	}
	var sug = suggestionIntensityMin(qc);
	if (sug == null) {
		return true; // nothing to apply
	}
	var applied = qc.applied_intensity_min;
	if (applied == null) {
		return false;
	}
	return Number(applied) === Number(sug);
}

function readSuggestionsFromSummaryFile(summaryAbs) {
	try {
		var raw = fs.readFileSync(summaryAbs, "utf8");
		var data = JSON.parse(raw);
		var analysis = data.analysis || {};
		var suggestions = analysis.suggestions || {};
		var intensity =
			suggestions.intensity_min != null
				? Number(suggestions.intensity_min)
				: analysis.intensity_threshold_estimate != null
					? Number(analysis.intensity_threshold_estimate)
					: null;
		return {
			intensity_min:
				intensity != null && !Number.isNaN(intensity) && intensity > 0
					? intensity
					: null,
			summary_lines: analysis.summary_lines || [],
		};
	} catch (_err) {
		return { intensity_min: null, summary_lines: [] };
	}
}

/**
 * Persist scout metadata after a successful qc-only run.
 * @returns {object} the written detect_qc record
 */
function recordDetectQcOnProject(bundleRoot, opts) {
	opts = opts || {};
	var project = require("./project");
	var pipelineRuns = require("./pipeline_runs");
	var batchPaths = null;
	try {
		batchPaths = require("../batch_paths");
	} catch (_e) {
		batchPaths = null;
	}

	var projectData;
	if (batchPaths && typeof batchPaths.loadProjectJson === "function") {
		projectData = batchPaths.loadProjectJson(bundleRoot);
	} else {
		var filePath = path.join(
			bundleRoot,
			fs.existsSync(path.join(bundleRoot, "project.masonjar"))
				? "project.masonjar"
				: "project.belljar",
		);
		projectData = JSON.parse(fs.readFileSync(filePath, "utf8"));
	}

	if (!projectData.processing) {
		projectData.processing = {};
	}
	var roles = projectData.roles || pipelineRuns.CANONICAL_ROLES;
	var predBase = pipelineRuns.resolveRoleBaseAbsForBundle(
		bundleRoot,
		roles,
		"predictions",
	);
	var maxBase = pipelineRuns.resolveRoleBaseAbsForBundle(
		bundleRoot,
		roles,
		"max",
	);
	var outputAbs = String(opts.outputAbs || "");
	var outputRel = predBase
		? path.relative(predBase, outputAbs).split(path.sep).join("/")
		: "";
	var summaryAbs = path.join(outputAbs, "detect_qc_summary.json");
	var summaryRel = predBase
		? path.relative(predBase, summaryAbs).split(path.sep).join("/")
		: "";
	var signalAbs = String(opts.signalDatasetAbs || "");
	var signalRel = maxBase && signalAbs
		? path.relative(maxBase, signalAbs).split(path.sep).join("/")
		: "";
	var sug = readSuggestionsFromSummaryFile(summaryAbs);

	var record = {
		output_rel: outputRel,
		summary_rel: summaryRel,
		signal_dataset_rel: signalRel,
		finished_at: new Date().toISOString(),
		suggestions: { intensity_min: sug.intensity_min },
		applied_intensity_min: null,
	};
	projectData.processing.detect_qc = record;

	if (batchPaths && typeof batchPaths.saveProjectJson === "function") {
		batchPaths.saveProjectJson(bundleRoot, projectData);
	} else {
		projectData.modified = new Date().toISOString();
		var outFile = path.join(
			bundleRoot,
			/\.masonjar$/i.test(
				fs
					.readdirSync(bundleRoot)
					.filter(function (n) {
						return /\.masonjar$/i.test(n);
					})[0] || "",
			)
				? fs
						.readdirSync(bundleRoot)
						.filter(function (n) {
							return /\.masonjar$/i.test(n);
						})[0]
				: "project.masonjar",
		);
		// Prefer named .masonjar if present
		var masonjars = fs
			.readdirSync(bundleRoot)
			.filter(function (n) {
				return /\.masonjar$/i.test(n);
			});
		if (masonjars.length === 1) {
			outFile = path.join(bundleRoot, masonjars[0]);
		} else if (fs.existsSync(path.join(bundleRoot, "project.masonjar"))) {
			outFile = path.join(bundleRoot, "project.masonjar");
		}
		fs.writeFileSync(outFile, JSON.stringify(projectData, null, 2), "utf8");
	}

	// Keep in-memory project in sync when this bundle is the active project
	try {
		if (
			project.isActive() &&
			project.getBundleRoot() &&
			path.resolve(project.getBundleRoot()) === path.resolve(bundleRoot)
		) {
			var live = project.getProject();
			if (live) {
				if (!live.processing) {
					live.processing = {};
				}
				live.processing.detect_qc = record;
			}
		}
	} catch (_syncErr) {
		/* ignore */
	}

	return record;
}

function markSuggestionApplied(projectData, intensityMin) {
	if (!projectData) {
		return;
	}
	if (!projectData.processing) {
		projectData.processing = {};
	}
	if (!projectData.processing.detect_qc) {
		projectData.processing.detect_qc = {};
	}
	projectData.processing.detect_qc.applied_intensity_min =
		intensityMin != null ? Number(intensityMin) : null;
}

function shouldPromptBeforeDetect(qc, formIntensityMin) {
	var sug = suggestionIntensityMin(qc);
	if (sug == null) {
		return false;
	}
	if (isSuggestionApplied(qc)) {
		return false;
	}
	var formVal = Number(formIntensityMin || 0);
	if (formVal > 0 && formVal === sug) {
		return false;
	}
	return true;
}

module.exports = {
	readDetectQc: readDetectQc,
	resolveScoutOutputAbs: resolveScoutOutputAbs,
	suggestionIntensityMin: suggestionIntensityMin,
	isSuggestionApplied: isSuggestionApplied,
	readSuggestionsFromSummaryFile: readSuggestionsFromSummaryFile,
	recordDetectQcOnProject: recordDetectQcOnProject,
	markSuggestionApplied: markSuggestionApplied,
	shouldPromptBeforeDetect: shouldPromptBeforeDetect,
};
