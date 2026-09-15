"use strict";

var dialogs = require("./dialogs");
var workspace = require("./workspace");

/** Bump when caveats change — users must re-acknowledge. */
var LEGACY_CAVEATS_VERSION = 1;
var ACK_STORAGE_KEY = "masonjar.legacyModeAck";

var CAVEATS = {
	requirements: [
		"Your brain folder must use the classic Bell Jar layout: a counting/ subfolder (e.g. M528/counting/00_dapi/).",
		"A .masonjar project bundle (M528_masonjar/data/counting/) will not scan correctly in legacy mode.",
		"Paths are auto-filled from counting/ when found; you can override them on each tool page.",
	],
	supported: [
		"Max Projection",
		"Align Sections",
		"Viewer/Editor (Adjust)",
		"DAPI cleanup",
		"Collate Counts",
		"Export dual-channel ROI TIFs",
	],
	partial: [
		"Cell Detection — works with manual paths; no dataset picker or active-run tracking",
		"Count Brain — manual prediction and annotation folders; no run pickers or slice subset",
		"Isolate Regions — manual paths; no project index or active-run updates",
	],
	unavailable: [
		"Sharpen and Top-hat filter",
		"Semi-manual tissue edge cleanup",
		"Orient slices and Check Orientation Consistency",
		"Parcellation (bulk)",
		"CZI import and re-import",
		"Batch processing across projects",
	],
};

/** @type {Record<string, string>} href → legacyStatus for menu tools */
var TOOL_LEGACY_STATUS_BY_HREF = {
	"./max.html": "full",
	"./sharpen_wizard.html": "blocked",
	"./tophat_wizard.html": "blocked",
	"./czi_wizard.html?flow=reextract": "blocked",
	"./tissue_cleanup_wizard.html": "blocked",
	"./orient.html": "blocked",
	"./dapi_cleanup.html": "full",
	"./align.html": "full",
	"./adjust.html": "full",
	"./parcellation_wizard.html": "blocked",
	"./detect_wizard.html": "partial",
	"./count.html": "partial",
	"./collate.html": "full",
	"./intensity.html": "partial",
	"./dual_export.html": "full",
};

var LEGACY_PIPELINE_CARD_SUBS = {
	preprocess: "Max, DAPI cleanup (legacy)",
	alignment: "Align sections, Viewer/Editor",
	detection: "Detection, Count, Collate (some limited)",
	exports: "Isolate regions, Dual-channel TIFs",
};

function readAckRecord() {
	try {
		var raw = localStorage.getItem(ACK_STORAGE_KEY);
		if (!raw) {
			return null;
		}
		return JSON.parse(raw);
	} catch (_err) {
		return null;
	}
}

function hasAcknowledgedLegacyMode() {
	var rec = readAckRecord();
	return !!(rec && rec.version === LEGACY_CAVEATS_VERSION && rec.acknowledged);
}

function setLegacyModeAcknowledged() {
	try {
		localStorage.setItem(
			ACK_STORAGE_KEY,
			JSON.stringify({
				version: LEGACY_CAVEATS_VERSION,
				acknowledged: true,
				at: new Date().toISOString(),
			}),
		);
	} catch (_err) {
		/* ignore */
	}
}

function escapeHtml(s) {
	return String(s)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}

function renderListItems(items) {
	var html = "";
	for (var i = 0; i < items.length; i++) {
		html += "<li>" + escapeHtml(items[i]) + "</li>";
	}
	return html;
}

function populateConsentModalBody(modalEl) {
	var body = modalEl.querySelector(".legacy-mode-consent-body");
	if (!body) {
		return;
	}
	body.innerHTML =
		'<div class="alert alert-warning text-start mb-3" role="alert">' +
		"<strong>Required folder layout</strong>" +
		"<ul class=\"mb-0 mt-2 ps-3\">" +
		renderListItems(CAVEATS.requirements) +
		"</ul></div>" +
		'<div class="row text-start g-3">' +
		'<div class="col-md-4"><h3 class="h6 text-success">Available</h3><ul class="small ps-3">' +
		renderListItems(CAVEATS.supported) +
		"</ul></div>" +
		'<div class="col-md-4"><h3 class="h6 text-warning">Limited</h3><ul class="small ps-3">' +
		renderListItems(CAVEATS.partial) +
		"</ul></div>" +
		'<div class="col-md-4"><h3 class="h6 text-danger">Not available</h3><ul class="small ps-3">' +
		renderListItems(CAVEATS.unavailable) +
		"</ul></div></div>" +
		'<p class="small text-muted text-start mt-3 mb-0">' +
		'Prefer full Mason Jar features? ' +
		'<a href="./project_wizard.html?mode=migrate">Migrate to a .masonjar project</a> instead.' +
		"</p>";
}

function getConsentModalEl() {
	return document.getElementById("legacyModeConsentModal");
}

/**
 * @param {{ requireConsent?: boolean, readOnly?: boolean }} [opts]
 * @returns {Promise<boolean>}
 */
