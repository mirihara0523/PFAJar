#!/usr/bin/env node
"use strict";

var assert = require("assert");
var legacyMode = require("../js/legacy_mode");

var STORAGE_KEY = legacyMode.ACK_STORAGE_KEY;

function withMockStorage(fn) {
	var prior = global.localStorage;
	var store = {};
	global.localStorage = {
		getItem: function (key) {
			return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
		},
		setItem: function (key, value) {
			store[key] = String(value);
		},
		removeItem: function (key) {
			delete store[key];
		},
	};
	try {
		fn(store);
	} finally {
		global.localStorage = prior;
	}
}

withMockStorage(function () {
	assert.strictEqual(legacyMode.hasAcknowledgedLegacyMode(), false);
	legacyMode.setLegacyModeAcknowledged();
	assert.strictEqual(legacyMode.hasAcknowledgedLegacyMode(), true);
	localStorage.setItem(
		STORAGE_KEY,
		JSON.stringify({ version: 0, acknowledged: true }),
	);
	assert.strictEqual(legacyMode.hasAcknowledgedLegacyMode(), false);
});

assert.ok(Array.isArray(legacyMode.CAVEATS.supported));
assert.ok(legacyMode.CAVEATS.supported.length >= 5);
assert.ok(Array.isArray(legacyMode.CAVEATS.unavailable));
assert.ok(legacyMode.CAVEATS.unavailable.length >= 5);

var hrefs = legacyMode.listAllMenuToolHrefs();
assert.ok(hrefs.length >= 14, "expected menu tool href mappings");

var menuCategory = require("../js/menu_category.js");
var categories = menuCategory.CATEGORIES;
assert.ok(categories, "menu_category should export CATEGORIES for tests");

function collectHrefs(tools, out) {
	for (var i = 0; i < tools.length; i++) {
		var tool = tools[i];
		if (tool.group && tool.tools) {
			collectHrefs(tool.tools, out);
		} else if (tool.href) {
			out.push(tool.href);
		}
	}
}

var menuHrefs = [];
var catKeys = Object.keys(categories);
for (var c = 0; c < catKeys.length; c++) {
	collectHrefs(categories[catKeys[c]].tools, menuHrefs);
}

for (var h = 0; h < menuHrefs.length; h++) {
	var href = menuHrefs[h];
	assert.ok(
		legacyMode.getLegacyStatusForHref(href),
		"missing legacy status for " + href,
	);
}

assert.strictEqual(legacyMode.getLegacyStatusForHref("./sharpen_wizard.html"), "blocked");
assert.strictEqual(legacyMode.getLegacyStatusForHref("./max.html"), "full");
assert.strictEqual(legacyMode.getLegacyStatusForHref("./detect_wizard.html"), "partial");

assert.ok(
	legacyMode.getLegacyPipelineCardSubtitle("preprocess").indexOf("Max") >= 0,
);

console.log("test-legacy-mode.js ok");
