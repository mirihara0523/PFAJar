"use strict";

var assert = require("assert");
var releaseNotes = require("./release_notes");

function testReadV2410() {
	var notes = releaseNotes.readReleaseNotes("2.4.10");
	assert.ok(notes);
	assert.match(notes.whatsNew, /rotate a slice in Orient/i);
	assert.ok(notes.changes.length >= 2);
	assert.strictEqual(
		notes.commitSubject,
		"Orient preview keeps rotate and flip steps together",
	);
	assert.match(notes.commitBody, /no longer reset/i);
}

function testMissingVersionReturnsNull() {
	assert.strictEqual(releaseNotes.readReleaseNotes("0.0.0"), null);
}

function testRequireThrows() {
	assert.throws(
		function () {
			releaseNotes.requireReleaseNotes("0.0.0");
		},
		/Missing human release notes/,
	);
}

function run() {
	testReadV2410();
	testMissingVersionReturnsNull();
	testRequireThrows();
	console.log("test-release-notes.js: OK");
}

run();
