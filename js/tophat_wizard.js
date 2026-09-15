"use strict";

var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var preprocessWizard = require("./preprocess_wizard");
var projectIndexBusy = require("./project_index_busy");

function getToolParams() {
	var radiusEl = document.getElementById("tophatRadius");
	var gammaEl = document.getElementById("tophatGamma");
	return {
		radius: radiusEl ? Number(radiusEl.value) : 10,
		gamma: gammaEl ? Number(gammaEl.value) : 1.25,
	};
}

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	preprocessWizard.wirePreprocessWizard({
		stepId: "tophat",
		sourceStorageKey: "masonjar.tophat.sourceDataset",
		configFileName: "tophat_run_config.json",
		runIpc: "runTophat",
		previewIpc: "runTophatPreview",
		previewResultIpc: "tophatPreviewResult",
		resultIpc: "tophatResult",
		killRunIpc: "killTophat",
		killPreviewIpc: "killTophatPreview",
		getToolParams: getToolParams,
		buildSlugContext: function (base, params) {
			return {
				sortedStems: base.sortedStems,
				subsetCount: base.subsetCount,
				sourceKind: base.sourceKind,
				sourceRunRel: base.sourceRunRel,
				radius: params.radius,
				gamma: params.gamma,
			};
		},
	});
});
