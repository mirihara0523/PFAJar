"use strict";

var assert = require("assert");
var activeMaxTaskHelp = require("../js/active_max_task_help");

function testSharpenHelpHtml() {
	var html = activeMaxTaskHelp.buildActiveMaxHelpHtml({ toolKind: "sharpen" });
	assert.ok(html.indexOf("Why it matters") >= 0);
	assert.ok(html.indexOf("Max projection") >= 0);
	assert.ok(html.indexOf("03_max/somata/sharpen/") >= 0);
	assert.ok(html.indexOf("Cell Detection") >= 0);
	assert.ok(html.indexOf("Isolate Regions") >= 0);
	assert.ok(html.indexOf("sharpened") >= 0);
	assert.ok(html.indexOf("top-hat filtered") < 0);
}

function testTophatHelpHtml() {
	var html = activeMaxTaskHelp.buildActiveMaxHelpHtml({ toolKind: "tophat" });
	assert.ok(html.indexOf("top-hat filtered") >= 0);
	assert.strictEqual(activeMaxTaskHelp.filteredImageLabel("tophat"), "top-hat filtered");
	assert.strictEqual(activeMaxTaskHelp.filteredImageLabel("sharpen"), "sharpened");
}

function testWireWithoutBootstrap() {
	var btn = {};
	assert.strictEqual(
		activeMaxTaskHelp.wireActiveMaxHelpPopover(btn, { toolKind: "sharpen" }),
		false,
	);
}

testSharpenHelpHtml();
testTophatHelpHtml();
testWireWithoutBootstrap();
console.log("test-active-max-help.js: OK");
