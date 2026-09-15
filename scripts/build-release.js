#!/usr/bin/env node
"use strict";

/**
 * Canonical Mason Jar release build (GitHub artifacts).
 *
 * Agents and humans MUST use this script for releases — do not run a single
 * `electron-forge make --arch=arm64` on the host machine and treat that as a
 * full release.
 *
 * Usage:
 *   node scripts/build-release.js              # macOS Intel + ARM + Windows (local artifacts)
 *   node scripts/build-release.js --windows-only  # Windows x64 zip only
 *   node scripts/build-release.js --macos-only    # macOS Intel + Apple Silicon DMGs only
 *   node scripts/build-release.js --local    # host OS/arch only (dev smoke)
 *   node scripts/build-release.js --no-test  # skip dev tests before packaging
 *   node scripts/build-release.js --dry-run  # print plan only
 *   node scripts/build-release.js --linux    # include Linux .deb (needs dpkg/fakeroot)
 */

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const { unzipSync } = require("cross-zip");

const REPO_ROOT = path.join(__dirname, "..");
const FORGE_MAKE = path.join(
	REPO_ROOT,
	"node_modules",
	"@electron-forge",
	"cli",
	"dist",
	"electron-forge-make.js",
);
const OUT_MAKE = path.join(REPO_ROOT, "out", "make");

/** Default local release build: all desktop platforms. */
const DEFAULT_RELEASE_TARGETS = [
	{
		platform: "darwin",
		arch: "x64",
		label: "macOS Intel",
		githubName: "macOS (Intel)",
	},
	{
		platform: "darwin",
		arch: "arm64",
		label: "macOS Apple Silicon",
		githubName: "macOS (Apple Silicon)",
	},
	{
		platform: "win32",
		arch: "x64",
		label: "Windows x64",
		githubName: "Windows (x64)",
	},
];

const WINDOWS_ONLY_TARGETS = [
	{
		platform: "win32",
		arch: "x64",
		label: "Windows x64",
		githubName: "Windows (x64)",
	},
];

const MACOS_ONLY_TARGETS = [
	{
		platform: "darwin",
		arch: "x64",
		label: "macOS Intel",
		githubName: "macOS (Intel)",
	},
	{
		platform: "darwin",
		arch: "arm64",
		label: "macOS Apple Silicon",
		githubName: "macOS (Apple Silicon)",
	},
];

const LINUX_RELEASE_TARGETS = [
	{
		platform: "linux",
		arch: "x64",
		label: "Linux x64",
		githubName: "Linux (x64)",
	},
	{
		platform: "linux",
		arch: "arm64",
		label: "Linux arm64",
		githubName: "Linux (arm64)",
	},
];

function releaseTargets(opts) {
	if (opts.macosOnly) {
		return MACOS_ONLY_TARGETS.slice();
	}
	let targets = opts.windowsOnly ? WINDOWS_ONLY_TARGETS : DEFAULT_RELEASE_TARGETS;
	if (opts.linux) {
		targets = targets.concat(LINUX_RELEASE_TARGETS);
	}
	return targets;
}

function readVersion() {
	const pkg = JSON.parse(
		fs.readFileSync(path.join(REPO_ROOT, "package.json"), "utf8"),
	);
	return pkg.version;
}

function parseArgs(argv) {
	const opts = {
		local: false,
		dryRun: false,
		noTest: false,
		linux: false,
		windowsOnly: false,
		macosOnly: false,
	};
	for (let i = 2; i < argv.length; i++) {
		const a = argv[i];
		if (a === "--local") {
			opts.local = true;
		} else if (a === "--dry-run") {
			opts.dryRun = true;
		} else if (a === "--no-test") {
			opts.noTest = true;
		} else if (a === "--linux") {
			opts.linux = true;
		} else if (a === "--macos-only") {
			opts.macosOnly = true;
		} else if (a === "--windows-only") {
			opts.windowsOnly = true;
		} else if (a === "--help" || a === "-h") {
			console.log(`Mason Jar release build

  node scripts/build-release.js              macOS Intel + ARM + Windows (default)
  node scripts/build-release.js --windows-only   Windows x64 zip only
  node scripts/build-release.js --macos-only     macOS Intel + Apple Silicon DMGs only
  node scripts/build-release.js --linux        Also build Linux .deb (needs dpkg/fakeroot)
  node scripts/build-release.js --local        Current machine only (dev smoke)
  node scripts/build-release.js --no-test      Skip dev tests before packaging
  node scripts/build-release.js --dry-run      Show targets only

Publish to GitHub: node scripts/publish-release.js  (Windows zip only by default)

Agents: build all platforms locally; publish uploads Windows zip only unless --all-platforms.
`);
			process.exit(0);
		} else {
			console.error("Unknown option:", a);
			process.exit(1);
		}
	}
	return opts;
}

