"use strict";

var ipc = require("electron").ipcRenderer;

var ROLE_LABELS = {
	original_scans: "Original scans",
	dapi: "00 DAPI (low-res PNG)",
	slices: "01 Slices (align output)",
	max: "03 Max projection",
	predictions: "05 Predictions",
	quantification: "06 Quantification",
	pkls: "07 PKLs (isolate regions)",
	dual: "08 Dual-channel export",
};

/**
 * @param {{ tag: string, defaultPath?: string }} opts
 * @returns {Promise<string|null>} Selected directory path, or null if canceled.
 */
function pickDirectory(opts) {
	opts = opts || {};
	var tag = opts.tag || "input";
	var payload = opts.defaultPath ? { tag: tag, defaultPath: opts.defaultPath } : tag;
	return ipc
		.invoke("showOpenDirectoryDialog", payload)
		.then(function (result) {
			if (!result) {
				alert("Folder dialog failed: no response from the app.");
				return null;
			}
			if (result.error) {
				alert("Could not open folder dialog:\n" + result.error);
				return null;
			}
			if (result.canceled || !result.path) {
				return null;
			}
			return result.path;
		})
		.catch(function (err) {
			alert("Could not open folder dialog:\n" + String(err.message || err));
			return null;
		});
}

/**
 * @param {{ tag?: string, defaultPath?: string }} opts
 * @returns {Promise<string[]|null>} Selected directory paths, or null if canceled.
 */
function pickNetworkLocations(opts) {
	opts = opts || {};
	var tag = opts.tag || "nasLocations";
	var payload = { tag: tag, multi: true };
	if (opts.defaultPath) {
		payload.defaultPath = opts.defaultPath;
	}
	return ipc
		.invoke("showOpenNetworkLocationsDialog", payload)
		.then(function (result) {
			if (!result) {
				alert("Folder dialog failed: no response from the app.");
				return null;
			}
			if (result.error) {
				alert("Could not open folder dialog:\n" + result.error);
				return null;
			}
			if (result.canceled || !result.paths || !result.paths.length) {
				return null;
			}
			return result.paths;
		})
		.catch(function (err) {
			alert("Could not open folder dialog:\n" + String(err.message || err));
			return null;
		});
}

function ensurePickRoleModal(canonicalRoles) {
	var modalEl = document.getElementById("mjPickRoleModal");
	if (modalEl) {
		return modalEl;
	}
	modalEl = document.createElement("div");
	modalEl.className = "modal fade";
	modalEl.id = "mjPickRoleModal";
	modalEl.tabIndex = -1;
	modalEl.setAttribute("aria-labelledby", "mjPickRoleModalLabel");
	modalEl.setAttribute("aria-hidden", "true");
	modalEl.innerHTML =
		'<div class="modal-dialog modal-dialog-centered">' +
		'<div class="modal-content text-start">' +
		'<div class="modal-header">' +
		'<h5 class="modal-title" id="mjPickRoleModalLabel">Add files to role</h5>' +
		'<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
		"</div>" +
		'<div class="modal-body">' +
		'<label class="form-label" for="mjPickRoleSelect">Destination role</label>' +
		'<select class="form-select" id="mjPickRoleSelect"></select>' +
		'<p class="form-text text-muted mb-0">Files are copied into the project bundle under that role folder.</p>' +
		"</div>" +
		'<div class="modal-footer">' +
		'<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>' +
		'<button type="button" class="btn btn-primary" id="mjPickRoleConfirm">Choose folder…</button>' +
		"</div>" +
		"</div></div>";
	document.body.appendChild(modalEl);

	var select = modalEl.querySelector("#mjPickRoleSelect");
	var keys = Object.keys(canonicalRoles || {}).sort();
	for (var i = 0; i < keys.length; i++) {
		var key = keys[i];
		var opt = document.createElement("option");
		opt.value = key;
		opt.textContent = (ROLE_LABELS[key] || key) + " — " + canonicalRoles[key];
		select.appendChild(opt);
	}
	return modalEl;
}

/**
 * @param {Record<string, string>} canonicalRoles role key → relative path
 * @param {string} [defaultRole]
 * @returns {Promise<string|null>} Selected role key, or null if canceled.
 */
