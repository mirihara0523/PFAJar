"use strict";

var assert = require("assert");
var busy = require("../js/project_index_busy");

function testMessagesEditable() {
	assert.ok(Array.isArray(busy.FUNNY_INDEX_MESSAGES));
	assert.ok(busy.FUNNY_INDEX_MESSAGES.length >= 1);
	var sample = busy.FUNNY_INDEX_MESSAGES[0];
	assert.strictEqual(typeof sample, "string");
	assert.ok(sample.length > 0);
}

function testPickFunnyMessage() {
	var a = busy.pickFunnyMessage("");
	assert.ok(busy.FUNNY_INDEX_MESSAGES.indexOf(a) >= 0);
	if (busy.FUNNY_INDEX_MESSAGES.length > 1) {
		var b = busy.pickFunnyMessage(a);
		assert.ok(busy.FUNNY_INDEX_MESSAGES.indexOf(b) >= 0);
	}
}

function testShiftConsoleLines() {
	var lines = ["", "", "", "newest"];
	var next = busy.shiftConsoleLines(lines, "fresh");
	assert.deepStrictEqual(next, ["", "", "newest", "fresh"]);
	assert.deepStrictEqual(busy.LINE_OPACITIES, [0.25, 0.5, 0.75, 1]);
	var again = busy.shiftConsoleLines(next, "third");
	assert.deepStrictEqual(again, ["", "newest", "fresh", "third"]);
	var full = busy.shiftConsoleLines(["a", "b", "c", "d"], "e");
	assert.deepStrictEqual(full, ["b", "c", "d", "e"]);
}

function testRandomDelayRange() {
	for (var i = 0; i < 40; i++) {
		var ms = busy.randomConsoleDelayMs();
		assert.ok(ms >= 500 && ms <= 2000, "delay out of range: " + ms);
	}
}

function testNoShowWithoutDocument() {
	assert.strictEqual(busy.show(), false);
	assert.strictEqual(busy.isVisible(), false);
	busy.hide();
}

function main() {
	testMessagesEditable();
	testPickFunnyMessage();
	testShiftConsoleLines();
	testRandomDelayRange();
	testNoShowWithoutDocument();
	console.log("test-project-index-busy.js: OK");
}

main();
