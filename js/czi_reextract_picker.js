"use strict";

/**
 * Subset slice/channel picker for CZI re-extract (used by czi_wizard.js reextract flow).
 */
var fs = require("fs");
var path = require("path");
var url = require("url");
var cziImport = require("./czi_import");

function escapeHtml(s) {
	return String(s)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}

function createPickerState() {
	return {
		bundleRoot: "",
		cziImportCfg: null,
		projectData: null,
		sliceIds: [],
		selectedSliceIds: {},
		blankSliceIds: {},
		selectedRoleKeys: {},
		blankPreviews: [],
	};
}

function parsePreselectedSlices(search) {
	var params = new URLSearchParams(search || "");
	var raw = params.get("slices") || "";
	if (!raw) {
		return [];
	}
	return raw
		.split(",")
		.map(function (s) {
			return s.trim();
		})
		.filter(Boolean);
}

function initPicker(state, bundleRoot, cziImportCfg, projectData, preselectedSlices) {
	state.bundleRoot = bundleRoot;
	state.cziImportCfg = cziImportCfg;
	state.projectData = projectData;
	state.sliceIds = cziImport.collectSliceIdsFromImport(cziImportCfg);
	state.selectedSliceIds = {};
	state.blankSliceIds = {};
	state.selectedRoleKeys = {};
	state.blankPreviews = [];
	var pre = preselectedSlices || [];
	for (var i = 0; i < pre.length; i++) {
		if (state.sliceIds.indexOf(pre[i]) >= 0) {
			state.selectedSliceIds[pre[i]] = true;
		}
	}
}

function selectedSliceIdList(state) {
	return Object.keys(state.selectedSliceIds).filter(function (k) {
		return state.selectedSliceIds[k];
	});
}

function selectedRoleKeyList(state) {
	var keys = [];
	for (var k in state.selectedRoleKeys) {
		if (Object.prototype.hasOwnProperty.call(state.selectedRoleKeys, k) && state.selectedRoleKeys[k]) {
			keys.push(k);
		}
	}
	return keys;
}

function buildTargets(state) {
	return cziImport.buildRepairTargetsForSelection(
		state.cziImportCfg,
		selectedSliceIdList(state),
		selectedRoleKeyList(state),
	);
}

function renderSliceList(state, qs) {
	var container = qs("reextractSliceList");
	if (!container) {
		return;
	}
	var showBlankOnly = qs("reextractShowBlankOnly") && qs("reextractShowBlankOnly").checked;
	container.innerHTML = "";
	for (var i = 0; i < state.sliceIds.length; i++) {
		var sliceId = state.sliceIds[i];
		if (showBlankOnly && !state.blankSliceIds[sliceId]) {
			continue;
		}
		var card = document.createElement("div");
		card.className =
			"reextract-slice-card" + (state.blankSliceIds[sliceId] ? " blank-dapi" : "");
		var checked = !!state.selectedSliceIds[sliceId];
		var previewPath = cziImport.orientDapiPreviewPath(state.bundleRoot, sliceId);
		var imgHtml = "";
		if (fs.existsSync(previewPath)) {
			var href = url.pathToFileURL(previewPath).href;
			imgHtml =
				'<img src="' +
				escapeHtml(href) +
				'" alt="" loading="lazy" onerror="this.style.display=\'none\'" />';
		}
		var badge = state.blankSliceIds[sliceId]
			? '<span class="badge bg-warning text-dark ms-1">Blank DAPI</span>'
			: "";
		card.innerHTML =
			'<div class="form-check mb-1">' +
			'<input class="form-check-input reextract-slice" type="checkbox" id="reextract_slice_' +
			escapeHtml(sliceId) +
			'" value="' +
			escapeHtml(sliceId) +
			'"' +
			(checked ? " checked" : "") +
			" />" +
			'<label class="form-check-label small" for="reextract_slice_' +
			escapeHtml(sliceId) +
			'">' +
			escapeHtml(sliceId) +
			badge +
			"</label></div>" +
			imgHtml;
		container.appendChild(card);
	}
	container.querySelectorAll(".reextract-slice").forEach(function (el) {
		el.addEventListener("change", function () {
			if (el.checked) {
				state.selectedSliceIds[el.value] = true;
			} else {
				delete state.selectedSliceIds[el.value];
			}
		});
	});
}