function pickProjectRole(canonicalRoles, defaultRole) {
	defaultRole = defaultRole || "dapi";
	return new Promise(function (resolve) {
		if (!window.bootstrap || !window.bootstrap.Modal) {
			alert("Role picker unavailable (Bootstrap not loaded).");
			resolve(null);
			return;
		}
		var modalEl = ensurePickRoleModal(canonicalRoles);
		var select = modalEl.querySelector("#mjPickRoleSelect");
		var confirmBtn = modalEl.querySelector("#mjPickRoleConfirm");
		if (canonicalRoles[defaultRole]) {
			select.value = defaultRole;
		}
		var modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
		var confirmed = false;

		function onHidden() {
			modalEl.removeEventListener("hidden.bs.modal", onHidden);
			if (!confirmed) {
				resolve(null);
			}
		}

		function onConfirm() {
			confirmed = true;
			var role = String(select.value || "").trim();
			modalEl.removeEventListener("hidden.bs.modal", onHidden);
			modal.hide();
			resolve(role || null);
		}

		confirmBtn.onclick = onConfirm;
		modalEl.addEventListener("hidden.bs.modal", onHidden);
		modal.show();
	});
}

/**
 * Three-button confirm modal.
 * @param {{ title?: string, message: string, buttons: Array<{id:string,label:string,primary?:boolean}> }} opts
 * @returns {Promise<string|null>} Selected button id, or null if dismissed.
 */
function confirmThreeWay(opts) {
	opts = opts || {};
	var title = opts.title || "Confirm";
	var message = opts.message || "";
	var buttons = opts.buttons || [];
	return new Promise(function (resolve) {
		if (!window.bootstrap || !window.bootstrap.Modal) {
			// Fallback: Apply = OK, Skip = second confirm, Cancel = dismiss
			if (window.confirm(message + "\n\nOK = Apply and run, Cancel = cancel.")) {
				resolve(buttons[0] ? buttons[0].id : "apply");
			} else {
				resolve("cancel");
			}
			return;
		}
		var modalEl = document.getElementById("mjConfirmThreeWayModal");
		if (!modalEl) {
			modalEl = document.createElement("div");
			modalEl.className = "modal fade";
			modalEl.id = "mjConfirmThreeWayModal";
			modalEl.tabIndex = -1;
			modalEl.setAttribute("aria-hidden", "true");
			document.body.appendChild(modalEl);
		}
		var btnHtml = "";
		for (var i = 0; i < buttons.length; i++) {
			var b = buttons[i];
			var cls = b.primary ? "btn btn-primary" : "btn btn-outline-secondary";
			btnHtml +=
				'<button type="button" class="' +
				cls +
				'" data-choice="' +
				String(b.id) +
				'">' +
				String(b.label) +
				"</button>";
		}
		modalEl.innerHTML =
			'<div class="modal-dialog modal-dialog-centered">' +
			'<div class="modal-content text-start">' +
			'<div class="modal-header">' +
			'<h5 class="modal-title">' +
			String(title) +
			"</h5>" +
			'<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
			"</div>" +
			'<div class="modal-body"><p class="mb-0">' +
			String(message) +
			"</p></div>" +
			'<div class="modal-footer flex-wrap gap-2">' +
			btnHtml +
			"</div></div></div>";

		var modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
		var chosen = null;

		function onHidden() {
			modalEl.removeEventListener("hidden.bs.modal", onHidden);
			resolve(chosen);
		}

		modalEl.addEventListener("hidden.bs.modal", onHidden);
		var choiceBtns = modalEl.querySelectorAll("[data-choice]");
		for (var c = 0; c < choiceBtns.length; c++) {
			choiceBtns[c].addEventListener("click", function (ev) {
				chosen = ev.currentTarget.getAttribute("data-choice");
				modal.hide();
			});
		}
		modal.show();
	});
}

module.exports = {
	pickDirectory: pickDirectory,
	pickNetworkLocations: pickNetworkLocations,
	pickProjectRole: pickProjectRole,
	confirmThreeWay: confirmThreeWay,
	ROLE_LABELS: ROLE_LABELS,
};
