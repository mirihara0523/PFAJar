#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const { unzipSync } = require("cross-zip");
const { wrapWindowsReleaseZipFile } = require("./build-release");

function fail(msg) {
	console.error("test-wrap-windows-zip.js: FAIL —", msg);
	process.exit(1);
}

function ok(msg) {
	console.log("test-wrap-windows-zip.js: OK —", msg);
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "mj-wrap-test-"));
try {
	const flat = path.join(tmp, "flat");
	fs.mkdirSync(flat, { recursive: true });
	fs.writeFileSync(path.join(flat, "masonjar.exe"), "fake");
	const appDir = path.join(flat, "resources", "app");
	fs.mkdirSync(appDir, { recursive: true });
	fs.writeFileSync(
		path.join(appDir, "package.json"),
		JSON.stringify({ name: "masonjar", version: "9.9.9" }, null, 2),
	);
	const zipPath = path.join(tmp, "masonjar-win32-x64-9.9.9.zip");
	const flatPs = flat.replace(/'/g, "''");
	const zipPs = zipPath.replace(/'/g, "''");
	const mk = spawnSync(
		"powershell.exe",
		[
			"-NoLogo",
			"-NoProfile",
			"-Command",
			"Get-ChildItem -LiteralPath '" +
				flatPs +
				"' | Compress-Archive -DestinationPath '" +
				zipPs +
				"' -Force",
		],
		{ stdio: "inherit" },
	);
	if (mk.status !== 0) {
		fail("could not create flat test zip");
	}

	wrapWindowsReleaseZipFile(zipPath, "9.9.9");

	const verifyDir = path.join(tmp, "verify");
	fs.mkdirSync(verifyDir, { recursive: true });
	unzipSync(zipPath, verifyDir);
	const top = fs.readdirSync(verifyDir);
	if (top.length !== 1 || top[0] !== "masonjar-win32-x64") {
		fail("expected single top-level masonjar-win32-x64/, got: " + top.join(", "));
	}
	const wrapped = path.join(verifyDir, "masonjar-win32-x64");
	if (!fs.existsSync(path.join(wrapped, "masonjar.exe"))) {
		fail("masonjar.exe missing inside wrapper folder");
	}
	if (!fs.existsSync(path.join(wrapped, "resources", "app", "package.json"))) {
		fail("resources/app/package.json missing inside wrapper");
	}
	if (!fs.existsSync(path.join(wrapped, "package.json"))) {
		fail("root package.json shim missing next to masonjar.exe");
	}
	const rootPkg = JSON.parse(
		fs.readFileSync(path.join(wrapped, "package.json"), "utf8"),
	);
	if (rootPkg.version !== "9.9.9") {
		fail("root package.json shim version mismatch: " + rootPkg.version);
	}

	ok("Windows release zip wraps app in masonjar-win32-x64/");
	ok("root package.json shim present for updater compatibility");
} finally {
	fs.rmSync(tmp, { recursive: true, force: true });
}
