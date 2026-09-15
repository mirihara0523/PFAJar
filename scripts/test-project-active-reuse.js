"use strict";

var assert = require("assert");
var fs = require("fs");
var path = require("path");
var helpers = require("./test-helpers");

helpers.ensureLocalStorage();

var project = require("../js/project");

function main() {
	var bundle = helpers.tmpDir("mj-active-reuse-");
	var projectFile = path.join(bundle, "reuse.masonjar");
	fs.writeFileSync(projectFile, JSON.stringify({ name: "reuse", roles: {} }), "utf8");
	var inMemory = { name: "in-memory", roles: { dapi: "custom-dapi" } };
	project.setActiveProject(bundle, inMemory);

	assert.strictEqual(project.tryRestoreActiveProject(), true);
	assert.strictEqual(
		project.getProject(),
		inMemory,
		"same active bundle should be reused instead of reopened",
	);

	project.clearActiveProject();
	helpers.rmDir(bundle);
	console.log("test-project-active-reuse.js: OK");
}

main();