function renderChannelList(state, qs) {
	var container = qs("reextractChannelList");
	if (!container) {
		return;
	}
	container.innerHTML = "";
	var channels = cziImport.listKeptChannelsForReimport(state.cziImportCfg);
	var blankRoles = {};
	for (var b = 0; b < state.blankPreviews.length; b++) {
		blankRoles[state.blankPreviews[b].role_key] = true;
	}
	for (var i = 0; i < channels.length; i++) {
		var entry = channels[i];
		var roleKey = entry.role_key;
		var checked =
			state.selectedRoleKeys[roleKey] != null
				? state.selectedRoleKeys[roleKey]
				: roleKey === cziImport.ROLE_DAPI || !!blankRoles[roleKey];
		if (state.selectedRoleKeys[roleKey] == null) {
			state.selectedRoleKeys[roleKey] = checked;
		}
		var div = document.createElement("div");
		div.className = "form-check";
		div.innerHTML =
			'<input class="form-check-input reextract-channel" type="checkbox" id="reextract_ch_' +
			escapeHtml(roleKey) +
			'" value="' +
			escapeHtml(roleKey) +
			'"' +
			(checked ? " checked" : "") +
			" />" +
			'<label class="form-check-label" for="reextract_ch_' +
			escapeHtml(roleKey) +
			'">' +
			escapeHtml(entry.label) +
			" (index " +
			entry.index +
			")</label>";
		container.appendChild(div);
	}
	container.querySelectorAll(".reextract-channel").forEach(function (el) {
		el.addEventListener("change", function () {
			state.selectedRoleKeys[el.value] = el.checked;
		});
	});
}

function collectReextractWarnings(state) {
	var warnParts = [];
	var targets = buildTargets(state);
	var validation = cziImport.validateReimportSources(targets);
	if (!validation.ok) {
		warnParts.push(
			validation.missing.length +
				" target(s) missing source CZI on disk. Re-import cannot proceed until files are reachable.",
		);
	}
	var cziCfg = state.cziImportCfg || {};
	if (cziCfg.geometry_applied_at) {
		warnParts.push(
			"Orientation was previously applied to this project. Re-import writes raw CZI orientation — re-apply geometry on step 5 (Orient) after extract.",
		);
	}
	var proc = (state.projectData && state.projectData.processing) || {};
	if (proc.active_runs && proc.active_runs.slices) {
		warnParts.push(
			"Alignment outputs exist. Re-import does not modify annotations; re-run Align for affected sections if needed.",
		);
	}
	if (proc.active_runs && proc.active_runs.predictions) {
		warnParts.push(
			"Detection outputs exist. Re-run detection if signal channels were replaced.",
		);
	}
	if (proc.active_runs && (proc.active_runs.sharpen || proc.active_runs.max)) {
		warnParts.push(
			"Sharpen or other preprocess runs may be stale after signal re-import.",
		);
	}
	var roleKeys = selectedRoleKeyList(state);
	var maxRuns = (cziCfg.max_runs || {});
	for (var r = 0; r < roleKeys.length; r++) {
		var rk = roleKeys[r];
		if (rk === cziImport.ROLE_DAPI) {
			continue;
		}
		if (!maxRuns[rk]) {
			warnParts.push(
				"No max run registered for role " +
					rk +
					" — z-stacks and previews will update but max TIFF may not refresh.",
			);
		}
	}
	return { warnParts: warnParts, targets: targets, validation: validation };
}

