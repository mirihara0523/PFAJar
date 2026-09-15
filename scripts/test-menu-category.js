#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const menuPath = path.join(__dirname, "..", "js", "menu_category.js");
const src = fs.readFileSync(menuPath, "utf8");

function extractPreprocessToolsSource(text) {
	const start = text.indexOf("preprocess:");
	if (start < 0) {
		return null;
	}
	const toolsIdx = text.indexOf("tools:", start);
	if (toolsIdx < 0) {
		return null;
	}
	const open = text.indexOf("[", toolsIdx);
	if (open < 0) {
		return null;
	}
	let depth = 0;
	for (let i = open; i < text.length; i++) {
		const ch = text[i];
		if (ch === "[") {
			depth++;
		} else if (ch === "]") {
			depth--;
			if (depth === 0) {
				return text.slice(open + 1, i);
			}
		}
	}
	return null;
}

const block = extractPreprocessToolsSource(src);
if (!block) {
	console.error("preprocess tools block not found");
	process.exit(1);
}

const topLevelLabels = [];
const deprecatedLabels = [];
let arrayDepth = 0;
const tokenRe = /(\[|\]|group:\s*"([^"]+)"|label:\s*"([^"]+)")/g;
let match;
while ((match = tokenRe.exec(block))) {
	if (match[1] === "[") {
		arrayDepth++;
		continue;
	}
	if (match[1] === "]") {
		arrayDepth = Math.max(0, arrayDepth - 1);
		continue;
	}
	if (match[2] === "Deprecated & Experimental") {
		continue;
	}
	const label = match[3];
	if (!label) {
		continue;
	}
	if (arrayDepth >= 1) {
		deprecatedLabels.push(label);
	} else {
		topLevelLabels.push(label);
	}
}

const requiredTop = [
	"Max Projection",
	"Sharpen",
	"Top-hat filter",
	"Re-import sections from CZI",
	"Semi-manual tissue edge cleanup",
];
for (const label of requiredTop) {
	if (topLevelLabels.indexOf(label) < 0) {
		console.error("Missing top-level tool:", label, topLevelLabels);
		process.exit(1);
	}
}
for (const moved of ["DAPI cleanup", "Orient slices"]) {
	if (topLevelLabels.indexOf(moved) >= 0) {
		console.error(moved, "should not be top-level");
		process.exit(1);
	}
	if (deprecatedLabels.indexOf(moved) < 0) {
		console.error(moved, "missing from Deprecated & Experimental", deprecatedLabels);
		process.exit(1);
	}
}

console.log("test-menu-category.js ok");

if (!/btn btn-primary w-100/.test(src) || !/btn btn-secondary w-100/.test(src)) {
	console.error("appendToolLink must assign w-100 to primary and secondary tool links");
	process.exit(1);
}
if (!/wrapper.className = "text-start w-100"/.test(src)) {
	console.error("appendToolLink wrapper must use text-start w-100");
	process.exit(1);
}

console.log("test-menu-category.js layout ok");
