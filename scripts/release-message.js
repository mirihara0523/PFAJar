#!/usr/bin/env node
"use strict";

/**
 * Print a suggested git commit message from docs/RELEASE_NOTES.md for package.json version.
 */

const fs = require("fs");
const path = require("path");
const releaseNotes = require("./release_notes");

const REPO_ROOT = path.join(__dirname, "..");

function readVersion() {
	const pkg = JSON.parse(
		fs.readFileSync(path.join(REPO_ROOT, "package.json"), "utf8"),
	);
	return pkg.version;
}

function main() {
	const version = readVersion();
	const notes = releaseNotes.requireReleaseNotes(version);
	const msg = releaseNotes.suggestedCommitMessage(notes);

	console.log("Suggested commit for v" + version + " (from docs/RELEASE_NOTES.md):\n");
	console.log("Subject:");
	console.log(msg.subject);
	console.log("\nBody:");
	console.log(msg.body);
	console.log("\n---");
	console.log("git commit -m \"$(cat <<'EOF'");
	console.log(msg.subject);
	console.log("");
	console.log(msg.body);
	console.log("EOF");
	console.log(")\"");
}

try {
	main();
} catch (err) {
	console.error(err.message || err);
	process.exit(1);
}
