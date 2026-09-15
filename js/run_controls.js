"use strict";

/**
 * Shared Run-button busy state for standalone pipeline pages.
 */

function bindRunButton(opts) {
	opts = opts || {};
	var runBtn = document.getElementById(opts.btnId || "run");
	var backBtn = document.getElementById(opts.backId || "back");
	var loadBar = document.getElementById(opts.loadBarId || "loadbar");
	var loadMessage = document.getElementById(opts.loadMessageId || "loadmessage");
	if (!runBtn) return;

	var busy = false;

	function setBusy(on, message) {
		busy = !!on;
		if (on) {
			runBtn.classList.add("disabled");
			runBtn.disabled = true;
			runBtn.innerHTML = "<i class='fas fa-spinner fa-spin'></i>";
			if (backBtn) {
				backBtn.classList.remove("btn-warning");
				backBtn.classList.add("btn-danger");
				backBtn.innerHTML = "Cancel";
			}
			if (loadMessage && message != null) {
				loadMessage.textContent = message;
			}
		} else {
			runBtn.classList.remove("disabled");
			runBtn.disabled = false;
			runBtn.innerHTML = opts.runLabel || "Run";
			if (backBtn) {
				backBtn.classList.add("btn-warning");
				backBtn.classList.remove("btn-danger");
				backBtn.innerHTML = opts.backLabel || "Back";
			}
			if (loadMessage) loadMessage.textContent = "";
			if (loadBar) loadBar.style.width = "0";
		}
	}

	runBtn.addEventListener("click", function () {
		if (busy) return;
		if (typeof opts.onRun === "function") {
			opts.onRun({ setBusy: setBusy, isBusy: function () { return busy; } });
		}
	});

	if (backBtn && opts.killChannel) {
		backBtn.addEventListener("click", function (event) {
			if (backBtn.classList.contains("btn-danger")) {
				event.preventDefault();
				var ipc = require("electron").ipcRenderer;
				ipc.send(opts.killChannel, []);
				setBusy(false);
			}
		});
	}

	if (opts.resultChannel) {
		var ipc = require("electron").ipcRenderer;
		ipc.on(opts.resultChannel, function () {
			setBusy(false);
			if (typeof opts.onResult === "function") {
				opts.onResult.apply(null, arguments);
			}
		});
	}

	return { setBusy: setBusy, isBusy: function () { return busy; } };
}

module.exports = {
	bindRunButton: bindRunButton,
};
