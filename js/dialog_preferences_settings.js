"use strict";

var pageInit = require("./page_init");
var navTrail = require("./nav_trail");
var dialogPrefs = require("./dialog_preferences");

function qs(id) {
	return document.getElementById(id);
}

function setFeedback(msg) {
	var el = qs("dialogPrefsFeedback");
	if (el) {
		el.textContent = msg || "";
	}
}

function renderList() {
	var list = qs("dialogSuppressedList");
	var empty = qs("dialogPrefsEmpty");
	if (!list) {
		return;
	}
	list.innerHTML = "";
	var items = dialogPrefs.listSuppressed();
	if (empty) {
		empty.classList.toggle("d-none", items.length > 0);
	}
	items.forEach(function (item) {
		var li = document.createElement("li");
		li.className = "list-group-item";
		li.textContent = item.label;
		list.appendChild(li);
	});
}

pageInit.onReady(function () {
	pageInit.installGlobalErrorHandler();
	navTrail.renderTrail(
		[
			{ label: "Start", href: "./menu.html" },
			{ label: "Settings", href: "./settings.html" },
			{ label: "Dialogs" },
		],
		"navTrail",
	);
	renderList();
	var btn = qs("dialogClearSuppressBtn");
	if (btn) {
		btn.addEventListener("click", function () {
			dialogPrefs.clearSuppressions();
			renderList();
			setFeedback("All suppressed warnings will show again.");
		});
	}
});
