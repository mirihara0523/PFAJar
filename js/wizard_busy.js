"use strict";

/**
 * Shared wizard busy state — disable controls and show immediate feedback.
 * Reference: js/czi_wizard.js setProbeControlsBusy / setExtractNavDisabled.
 */

var _busy = false;
var _savedStates = [];

function _qs(sel) {
	if (!sel || typeof document === "undefined") return null;
	if (typeof sel === "string") {
		return document.querySelector(sel);
	}
	return sel;
}

function _saveDisabled(el) {
	if (!el) return;
	_savedStates.push({ el: el, disabled: el.disabled });
}

function setWizardBusy(opts) {
	opts = opts || {};
	var busy = !!opts.busy;
	_busy = busy;
	var root = opts.rootId
		? typeof document !== "undefined" && document.getElementById(opts.rootId)
		: typeof document !== "undefined"
			? document.body
			: null;
	if (root) {
		if (busy) {
			root.classList.add("wizard-busy");
			root.setAttribute("aria-busy", "true");
		} else {
			root.classList.remove("wizard-busy");
			root.removeAttribute("aria-busy");
		}
	}
	if (busy) {
		_savedStates = [];
		var primary = _qs(opts.primarySelector);
		var cancel = _qs(opts.cancelSelector);
		var backs = opts.backSelectors || [];
		_saveDisabled(primary);
		_saveDisabled(cancel);
		for (var i = 0; i < backs.length; i++) {
			_saveDisabled(_qs(backs[i]));
		}
		if (opts.extraSelectors) {
			for (var j = 0; j < opts.extraSelectors.length; j++) {
				_saveDisabled(_qs(opts.extraSelectors[j]));
			}
		}
		if (primary) primary.disabled = true;
		if (cancel && !opts.keepCancelEnabled) cancel.disabled = true;
		for (var b = 0; b < backs.length; b++) {
			var backEl = _qs(backs[b]);
			if (backEl) backEl.disabled = true;
		}
		if (opts.extraSelectors) {
			for (var k = 0; k < opts.extraSelectors.length; k++) {
				var ex = _qs(opts.extraSelectors[k]);
				if (ex) ex.disabled = true;
			}
		}
		if (opts.stepPillSelector) {
			var pills = document.querySelectorAll(opts.stepPillSelector);
			for (var p = 0; p < pills.length; p++) {
				pills[p].classList.add("disabled");
				pills[p].style.pointerEvents = "none";
			}
		}
	} else {
		for (var r = 0; r < _savedStates.length; r++) {
			var item = _savedStates[r];
			if (item.el) item.el.disabled = item.disabled;
		}
		_savedStates = [];
		if (opts.stepPillSelector) {
			var pills2 = document.querySelectorAll(opts.stepPillSelector);
			for (var p2 = 0; p2 < pills2.length; p2++) {
				pills2[p2].style.pointerEvents = "";
			}
		}
	}
	var msgEl = _qs(opts.messageEl);
	if (msgEl && opts.message != null && typeof document !== "undefined") {
		msgEl.textContent = opts.message;
	}
}

function isWizardBusy() {
	return _busy;
}

function guardClick(fn, opts) {
	return function () {
		if (_busy) return;
		setWizardBusy(Object.assign({}, opts || {}, { busy: true }));
		try {
			return fn.apply(this, arguments);
		} catch (err) {
			setWizardBusy(Object.assign({}, opts || {}, { busy: false }));
			throw err;
		}
	};
}

module.exports = {
	setWizardBusy: setWizardBusy,
	isWizardBusy: isWizardBusy,
	guardClick: guardClick,
};
