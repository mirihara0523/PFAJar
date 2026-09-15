"use strict";

/**
 * Parse docs/RELEASE_NOTES.md for human-facing release and commit copy.
 */

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.join(__dirname, "..");
const RELEASE_NOTES_PATH = path.join(REPO_ROOT, "docs", "RELEASE_NOTES.md");

function sectionHeadingPattern(name) {
	const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
	return new RegExp(
		"(?:\\*\\*" + escaped + "\\*\\*|###\\s+" + escaped + ")\\s*\\n",
		"i",
	);
}

function extractSection(block, headingName) {
	const re = sectionHeadingPattern(headingName);
	const match = re.exec(block);
	if (!match) {
		return "";
	}
	const start = match.index + match[0].length;
	const rest = block.slice(start);
	const nextSection = rest.search(
		/\n(?:\*\*[^*]+\*\*|###\s+|\#\#\s+v\d)/,
	);
	const body = nextSection >= 0 ? rest.slice(0, nextSection) : rest;
	return body.trim();
}

function parseChangeBullets(changesText) {
	if (!changesText) {
		return [];
	}
	const bullets = [];
	for (const line of changesText.split(/\r?\n/)) {
		const m = line.match(/^\s*[-*]\s+(.+)$/);
		if (m) {
			bullets.push(m[1].trim());
		}
	}
	return bullets;
}

/**
 * @param {string} version e.g. "2.4.10"
 * @returns {{ whatsNew: string, changes: string[], commitSubject: string, commitBody: string } | null}
 */
function readReleaseNotes(version) {
	if (!fs.existsSync(RELEASE_NOTES_PATH)) {
		return null;
	}
	const text = fs.readFileSync(RELEASE_NOTES_PATH, "utf8");
	const marker = "## v" + version;
	const idx = text.indexOf(marker);
	if (idx < 0) {
		return null;
	}
	const afterMarker = idx + marker.length;
	const nextVersion = text.indexOf("\n## v", afterMarker + 1);
	const block =
		nextVersion >= 0 ? text.slice(afterMarker, nextVersion) : text.slice(afterMarker);

	const whatsNew = extractSection(block, "What's new");
	const changesRaw = extractSection(block, "Changes");
	const commitSubject = extractSection(block, "Commit subject").split("\n")[0].trim();
	const commitBody = extractSection(block, "Commit body");

	if (!whatsNew) {
		return null;
	}

	return {
		whatsNew: whatsNew,
		changes: parseChangeBullets(changesRaw),
		commitSubject: commitSubject,
		commitBody: commitBody,
	};
}

function requireReleaseNotes(version) {
	const notes = readReleaseNotes(version);
	if (!notes || !notes.whatsNew) {
		throw new Error(
			"Missing human release notes for v" +
				version +
				". Add a ## v" +
				version +
				" section with **What's new** in docs/RELEASE_NOTES.md (see docs/COMMIT_AND_RELEASE.md).",
		);
	}
	return notes;
}

function suggestedCommitMessage(notes) {
	var subject = notes.commitSubject;
	if (!subject) {
		subject = notes.whatsNew.split(/\n/)[0].trim();
		if (subject.length > 72) {
			subject = subject.slice(0, 69).trim() + "…";
		}
	}
	var body = notes.commitBody || notes.whatsNew;
	return { subject: subject, body: body };
}

module.exports = {
	RELEASE_NOTES_PATH: RELEASE_NOTES_PATH,
	readReleaseNotes: readReleaseNotes,
	requireReleaseNotes: requireReleaseNotes,
	suggestedCommitMessage: suggestedCommitMessage,
};
