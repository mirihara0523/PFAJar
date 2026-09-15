"use strict";

var project = require("./project");
var pipelineRuns = require("./pipeline_runs");
var ipc = require("electron").ipcRenderer;

/**
 * Trash button for a run <select>: deletes the selected run folder after confirm.
 * @param {HTMLSelectElement} selectEl
 * @param {string} role output role (predictions, slices, pkls, …)
 * @param {{ onDeleted?: function(): void }} [options]
 * @returns {HTMLButtonElement}
 */
function attachRunDeleteButton(selectEl, role, options) {
	options = options || {};
	var btn = document.createElement("button");
	btn.type = "button";
	btn.className = "btn btn-sm btn-outline-danger ms-1";
	btn.title = "Delete selected task folder";
	btn.setAttribute("aria-label", "Delete selected task");
	btn.innerHTML = '<i class="fas fa-trash-alt" aria-hidden="true"></i>';

	function syncDisabled() {
		var rel = selectEl ? String(selectEl.value || "") : "";
		btn.disabled = !rel;
	}

	if (selectEl) {
		selectEl.addEventListener("change", syncDisabled);
	}
	syncDisabled();

	btn.addEventListener("click", function () {
		if (!project.isActive() || !selectEl) {
			return;
		}
		var rel = String(selectEl.value || "").trim();
		if (!rel) {
			alert("Cannot delete the flat role root. Pick a nested task folder first.");
			return;
		}
		var bundleRoot = project.getBundleRoot();
		var proj = project.getProject();
		var roles = (proj && proj.roles) || project.CANONICAL_ROLES;
		var targets = pipelineRuns.collectRunDeleteTargets(bundleRoot, roles, role, rel);
		if (!targets.length) {
			alert("No task folder found on disk for:\n" + rel);
			return;
		}
		var msg = pipelineRuns.buildRunDeleteConfirmMessage(role, rel, targets);
		if (!window.confirm(msg)) {
			return;
		}
		btn.disabled = true;
		var result = pipelineRuns.removeRunForRole(role, rel);
		if (!result.ok) {
			alert("Delete failed: " + (result.error || "unknown error"));
			syncDisabled();
			return;
		}
		project
			.refreshProjectIndex()
			.then(function () {
				if (typeof options.onDeleted === "function") {
					options.onDeleted();
				}
			})
			.catch(function (err) {
				alert(
					"Run deleted but index refresh failed: " +
						String(err.message || err),
				);
				if (typeof options.onDeleted === "function") {
					options.onDeleted();
				}
			})
			.finally(function () {
				syncDisabled();
			});
	});

	return btn;
}

/**
 * Open the predictions role folder in the system file manager.
 * @param {string} role output role (predictions)
 * @returns {HTMLButtonElement}
 */
function attachRunBrowseButton(role) {
	var btn = document.createElement("button");
	btn.type = "button";
	btn.className = "btn btn-sm btn-outline-secondary ms-1";
	btn.title = "Open predictions folder (all detection runs)";
	btn.setAttribute("aria-label", "Open predictions folder");
	btn.innerHTML = '<i class="fas fa-folder-open" aria-hidden="true"></i>';

	btn.addEventListener("click", function () {
		if (!project.isActive()) {
			return;
		}
		var abs = pipelineRuns.resolveRoleBaseAbs(role);
		if (!abs) {
			alert("Predictions folder not found for this project.");
			return;
		}
		ipc.send("openPathInShell", abs);
	});

	return btn;
}

module.exports = {
	attachRunDeleteButton: attachRunDeleteButton,
	attachRunBrowseButton: attachRunBrowseButton,
};
