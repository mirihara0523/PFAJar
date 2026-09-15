"use strict";

/**
 * Static check: Align setup banners hide the workspace-block row, not only the
 * inner alert (avoids empty bordered frames when banners are hidden).
 */

const fs = require("fs");
const path = require("path");

const REPO = path.join(__dirname, "..");
const htmlPath = path.join(REPO, "pages", "align.html");
const jsPath = path.join(REPO, "js", "align.js");

const html = fs.readFileSync(htmlPath, "utf8");
const js = fs.readFileSync(jsPath, "utf8");

function assert(cond, msg) {
	if (!cond) {
		console.error("test-align-banners.js FAIL:", msg);
		process.exit(1);
	}
}

const rowIds = ["alignSessionRestoreBannerRow", "alignNapariBannerRow"];
for (const id of rowIds) {
	const re = new RegExp(
		'id="' + id + '"[\\s\\S]*?class="[^"]*\\bd-none\\b[^"]*"',
	);
	assert(re.test(html), id + " must exist with d-none on the row");
}

assert(
	js.indexOf("alignSessionRestoreBannerRow") >= 0,
	"align.js must reference alignSessionRestoreBannerRow",
);
assert(
	js.indexOf("alignNapariBannerRow") >= 0,
	"align.js must reference alignNapariBannerRow",
);
assert(
	/alignSessionRestoreBannerRow\.classList\.(add|remove)\(\s*["']d-none["']\s*\)/.test(
		js,
	),
	"align.js must set d-none on alignSessionRestoreBannerRow",
);
assert(
	/alignNapariBannerRow\.classList\.(add|remove)\(\s*["']d-none["']\s*\)/.test(
		js,
	),
	"align.js must set d-none on alignNapariBannerRow",
);

// Inner alerts must not be the sole hide target (no d-none on banner ids alone).
const innerHide =
	/id="align(SessionRestore|Napari)Banner"[\s\n]+class="[^"]*\bd-none\b/;
assert(!innerHide.test(html), "inner banner alerts must not carry d-none alone");

const wizardPanels = ["alignSetupPanel", "alignWarpPanel", "alignFinishPanel"];
for (const id of wizardPanels) {
	assert(
		html.indexOf('id="' + id + '"') >= 0,
		id + " must exist in align.html",
	);
	assert(js.indexOf(id) >= 0, "align.js must reference " + id);
}

assert(
	html.indexOf('id="alignWarpProgress"') >= 0,
	"alignWarpProgress bar must exist",
);
assert(html.indexOf('id="alignWarpLog"') >= 0, "alignWarpLog must exist");
assert(
	js.indexOf("alignWarping") >= 0,
	"align.js must listen for alignWarping",
);
assert(
	js.indexOf("showAlignPhase") >= 0 || js.indexOf('showAlignPhase("warp")') >= 0,
	"align.js must switch to warp phase",
);
assert(
	html.indexOf("Finish</strong> in Napari") >= 0 ||
		html.indexOf("warping progress") >= 0,
	"Napari banner should mention Finish returns Mason Jar for warping",
);

console.log("test-align-banners.js: OK");
