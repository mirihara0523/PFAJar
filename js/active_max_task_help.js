"use strict";

var POPOVER_CLASS = "active-max-help-popover";
var POPOVER_TITLE = "Active max task";

function filteredImageLabel(toolKind) {
	if (toolKind === "tophat") {
		return "top-hat filtered";
	}
	return "sharpened";
}

function buildActiveMaxHelpHtml(opts) {
	opts = opts || {};
	var toolKind = opts.toolKind === "tophat" ? "tophat" : "sharpen";
	var filteredLabel = filteredImageLabel(toolKind);

	return (
		'<div class="active-max-help-content text-start">' +
		"<p><strong>Why it matters</strong></p>" +
		"<p>On a given signal branch (somata, nuclei, axons, etc.) you can have several intensity datasets under <code>03_max</code>:</p>" +
		'<table class="table table-sm table-bordered mb-2">' +
		"<thead><tr><th>Kind</th><th>Example path</th></tr></thead>" +
		"<tbody>" +
		"<tr><td>Max projection</td><td><code>03_max/somata/max/...</code></td></tr>" +
		"<tr><td>Sharpen</td><td><code>03_max/somata/sharpen/...</code></td></tr>" +
		"<tr><td>Top-hat</td><td><code>03_max/somata/tophat/...</code></td></tr>" +
		"</tbody></table>" +
		"<p>If you run this step but do not set it active, downstream tools may still default to the original max projection (or whatever was active before).</p>" +
		"<p>If you check this box, <strong>Cell Detection</strong>, <strong>Isolate Regions</strong>, and related steps will use the " +
		filteredLabel +
		" TIFFs by default when you open those tools (or when the Intensity dataset picker loads).</p>" +
		"<p><strong>For this branch</strong></p>" +
		"<p>That refers to the signal branch you picked at the top of this wizard (e.g. somata vs nuclei). " +
		"This tool always writes under that branch; checking the box makes this run the project-wide active max dataset.</p>" +
		"<p><strong>Practical guidance</strong></p>" +
		"<ul class=\"mb-0 ps-3\">" +
		"<li>Check it if you intend to run Detect / Isolate Regions on the " +
		filteredLabel +
		" images next.</li>" +
		"<li>Leave it unchecked if you were experimenting, or you still want detection/intensity on the unsharpened max projection.</li>" +
		"<li>Sharpen and Top-hat are independent — you can chain them and set active after whichever result you want downstream to use.</li>" +
		"</ul>" +
		"</div>"
	);
}

function getBootstrapPopover() {
	if (typeof window === "undefined" || !window.bootstrap || !window.bootstrap.Popover) {
		return null;
	}
	return window.bootstrap.Popover;
}

function wireActiveMaxHelpPopover(buttonEl, opts) {
	if (!buttonEl) {
		return false;
	}
	var Popover = getBootstrapPopover();
	if (!Popover) {
		return false;
	}
	if (buttonEl._mjActiveMaxHelpPopover) {
		buttonEl._mjActiveMaxHelpPopover.dispose();
		buttonEl._mjActiveMaxHelpPopover = null;
	}
	var html = buildActiveMaxHelpHtml(opts);
	buttonEl.setAttribute("data-bs-toggle", "popover");
	buttonEl.setAttribute("data-bs-trigger", "click");
	buttonEl.setAttribute("data-bs-placement", "auto");
	buttonEl.setAttribute("aria-expanded", "false");
	buttonEl._mjActiveMaxHelpPopover = new Popover(buttonEl, {
		title: POPOVER_TITLE,
		content: html,
		html: true,
		sanitize: false,
		trigger: "click",
		placement: "auto",
		customClass: POPOVER_CLASS,
		container: "body",
	});
	return true;
}

module.exports = {
	POPOVER_CLASS: POPOVER_CLASS,
	buildActiveMaxHelpHtml: buildActiveMaxHelpHtml,
	wireActiveMaxHelpPopover: wireActiveMaxHelpPopover,
	filteredImageLabel: filteredImageLabel,
};