function renderConfirmStep(state, qs) {
	var tbody = qs("reextractConfirmTableBody");
	var warnings = qs("reextractConfirmWarnings");
	if (!tbody) {
		return collectReextractWarnings(state);
	}
	tbody.innerHTML = "";
	var info = collectReextractWarnings(state);
	var targets = info.targets;
	var validation = info.validation;
	if (warnings) {
		if (info.warnParts.length) {
			warnings.innerHTML = info.warnParts.join(" ");
			warnings.classList.remove("d-none");
		} else {
			warnings.textContent = "";
			warnings.classList.add("d-none");
		}
	}
	for (var i = 0; i < targets.length; i++) {
		var t = targets[i];
		var item = null;
		var workItems = cziImport.iterKeptChannelScenes(state.cziImportCfg);
		for (var w = 0; w < workItems.length; w++) {
			if (
				workItems[w].slice_id === t.slice_id &&
				workItems[w].role_key === t.role_key &&
				workItems[w].channel_index === t.channel_index
			) {
				item = workItems[w];
				break;
			}
		}
		if (!item) {
			continue;
		}
		var outputs = cziImport.listReimportOutputPaths(
			state.bundleRoot,
			item,
			state.cziImportCfg,
			state.projectData,
		);
		var tr = document.createElement("tr");
		var cziLabel = t.czi_path ? path.basename(t.czi_path) : "(missing)";
		var cziClass =
			t.czi_path && fs.existsSync(t.czi_path) ? "" : "table-danger";
		tr.innerHTML =
			"<td>" +
			escapeHtml(t.slice_id) +
			"</td><td>" +
			escapeHtml(t.role_key) +
			"</td><td" +
			(cziClass ? ' class="' + cziClass + '"' : "") +
			">" +
			escapeHtml(cziLabel) +
			'</td><td class="small text-muted"><ul class="mb-0 ps-3">' +
			outputs
				.map(function (p) {
					return "<li>" + escapeHtml(path.relative(state.bundleRoot, p)) + "</li>";
				})
				.join("") +
			"</ul></td>";
		tbody.appendChild(tr);
	}
	var runBtn = qs("reextractStep3Run");
	var confirmBox = qs("reextractConfirmOverwrite");
	if (runBtn) {
		runBtn.disabled = !validation.ok || !(confirmBox && confirmBox.checked);
	}
	return info;
}

function scanBlankPreviewsAsync(state, qs) {
	var status = qs("reextractSliceScanStatus");
	if (status) {
		status.textContent = "Scanning previews for blank DAPI…";
	}
	return cziImport
		.findBlankPreviewsAsync(state.bundleRoot, state.cziImportCfg, {})
		.then(function (blanks) {
			state.blankPreviews = blanks || [];
			state.blankSliceIds = {};
			for (var i = 0; i < state.blankPreviews.length; i++) {
				if (state.blankPreviews[i].role_key === cziImport.ROLE_DAPI) {
					state.blankSliceIds[state.blankPreviews[i].slice_id] = true;
					if (!Object.keys(state.selectedSliceIds).length) {
						state.selectedSliceIds[state.blankPreviews[i].slice_id] = true;
					}
				}
			}
			if (status) {
				status.textContent =
					state.blankPreviews.length > 0
						? state.blankPreviews.length + " blank preview(s) detected."
						: state.sliceIds.length + " section(s) available.";
			}
			renderSliceList(state, qs);
		});
}

module.exports = {
	createPickerState: createPickerState,
	parsePreselectedSlices: parsePreselectedSlices,
	initPicker: initPicker,
	selectedSliceIdList: selectedSliceIdList,
	selectedRoleKeyList: selectedRoleKeyList,
	buildTargets: buildTargets,
	renderSliceList: renderSliceList,
	renderChannelList: renderChannelList,
	renderConfirmStep: renderConfirmStep,
	collectReextractWarnings: collectReextractWarnings,
	scanBlankPreviewsAsync: scanBlankPreviewsAsync,
};