function hostTarget() {
	const platform = process.platform;
	const arch = os.arch() === "x64" ? "x64" : os.arch() === "arm64" ? "arm64" : os.arch();
	const all = DEFAULT_RELEASE_TARGETS.concat(LINUX_RELEASE_TARGETS);
	const match = all.find((t) => t.platform === platform && t.arch === arch);
	if (match) {
		return [match];
	}
	return [
		{
			platform,
			arch,
			label: platform + " " + arch,
			githubName: platform + " " + arch,
		},
	];
}

let dryRunMode = false;

function run(cmd, args, label) {
	console.log("\n>>", label || [cmd, ...args].join(" "));
	if (dryRunMode) {
		return { status: 0 };
	}
	const r = spawnSync(cmd, args, {
		cwd: REPO_ROOT,
		stdio: "inherit",
		env: process.env,
	});
	return r;
}

function runNodeForge(target) {
	if (!fs.existsSync(FORGE_MAKE)) {
		console.error(
			"Missing Electron Forge. Run: npm install or yarn install in the repo root.",
		);
		process.exit(1);
	}
	return run(
		process.execPath,
		[FORGE_MAKE, "--platform=" + target.platform, "--arch=" + target.arch],
		"electron-forge make " + target.platform + "/" + target.arch,
	);
}

function findArtifacts(version) {
	const found = [];
	if (!fs.existsSync(OUT_MAKE)) {
		return found;
	}
	const versionNeedle = "-" + version;
	function isReleaseArtifact(name) {
		if (!/\.(dmg|zip|deb|rpm)$/i.test(name)) {
			return false;
		}
		if (!name.toLowerCase().startsWith("masonjar")) {
			return false;
		}
		return name.indexOf(versionNeedle) !== -1 || name.indexOf(version) !== -1;
	}
	function walk(dir) {
		let entries;
		try {
			entries = fs.readdirSync(dir, { withFileTypes: true });
		} catch (_e) {
			return;
		}
		for (const ent of entries) {
			const full = path.join(dir, ent.name);
			if (ent.isDirectory()) {
				walk(full);
			} else if (ent.isFile() && isReleaseArtifact(ent.name)) {
				found.push(full);
			}
		}
	}
	walk(OUT_MAKE);
	return [...new Set(found)].sort();
}

/** Electron Forge maker-zip puts app files at zip root; wrap in masonjar-win32-x64/ for extract. */
function windowsZipParentFolderName(zipPath, version) {
	const base = path.basename(zipPath, ".zip");
	const suffix = "-" + version;
	if (base.endsWith(suffix)) {
		return base.slice(0, -suffix.length);
	}
	return base;
}

function isWindowsReleaseZip(zipPath) {
	const rel = path.relative(OUT_MAKE, zipPath).split(path.sep).join("/");
	return (
		/^zip\/win32\/x64\/.*\.zip$/i.test(rel) &&
		!\.wrap-tmp\.zip$/i.test(rel)
	);
}

