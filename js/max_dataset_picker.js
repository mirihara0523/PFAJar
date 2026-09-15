"use strict";

var project = require("./project");
var maxDatasets = require("./max_datasets");

/**
 * Project-mode intensity/max dataset picker for detect and isolate regions.
 * @param {object} opts
 * @param {string} opts.storageKey
 * @param {HTMLElement} opts.indirInput
 * @param {string} opts.sectionId
 * @param {string} opts.branchSelectId
 * @param {string} opts.datasetSelectId
 * @param {function(): string} [opts.defaultBranch]
 */
function wireMaxDatasetPicker(opts) {
	var section = document.getElementById(opts.sectionId);
	var branchSelect = document.getElementById(opts.branchSelectId);
	var datasetSelect = document.getElementById(opts.datasetSelectId);
	if (!section || !opts.indirInput) {
		return;
	}

	function savedRel() {
		try {
			return sessionStorage.getItem(opts.storageKey) || "";
		} catch (_err) {
			return "";
		}
	}

	function persistRel(rel) {
		try {
			sessionStorage.setItem(opts.storageKey, rel || "");
		} catch (_err) {}
	}

	function refresh() {
		if (!project.isActive()) {
			section.classList.add("d-none");
			return;
		}
		// Never hide in project mode: a branch with only one dataset used to
		// set d-none on the whole section, which removes the branch dropdown.
		section.classList.remove("d-none");
		var root = project.getBundleRoot();
		var branch = branchSelect ? branchSelect.value : "";
		if (!branch && opts.defaultBranch) {
			branch = opts.defaultBranch();
		}
		if (branchSelect && branch) {
			branchSelect.value = branch;
		}
		var datasets = maxDatasets.listDatasetsForBranch(root, branch);
		if (datasetSelect) {
			datasetSelect.innerHTML = "";
			for (var i = 0; i < datasets.length; i++) {
				var d = datasets[i];
				var opt = document.createElement("option");
				opt.value = d.rel;
				opt.textContent = d.label;
				datasetSelect.appendChild(opt);
			}
		}
		var def = maxDatasets.defaultDatasetForBranch(root, branch, {
			preferKind: "max",
			savedRel: savedRel(),
		});
		if (def && datasetSelect) {
			datasetSelect.value = def.rel;
			opts.indirInput.value = def.abs;
			persistRel(def.rel);
		}
	}

	function onDatasetChange() {
		if (!project.isActive() || !datasetSelect) {
			return;
		}
		var root = project.getBundleRoot();
		var branch = branchSelect ? branchSelect.value : "";
		var rel = datasetSelect.value;
		persistRel(rel);
		var datasets = maxDatasets.listDatasetsForBranch(root, branch);
		for (var i = 0; i < datasets.length; i++) {
			if (datasets[i].rel === rel) {
				opts.indirInput.value = datasets[i].abs;
				break;
			}
		}
	}

	function populateBranches() {
		if (!branchSelect || !project.isActive()) {
			return;
		}
		var branches = maxDatasets.listSignalBranches(project.getBundleRoot());
		branchSelect.innerHTML = "";
		for (var i = 0; i < branches.length; i++) {
			var opt = document.createElement("option");
			opt.value = branches[i];
			opt.textContent = branches[i] || "(default)";
			branchSelect.appendChild(opt);
		}
		if (opts.defaultBranch) {
			var db = opts.defaultBranch();
			if (db) {
				branchSelect.value = db;
			}
		}
	}

	if (branchSelect) {
		branchSelect.addEventListener("change", function () {
			persistRel("");
			refresh();
		});
	}
	if (datasetSelect) {
		datasetSelect.addEventListener("change", onDatasetChange);
	}

	populateBranches();
	refresh();

	return { refresh: refresh };
}

module.exports = {
	wireMaxDatasetPicker: wireMaxDatasetPicker,
};