function showLegacyModeConsentModal(opts) {
	opts = opts || {};
	var modalEl = getConsentModalEl();
	if (!modalEl) {
		return Promise.resolve(false);
	}
	populateConsentModalBody(modalEl);

	var check = modalEl.querySelector("#legacyModeConsentCheck");
	var continueBtn = modalEl.querySelector("#legacyModeConsentContinue");
	var cancelBtn = modalEl.querySelector("#legacyModeConsentCancel");
	var checkRow = check ? check.closest(".form-check") : null;
	var readOnly = !!opts.readOnly;

	if (checkRow) {
		checkRow.classList.toggle("d-none", readOnly);
	}
	if (check) {
		check.checked = readOnly || hasAcknowledgedLegacyMode();
		check.disabled = readOnly;
	}
	if (continueBtn) {
		continueBtn.textContent = readOnly ? "Close" : "I understand — continue";
		continueBtn.disabled = !readOnly && !(check && check.checked);
	}
	if (cancelBtn) {
		cancelBtn.classList.toggle("d-none", readOnly);
	}

	return new Promise(function (resolve) {
		if (!window.bootstrap || !window.bootstrap.Modal) {
			resolve(false);
			return;
		}
		var modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);

		function syncContinue() {
			if (continueBtn && check && !readOnly) {
				continueBtn.disabled = !check.checked;
			}
		}

		function onHidden() {
			modalEl.removeEventListener("hidden.bs.modal", onHidden);
			if (check) {
				check.removeEventListener("change", syncContinue);
			}
			if (continueBtn) {
				continueBtn.removeEventListener("click", onContinue);
			}
			var cancelBtn = modalEl.querySelector("#legacyModeConsentCancel");
			if (cancelBtn) {
				cancelBtn.removeEventListener("click", onCancel);
			}
		}

		var agreed = false;

		function onContinue() {
			if (readOnly) {
				agreed = true;
				modal.hide();
				return;
			}
			if (!check || !check.checked) {
				return;
			}
			setLegacyModeAcknowledged();
			agreed = true;
			modal.hide();
		}

		function onCancel() {
			agreed = false;
		}

		if (check) {
			check.addEventListener("change", syncContinue);
		}
		if (continueBtn) {
			continueBtn.addEventListener("click", onContinue);
		}
		var cancelBtn = modalEl.querySelector("#legacyModeConsentCancel");
		if (cancelBtn) {
			cancelBtn.addEventListener("click", onCancel);
		}
		modalEl.addEventListener("hidden.bs.modal", function handler() {
			onHidden();
			resolve(agreed);
		});

		if (opts.requireConsent === false || readOnly) {
			modal.show();
			return;
		}
		if (hasAcknowledgedLegacyMode()) {
			resolve(true);
			return;
		}
		modal.show();
	});
}

/**
 * Consent (if needed) → folder pick → scan brain root.
 * @returns {Promise<{ ok: boolean, workspace?: object, message?: string }>}
 */
function requestLegacyModeEntry() {
	return showLegacyModeConsentModal({ requireConsent: true }).then(function (agreed) {
		if (!agreed) {
			return { ok: false };
		}
		return dialogs.pickDirectory({ tag: "brainRoot" }).then(function (selected) {
			if (!selected) {
				return { ok: false };
			}
			workspace.scanBrainRoot(selected);
			var msg = workspace.getScanStatusMessage();
			var ws = workspace.loadWorkspace();
			if (ws.countingRoot) {
				return { ok: true, workspace: ws, message: msg };
			}
			return { ok: false, workspace: ws, message: msg };
		});
	});
}

function getLegacyStatusForHref(href) {
	return TOOL_LEGACY_STATUS_BY_HREF[href] || "full";
}

function listAllMenuToolHrefs() {
	return Object.keys(TOOL_LEGACY_STATUS_BY_HREF);
}

function getLegacyPipelineCardSubtitle(cat) {
	return LEGACY_PIPELINE_CARD_SUBS[cat] || "";
}

module.exports = {
	LEGACY_CAVEATS_VERSION: LEGACY_CAVEATS_VERSION,
	ACK_STORAGE_KEY: ACK_STORAGE_KEY,
	CAVEATS: CAVEATS,
	TOOL_LEGACY_STATUS_BY_HREF: TOOL_LEGACY_STATUS_BY_HREF,
	hasAcknowledgedLegacyMode: hasAcknowledgedLegacyMode,
	setLegacyModeAcknowledged: setLegacyModeAcknowledged,
	showLegacyModeConsentModal: showLegacyModeConsentModal,
	requestLegacyModeEntry: requestLegacyModeEntry,
	getLegacyStatusForHref: getLegacyStatusForHref,
	listAllMenuToolHrefs: listAllMenuToolHrefs,
	getLegacyPipelineCardSubtitle: getLegacyPipelineCardSubtitle,
	populateConsentModalBody: populateConsentModalBody,
};