function createWindowsZipFromFolder(folderPath, zipPath) {
	if (fs.existsSync(zipPath)) {
		fs.unlinkSync(zipPath);
	}
	const parent = path.dirname(folderPath);
	const name = path.basename(folderPath);
	// Windows 10/11 ships bsdtar. Prefer it because Compress-Archive can fail
	// when the PowerShell.Archive module is unavailable in a restricted shell.
	const tarResult = spawnSync(
		"tar",
		["-a", "-c", "-f", zipPath, "-C", parent, name],
		{ stdio: "inherit" },
	);
	if (tarResult.status === 0) {
		return;
	}
	if (process.platform === "win32") {
		const psPath = folderPath.replace(/'/g, "''");
		const psZip = zipPath.replace(/'/g, "''");
		const script =
			"Compress-Archive -LiteralPath '" +
			psPath +
			"' -DestinationPath '" +
			psZip +
			"' -Force";
		const r = spawnSync(
			"powershell.exe",
			["-NoLogo", "-NoProfile", "-Command", script],
			{ stdio: "inherit" },
		);
		if (r.status !== 0) {
			throw new Error("Compress-Archive failed for " + zipPath);
		}
		return;
	}
	if (tarResult.status !== 0) {
		throw new Error("tar failed creating " + zipPath);
	}
}

function ensureWindowsZipRootPackageJson(appFolder) {
	const appPkg = path.join(appFolder, "resources", "app", "package.json");
	const rootPkg = path.join(appFolder, "package.json");
	if (!fs.existsSync(appPkg)) {
		return false;
	}
	fs.copyFileSync(appPkg, rootPkg);
	return true;
}

function moveWindowsZipEntry(sourcePath, destinationPath) {
	try {
		fs.renameSync(sourcePath, destinationPath);
	} catch (err) {
		// Windows can transiently deny a directory rename in %TEMP% while a
		// scanner or indexer has the extracted entry open. Copying within the
		// same temporary workspace preserves the release contents and lets the
		// cleanup in the caller remove the original afterwards.
		if (!err || (err.code !== "EPERM" && err.code !== "EXDEV")) {
			throw err;
		}
		fs.cpSync(sourcePath, destinationPath, { recursive: true, force: true });
		fs.rmSync(sourcePath, { recursive: true, force: true });
	}
}

function wrapWindowsReleaseZipFile(zipPath, version) {
	const parentName = windowsZipParentFolderName(zipPath, version);
	const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mj-zipwrap-"));
	try {
		const extracted = path.join(tmpRoot, "extracted");
		const wrapped = path.join(tmpRoot, parentName);
		fs.mkdirSync(extracted, { recursive: true });
		unzipSync(zipPath, extracted);

		const top = fs.readdirSync(extracted);
		let appFolder;
		if (
			top.length === 1 &&
			top[0] === parentName &&
			fs.statSync(path.join(extracted, top[0])).isDirectory()
		) {
			appFolder = path.join(extracted, parentName);
			console.log("Windows zip already wrapped:", path.basename(zipPath));
		} else {
			fs.mkdirSync(wrapped, { recursive: true });
			for (const ent of top) {
				moveWindowsZipEntry(path.join(extracted, ent), path.join(wrapped, ent));
			}
			appFolder = wrapped;
			console.log("Wrapped Windows zip with top-level folder:", parentName + "/");
		}

		// Compatibility shim: old apply scripts verify installRoot/package.json
		// (next to masonjar.exe). Electron's real file is resources/app/package.json.
		if (ensureWindowsZipRootPackageJson(appFolder)) {
			console.log("Added root package.json shim for updater compatibility");
		} else {
			console.warn(
				"WARN: resources/app/package.json missing; skipped root package.json shim",
			);
		}

		const outTmp = zipPath.replace(/\.zip$/i, "") + ".wrap-tmp.zip";
		if (fs.existsSync(outTmp)) {
			fs.unlinkSync(outTmp);
		}
		createWindowsZipFromFolder(appFolder, outTmp);
		if (fs.existsSync(zipPath)) {
			fs.unlinkSync(zipPath);
		}
		fs.renameSync(outTmp, zipPath);
	} finally {
		fs.rmSync(tmpRoot, { recursive: true, force: true });
	}
}

function wrapWindowsReleaseZips(artifacts, version) {
	for (const zipPath of artifacts) {
		if (!isWindowsReleaseZip(zipPath)) {
			continue;
		}
		wrapWindowsReleaseZipFile(zipPath, version);
	}
}

function writeManifest(version, targets, artifacts) {
	const lines = [
		"# Mason Jar release artifacts",
		"",
		"Version: " + version,
		"Built: " + new Date().toISOString(),
		"Host: " + os.platform() + " " + os.arch(),
		"",
		"## GitHub release checklist",
		"",
		"1. Add human copy: `docs/RELEASE_NOTES.md` section `## v" + version + "` (see `docs/COMMIT_AND_RELEASE.md`).",
		"2. Suggested commit: `node scripts/release-message.js`",
		"3. Tag: `v" + version + "` (must match package.json).",
		"4. Publish: `node scripts/publish-release.js` (Windows zip) or `--all-platforms` for macOS DMGs too.",
		"5. Artifacts: `masonjar-win32-x64-" + version + ".zip`, `masonjar-" + version + "-x64.dmg`, `masonjar-" + version + "-arm64.dmg`",
		"",
		"## Targets built",
		"",
	];
	for (const t of targets) {
		lines.push("- " + t.label + " (`" + t.platform + "/" + t.arch + "`)");
	}
	if (!targets.some((t) => t.platform === "linux")) {
		lines.push(
			"",
			"_Linux builds skipped unless `--linux` (needs dpkg/fakeroot)._",
		);
	}
	lines.push("", "## Files", "");
	if (artifacts.length === 0) {
		lines.push("(none found — check out/make/)");
	} else {
		for (const a of artifacts) {
			const rel = path.relative(REPO_ROOT, a);
			let size = "";
			try {
				size = " (" + Math.round(fs.statSync(a).size / 1024 / 1024) + " MB)";
			} catch (_e) {
				/* ignore */
			}
			lines.push("- `" + rel + "`" + size);
		}
	}
	lines.push("");
	const outPath = path.join(OUT_MAKE, "RELEASE-" + version + ".md");
	fs.mkdirSync(OUT_MAKE, { recursive: true });
	fs.writeFileSync(outPath, lines.join("\n"), "utf8");
	console.log("\nWrote", path.relative(REPO_ROOT, outPath));
	return outPath;
}

function resolveTsc() {
	// Always return a node-runnable entry point: this is invoked via
	// process.execPath (node), so it must be the TypeScript package's JS bin,
	// not the node_modules/.bin/tsc shell shim (a #!/bin/sh script on every
	// platform, including Windows, which `node` cannot parse).
	const pkgBin = path.join(REPO_ROOT, "node_modules", "typescript", "bin", "tsc");
	if (fs.existsSync(pkgBin)) {
		return pkgBin;
	}
	return path.join(REPO_ROOT, "node_modules", ".bin", "tsc");
}

function main() {
	const opts = parseArgs(process.argv);
	dryRunMode = opts.dryRun;

	if (!fs.existsSync(path.join(REPO_ROOT, "node_modules"))) {
		console.error("Run npm install or yarn install before building.");
		process.exit(1);
	}

	const version = readVersion();
	const targets = opts.local ? hostTarget() : releaseTargets(opts);

	console.log("Mason Jar release build v" + version);
	if (opts.local) {
		console.warn(
			"\n*** --local: building for this machine only. NOT sufficient for GitHub release. ***\n",
		);
	} else {
		console.log(
			"\nRelease build targets" +
				(opts.macosOnly
					? " (macOS only):"
					: opts.windowsOnly
						? " (Windows only):"
						: ":"),
		);
		for (const t of targets) {
			console.log("  -", t.label, "→", t.githubName);
		}
	}

	if (opts.dryRun) {
		console.log("\n(dry-run: no compile, tests, or forge)");
		process.exit(0);
	}

	let r = run(process.execPath, [resolveTsc()], "tsc (compile main process)");
	if (r.status !== 0) {
		process.exit(r.status || 1);
	}

	if (!opts.noTest) {
		r = run(process.execPath, [path.join(REPO_ROOT, "scripts", "test-file-index.js")], "test-file-index.js");
		if (r.status !== 0) {
			process.exit(r.status || 1);
		}
		r = run(process.execPath, [path.join(REPO_ROOT, "scripts", "test-pipeline-run.js")], "test-pipeline-run.js");
		if (r.status !== 0) {
			process.exit(r.status || 1);
		}
	}

	const failed = [];
	for (const target of targets) {
		r = runNodeForge(target);
		if (r.status !== 0) {
			failed.push(target.label);
			console.error("FAILED:", target.label);
		}
	}

	const artifacts = findArtifacts(version);
	wrapWindowsReleaseZips(artifacts, version);
	writeManifest(version, targets, artifacts);

	console.log("\n=== Build complete ===\n");
	if (artifacts.length) {
		for (const a of artifacts) {
			console.log(" ", path.relative(REPO_ROOT, a));
		}
	}

	if (failed.length) {
		console.error("\nSome targets failed:", failed.join(", "));
		console.error("Fix errors and re-run. Do not publish a partial release without noting missing platforms.");
		process.exit(1);
	}

	if (!opts.local && artifacts.length < targets.length) {
		console.warn(
			"\nWarning: expected artifacts for",
			targets.length,
			"targets but found",
			artifacts.length,
			"files. Review out/make/ before publishing.",
		);
	}

	if (!opts.local) {
		console.log(
			"\nBefore publish: edit docs/RELEASE_NOTES.md for v" +
				version +
				" (human-facing What's new).",
		);
		try {
			const releaseNotes = require("./release_notes");
			const notes = releaseNotes.readReleaseNotes(version);
			if (notes && notes.whatsNew) {
				console.log("  RELEASE_NOTES.md: section found for v" + version);
			} else {
				console.warn(
					"  WARNING: no **What's new** for v" +
						version +
						" in docs/RELEASE_NOTES.md — publish will fail until you add it.",
				);
			}
		} catch (e) {
			console.warn("  Could not check RELEASE_NOTES.md:", e.message || e);
		}
		console.log(
			"  Commit: node scripts/release-message.js",
		);
		console.log(
			"  Then: tag v" +
				version +
				" → node scripts/publish-release.js. See out/make/RELEASE-" +
				version +
				".md",
		);
	}
}

if (require.main === module) {
	main();
}

module.exports = {
	wrapWindowsReleaseZips,
	wrapWindowsReleaseZipFile,
	windowsZipParentFolderName,
	isWindowsReleaseZip,
	OUT_MAKE,
};
