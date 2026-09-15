"use strict";

var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var preprocessWizard = require("./preprocess_wizard");
var projectIndexBusy = require("./project_index_busy");

function getToolParams() {
	var radiusEl = document.getElementById("sharpenRadius");
	var amountEl = document.getElementById("sharpenAmount");
	var eqEl = document.getElementById("sharpenEqualize");
	return {
		radius: radiusEl ? Number(radiusEl.value) : 3,
		amount: amountEl ? Number(amountEl.value) : 2,
		equalize: eqEl ? eqEl.checked : true,
	};
}

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	preprocessWizard.wirePreprocessWizard({
		stepId: "sharpen",
		sourceStorageKey: "masonjar.sharpen.sourceDataset",
		configFileName: "sharpen_run_config.json",
		runIpc: "runSharpen",
		previewIpc: "runSharpenPreview",
		previewResultIpc: "sharpenPreviewResult",
		resultIpc: "sharpenResult",
		killRunIpc: "killSharpen",
		killPreviewIpc: "killSharpenPreview",
		getToolParams: getToolParams,
		buildSlugContext: function (base, params) {
			return {
				sortedStems: base.sortedStems,
				subsetCount: base.subsetCount,
				sourceKind: base.sourceKind,
				sourceRunRel: base.sourceRunRel,
				radius: params.radius,
				amount: params.amount,
				equalize: params.equalize,
			};
		},
	});
});
