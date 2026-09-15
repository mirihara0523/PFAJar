"use strict";

var fs = require("fs");
var path = require("path");

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
	var pipelineRuns = require("./pipeline_runs");
	var m = (modelPath || "").trim();
	if (m) {
		var base = path.basename(m).replace(/\.pt$/i, "");
		return pipelineRuns.sanitizeSlugPart(base) || "custom";
	}
	return detectionMethod === "nuclei" ? "nuclei" : "somata";
}

function checkNumber(value, message) {
	var str = value.toString();
	if (!str.match(/^-?\d*\.?\d*$/)) {
		alert(message);
		return false;
	}
	return true;
}

function parseDetectParams(form) {
	var c = 0.5;
	var e = 0.2;
	var a = 200;
	var t = 640;
	var intensityMin = 0;
	var m = "";
	var mc = false;

	if (form.confidence && form.confidence.value && form.confidence.value < 1 && form.confidence.value > 0) {
		c = checkNumber(
			form.confidence.value,
			"Confidence should be a float between 0-1, using default.",
		)
			? Number(form.confidence.value)
			: 0.5;
	}

	if (
		form.eccentricity &&
		form.eccentricity.value &&
		form.eccentricity.value < 1 &&
		form.eccentricity.value > 0
	) {
		e = checkNumber(
			form.eccentricity.value,
			"Eccentricity should be a float between 0-1, using default.",
		)
			? Number(form.eccentricity.value)
			: 0.2;
	}

	if (form.area && form.area.value && form.area.value > 0) {
		a = checkNumber(form.area.value, "Area should be an integer, using default.")
			? Number(form.area.value)
			: 200;
	}

	if (form.tile && form.tile.value && form.tile.value > 0) {
		t = checkNumber(form.tile.value, "Tile should be an integer, using default.")
			? Number(form.tile.value)
			: 640;
	}

	if (form.intensityMin && form.intensityMin.value !== "" && Number(form.intensityMin.value) > 0) {
		intensityMin = checkNumber(
			form.intensityMin.value,
			"Intensity cutoff should be a number 0-255.",
		)
			? Number(form.intensityMin.value)
			: 0;
	}

	if (form.model && form.model.value) {
		m = form.model.value;
	}
	if (form.multichannel && form.multichannel.checked) {
		mc = true;
	}

	return {
		confidence: c,
		eccentricity: e,
		area: a,
		tile: t,
		intensityMin: intensityMin,
		model: m,
		multichannel: mc,
	};
}

function buildRunPayload(options) {
	var project = require("./project");
	var pipelineRun = require("./pipeline_run");
	var pipelineRuns = require("./pipeline_runs");

	var form = options.form;
	var detectionMethod = options.detectionMethod;
	var mode = pipelineRun.getSelectedRunMode("detect");
	var params = parseDetectParams(form);
	var sortedStems = listInputSliceStems(form.indir.value);
	if (project.isActive() && !sortedStems.length) {
		return { error: "No slices to process (input folder has no images)." };
	}

	var modelBranch = modelBranchForSlug(detectionMethod, params.model);
	var inputDatasetRel = "";
	if (project.isActive() && form.indir.value) {
		inputDatasetRel = pipelineRuns.relFromRoleBase("max", form.indir.value) || "";
	}
	// Folder under 05_predictions is the intensity/signal branch (starters, somata, …),
	// not the detection model name — mirrors 03_max/{branch}/….
	var signalBranch =
		pipelineRuns.inferSignalBranchForMaxFamily(
			inputDatasetRel,
			form.indir.value,
		) || modelBranch;
	// Resolve the intended output leaf *before* merge planning so we do not treat
	// an active predictions run from another intensity branch as "already done".
	var slug = pipelineRuns.buildDetectRunSlug({
		confidence: params.confidence,
		tile: params.tile,
		area: params.area,
		eccentricity: params.eccentricity,
		intensityMin: params.intensityMin,
		sortedStems: sortedStems,
		subsetCount: sortedStems.length,
		inputDatasetRel: inputDatasetRel,
		modelBranch: modelBranch,
	});
	var useFlat = form.flatOutput && form.flatOutput.checked;
	var finalOut = pipelineRuns.resolveStepOutputPath("detect", {
		slug: slug,
		flat: useFlat,
		runMode: mode,
		branchOverride: signalBranch,
		signalBranch: signalBranch,
		indirAbs: form.indir.value,
		legacyOutBase: form.outdir.value,
	});
	try {
		fs.mkdirSync(finalOut, { recursive: true });
	} catch (err) {
		return { error: "Could not create output directory: " + (err.message || err) };
	}

	var lastDetectionRunRel = "";
	if (project.isActive() && !useFlat) {
		lastDetectionRunRel = pipelineRuns.relFromRoleBase("detect", finalOut);
	}

	var plan = pipelineRun.preparePipelineRun("detect", mode, {
		outputRunRel: lastDetectionRunRel || "",
		sliceIds: sortedStems,
	});
	if (project.isActive() && !plan.toProcess.length) {
		return { error: "No slices to process (subset empty or all filtered)." };
	}

	return {
		params: params,
		plan: plan,
		slug: slug,
		finalOut: finalOut,
		lastDetectionRunRel: lastDetectionRunRel,
		useFlat: useFlat,
		perSliceQc: !!(form.perSliceQc && form.perSliceQc.checked),
		detectionMethod: detectionMethod,
		ipcArgs: [
			form.indir.value,
			finalOut,
			params.confidence,
			params.tile,
			params.model,
			params.multichannel,
			detectionMethod,
			params.area,
			params.eccentricity,
			plan.sliceListPath || "",
			!!(form.perSliceQc && form.perSliceQc.checked),
			params.intensityMin,
		],
	};
}

module.exports = {
	sliceStemFromImageBasename: sliceStemFromImageBasename,
	listInputSliceStems: listInputSliceStems,
	modelBranchForSlug: modelBranchForSlug,
	parseDetectParams: parseDetectParams,
	buildRunPayload: buildRunPayload,
};
