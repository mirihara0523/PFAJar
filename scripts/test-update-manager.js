"use strict";

const path = require("path");

const repoRoot = path.join(__dirname, "..");
const updateManager = require(path.join(repoRoot, "update_manager.js"));

function assert(cond, msg) {
	if (!cond) {
		throw new Error(msg);
	}
}

function testPickWindowsZipAsset() {
	const assets = [
		{
			name: "masonjar-win32-x64-6.0.0.zip",
			browser_download_url: "https://example.com/a.zip",
			size: 100,
		},
		{ name: "checksums.txt", browser_download_url: "https://example.com/c", size: 1 },
	];
	const picked = updateManager.pickWindowsZipAsset(assets, "6.0.0");
	assert(picked && picked.name === "masonjar-win32-x64-6.0.0.zip", "exact zip name");
	const fallback = updateManager.pickWindowsZipAsset(
		[{ name: "masonjar-win32-x64-6.0.1.zip", browser_download_url: "u", size: 1 }],
		"6.0.0",
	);
	assert(fallback && fallback.name.includes("masonjar-win32-x64"), "fallback zip");
}

function testCompareUpdateAvailable() {
	assert(updateManager.compareUpdateAvailable("5.0.10", "6.0.0"), "6 > 5");
	assert(!updateManager.compareUpdateAvailable("6.0.0", "6.0.0"), "equal");
	assert(!updateManager.compareUpdateAvailable("6.1.0", "6.0.0"), "no downgrade");
}

function testPickBestRelease() {
	const releases = [
		{
			tag_name: "v5.0.10",
			html_url: "https://github.com/a/r5",
			body: "",
			prerelease: false,
			draft: false,
			assets: [],
		},
		{
			tag_name: "v6.0.0-beta.1",
			html_url: "https://github.com/a/r6b",
			body: "",
			prerelease: true,
			draft: false,
			assets: [],
		},
		{
			tag_name: "v6.0.0",
			html_url: "https://github.com/a/r6",
			body: "",
			prerelease: false,
			draft: false,
			assets: [],
		},
		{
			tag_name: "v7.0.0-draft",
			html_url: "https://github.com/a/rd",
			body: "",
			prerelease: false,
			draft: true,
			assets: [],
		},
	];
	const best = updateManager.pickBestRelease(releases);
	assert(best && best.tag_name === "v6.0.0", "highest non-draft semver");
}

function testBuildCheckResult() {
	const release = {
		tag_name: "v6.0.0",
		html_url: "https://github.com/a/r6",
		body: "What's new\n\nMore text",
		prerelease: true,
		draft: false,
		assets: [
			{
				name: "masonjar-win32-x64-6.0.0.zip",
				browser_download_url: "https://example.com/a.zip",
				size: 100,
			},
		],
	};
	const result = updateManager.buildCheckResult("5.0.10", release);
	assert(result.updateAvailable, "update available");
	assert(result.latest === "6.0.0", "latest version");
	assert(result.isPrerelease, "prerelease flag");
	assert(result.windowsAsset != null, "windows asset");
	assert(
		result.releaseNotesExcerpt.indexOf("What's new") === 0,
		"notes excerpt first paragraph",
	);
}

function testReleaseNotesExcerpt() {
	const excerpt = updateManager.releaseNotesExcerpt("Line one\n\nLine two");
	assert(excerpt === "Line one", "first paragraph only");
}

function testExpectedWindowsZipName() {
	assert(
		updateManager.expectedWindowsZipName("6.0.0") ===
			"masonjar-win32-x64-6.0.0.zip",
		"zip naming",
	);
}

function testUpdatePreferencesRoundTrip() {
	const os = require("os");
	const fs = require("fs");
	const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "mj-update-prefs-"));
	try {
		const loaded = updateManager.loadUpdatePreferences(tmpHome);
		assert(!loaded.allow_prerelease, "default prerelease off");
		assert(!loaded.keep_version_backups, "default backups off");
		const saved = updateManager.saveUpdatePreferences(tmpHome, {
			allow_prerelease: true,
			keep_version_backups: true,
		});
		assert(saved.allow_prerelease, "saved prerelease on");
		assert(saved.keep_version_backups, "saved backups on");
		const again = updateManager.loadUpdatePreferences(tmpHome);
		assert(again.allow_prerelease, "read back prerelease on");
		assert(again.keep_version_backups, "read back backups on");
		const partial = updateManager.saveUpdatePreferences(tmpHome, {
			allow_prerelease: false,
		});
		assert(!partial.allow_prerelease, "prerelease cleared");
		assert(partial.keep_version_backups, "backups preserved on partial save");
	} finally {
		fs.rmSync(tmpHome, { recursive: true, force: true });
	}
}

function testBuildApplySpawnCommand() {
	const spec = updateManager.buildApplySpawnCommand("C:\\Temp\\apply-update.ps1");
	assert(spec.command === "cmd.exe", "cmd breakaway launcher");
	assert(spec.args[0] === "/c", "cmd /c");
	assert(spec.args.includes("start"), "start breakaway");
	assert(spec.args.includes("/b"), "start /b");
	assert(spec.args.includes("powershell.exe"), "powershell after start");
	assert(spec.args.includes("-File"), "file arg");
	assert(spec.args.includes("-WindowStyle"), "hidden window");
	assert(
		spec.args[spec.args.length - 1] === "C:\\Temp\\apply-update.ps1",
		"script path last arg",
	);
}

function testAppendUpdateLogLine() {
	const os = require("os");
	const fs = require("fs");
	const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "mj-update-log-"));
	try {
		updateManager.appendUpdateLogLine(tmpHome, "test line");
		const logPath = updateManager.updateLogPath(tmpHome);
		assert(fs.existsSync(logPath), "log file created");
		const text = fs.readFileSync(logPath, "utf8");
		assert(text.indexOf("test line") >= 0, "log content");
	} finally {
		fs.rmSync(tmpHome, { recursive: true, force: true });
	}
}

function testUpdateLockLifecycle() {
	const os = require("os");
	const fs = require("fs");
	const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "mj-update-lock-"));
	const staging = path.join(tmpHome, "staging");
	fs.mkdirSync(staging, { recursive: true });
	fs.writeFileSync(path.join(staging, "masonjar.exe"), "");
	const lockPath = updateManager.updateLockPath();
	fs.mkdirSync(path.dirname(lockPath), { recursive: true });
	try {
		const mgr = new updateManager.UpdateManager(tmpHome, "6.0.3", true);
		mgr.stagedExtractDir = staging;
		mgr.stagedVersion = "6.0.4";
		if (process.platform === "win32") {
			const prepared = mgr.prepareWindowsApply();
			assert(prepared.ok, "prepare without pre-write lock");
			assert(!fs.existsSync(lockPath), "prepare does not write lock");
		}

		updateManager.writeUpdateLock("6.0.4", {
			installRoot: "C:\\Apps\\masonjar",
		});
		assert(fs.existsSync(lockPath), "writeUpdateLock creates lock");
		const payload = updateManager.readUpdateLock();
		assert(payload && payload.version === "6.0.4", "readUpdateLock version");
		assert(
			payload.installRoot === "C:\\Apps\\masonjar",
			"readUpdateLock installRoot",
		);
		updateManager.releaseUpdateLock();
		assert(!fs.existsSync(lockPath), "releaseUpdateLock clears lock");

		// Fresh lock with no live apply is not "active" (no invisible timer).
		updateManager.writeUpdateLock("6.0.4");
		assert(
			!updateManager.isActiveUpdateLock("C:\\Apps\\masonjar"),
			"fresh lock without apply is not active",
		);

		updateManager.writeUpdateLock("6.0.4");
		const staleTime = Date.now() - updateManager.UPDATE_LOCK_STALE_MS - 1000;
		fs.utimesSync(lockPath, staleTime / 1000, staleTime / 1000);
		assert(updateManager.clearStaleUpdateLock(), "clearStaleUpdateLock");
		assert(!fs.existsSync(lockPath), "stale lock removed");

		// Fresh lock without applyPid must NOT be cleared as orphan (Settings open).
		updateManager.writeUpdateLock("6.0.4");
		const freshTime = Date.now() - 5000;
		fs.utimesSync(lockPath, freshTime / 1000, freshTime / 1000);
		assert(
			!updateManager.clearOrphanUpdateLock(),
			"fresh lock without dead applyPid is not orphan-cleared",
		);
		assert(fs.existsSync(lockPath), "fresh lock still present");

		// Dead applyPid + age past handoff window → clear orphan.
		updateManager.writeUpdateLock("6.0.4", { applyPid: 2147483000 });
		const orphanTime = Date.now() - 120_000;
		fs.utimesSync(lockPath, orphanTime / 1000, orphanTime / 1000);
		assert(
			updateManager.clearOrphanUpdateLock(),
			"clearOrphanUpdateLock for dead applyPid",
		);
		assert(!fs.existsSync(lockPath), "orphan lock removed");
	} finally {
		updateManager.releaseUpdateLock();
		fs.rmSync(tmpHome, { recursive: true, force: true });
	}
}

function testApplyScriptContent() {
	const os = require("os");
	const fs = require("fs");
	const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "mj-update-script-"));
	const installRoot = path.join(tmpHome, "install");
	const staging = path.join(tmpHome, "staging");
	fs.mkdirSync(installRoot, { recursive: true });
	fs.mkdirSync(staging, { recursive: true });
	fs.writeFileSync(path.join(staging, "masonjar.exe"), "");
	try {
		const mgr = new updateManager.UpdateManager(tmpHome, "6.0.0", true);
		const scriptPath = mgr.writeApplyScript(
			installRoot,
			staging,
			"6.0.0",
			"6.0.2",
			false,
		);
		const ps1 = fs.readFileSync(scriptPath, "utf8");
		assert(ps1.indexOf("Win32_Process") >= 0, "CIM process wait");
		assert(ps1.indexOf("Merge-WithRetries") >= 0, "merge retries");
		assert(ps1.indexOf(".Path -eq") < 0, "no Get-Process Path filter");
		assert(ps1.indexOf("FallbackLogPath") >= 0, "fallback log");
		assert(ps1.indexOf("Assert-UpdatePaths") >= 0, "path preflight");
		assert(
			ps1.indexOf("still running after waiting 5 minutes") >= 0,
			"fail-closed wait",
		);
		assert(
			ps1.indexOf("Released update.lock before relaunch") >= 0,
			"clears lock before relaunch",
		);
		assert(ps1.indexOf("$KeepBackup = $false") >= 0, "backup off by default");
		assert(
			ps1.indexOf("Skipping version backup") >= 0,
			"skip backup log when off",
		);
		assert(ps1.indexOf("Register-ApplyLock") >= 0, "lock rewrite helper");
		assert(ps1.indexOf("applyPid = $PID") >= 0, "lock uses PowerShell PID");
		assert(
			ps1.indexOf("Apply update process started pid=$PID") >= 0,
			"early survival log",
		);
		assert(ps1.indexOf("-PassThru") >= 0, "elevation PassThru");
		assert(
			ps1.indexOf("Elevated apply failed or was cancelled") >= 0,
			"elevation exit-code check",
		);
		assert(
			ps1.indexOf("Apply update failed:") >= 0,
			"clear failure logging",
		);
		assert(
			ps1.indexOf("resources\\app\\package.json") >= 0 ||
				ps1.indexOf("resources\\\\app\\\\package.json") >= 0,
			"verifies Electron resources/app/package.json",
		);
		assert(
			ps1.indexOf("Join-Path $InstallRoot 'resources") >= 0,
			"resolves app package.json via Join-Path",
		);
		assert(
			ps1.indexOf("checked resources") >= 0,
			"missing-pkg error mentions both candidate paths",
		);
		// Must not hard-code only installRoot/package.json as the sole verify path.
		assert(
			!/\$PkgPath = '[^']*package\.json'/.test(ps1) ||
				ps1.indexOf("resources") >= 0,
			"does not sole-bind root package.json as PkgPath",
		);

		const withBackup = mgr.writeApplyScript(
			installRoot,
			staging,
			"6.0.0",
			"6.0.2",
			true,
		);
		const ps1b = fs.readFileSync(withBackup, "utf8");
		assert(ps1b.indexOf("$KeepBackup = $true") >= 0, "backup on when requested");
		assert(ps1b.indexOf("Backing up to") >= 0, "backup robocopy path");
	} finally {
		fs.rmSync(tmpHome, { recursive: true, force: true });
	}
}

function testVersionBackupHelpers() {
	const os = require("os");
	const fs = require("fs");
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "mj-update-bak-"));
	const installRoot = path.join(tmp, "masonjar-win32-x64-6.0.16");
	fs.mkdirSync(installRoot, { recursive: true });
	const bak1 = `${installRoot}.backup-6.0.15`;
	const bak2 = `${installRoot}.backup-6.0.14`;
	const decoy = path.join(tmp, "other.backup-6.0.15");
	fs.mkdirSync(bak1, { recursive: true });
	fs.mkdirSync(bak2, { recursive: true });
	fs.mkdirSync(decoy, { recursive: true });
	try {
		const listed = updateManager.listInstallVersionBackups(installRoot);
		assert(listed.length === 2, "lists two matching backups");
		assert(
			listed.every(function (p) {
				return p.indexOf("masonjar-win32-x64-6.0.16.backup-") >= 0;
			}),
			"only same install basename backups",
		);
		const del = updateManager.deleteInstallVersionBackups(installRoot);
		assert(del.ok, "delete ok");
		assert(del.deleted.length === 2, "deleted both");
		assert(
			updateManager.listInstallVersionBackups(installRoot).length === 0,
			"none left",
		);
		assert(fs.existsSync(decoy), "unrelated decoy kept");
	} finally {
		fs.rmSync(tmp, { recursive: true, force: true });
	}
}

function testIsMandatoryUpdateRequired() {
	const stableNewer = updateManager.buildCheckResult("6.0.13", {
		tag_name: "v6.0.14",
		html_url: "https://github.com/a/r",
		body: "",
		prerelease: false,
		draft: false,
		assets: [],
	});
	assert(
		updateManager.isMandatoryUpdateRequired("6.0.13", stableNewer),
		"stable newer requires mandatory update",
	);
	assert(
		!updateManager.isMandatoryUpdateRequired("6.0.14", stableNewer),
		"equal version not mandatory",
	);
	const prerelease = updateManager.buildCheckResult("6.0.13", {
		tag_name: "v6.0.14-beta",
		html_url: "https://github.com/a/r",
		body: "",
		prerelease: true,
		draft: false,
		assets: [],
	});
	assert(
		!updateManager.isMandatoryUpdateRequired("6.0.13", prerelease),
		"prerelease not mandatory",
	);
	const withError = Object.assign({}, stableNewer, { error: "offline" });
	assert(
		!updateManager.isMandatoryUpdateRequired("6.0.13", withError),
		"error skips mandatory",
	);
}

function testCustomBuildUpdateDefaults() {
	assert(
		updateManager.GITHUB_REPO === "mirihara0523-hue/masonjar",
		"manual update checks use the custom-build fork",
	);
}

function testCountOtherMasonJarInstancesFromList() {
	const root = "C:\\Apps\\masonjar-win32-x64";
	const list = [
		{
			pid: 100,
			exePath: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
			commandLine: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
		},
		{
			pid: 200,
			exePath: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
			commandLine: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
		},
		{
			pid: 300,
			exePath: "D:\\Other\\masonjar.exe",
			commandLine: "D:\\Other\\masonjar.exe",
		},
	];
	assert(
		updateManager.countOtherMasonJarInstancesFromList(list, 100, root) === 1,
		"counts same-install-root excluding self",
	);
	assert(
		updateManager.countOtherMasonJarInstancesFromList(list, 100, null) === 2,
		"without install root counts all other mains",
	);
	assert(
		updateManager.countOtherMasonJarInstancesFromList(list, 100, root) === 1,
		"ignores different install root",
	);

	const withHelpers = [
		{
			pid: 100,
			exePath: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
			commandLine: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
		},
		{
			pid: 101,
			exePath: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
			commandLine:
				"C:\\Apps\\masonjar-win32-x64\\masonjar.exe --type=renderer --foo",
		},
		{
			pid: 102,
			exePath: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
			commandLine:
				"C:\\Apps\\masonjar-win32-x64\\masonjar.exe --type=gpu-process",
		},
		{
			pid: 200,
			exePath: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
			commandLine: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
		},
	];
	assert(
		updateManager.countOtherMasonJarInstancesFromList(withHelpers, 100, root) ===
			1,
		"ignores Electron --type= helpers; counts second main",
	);
	assert(
		updateManager.countOtherMasonJarInstancesFromList(
			[
				{
					pid: 100,
					exePath: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
					commandLine: "C:\\Apps\\masonjar-win32-x64\\masonjar.exe",
				},
				{ pid: 101, exePath: "", commandLine: "" },
			],
			100,
			root,
		) === 0,
		"empty path/cmdline does not count as peer",
	);
	assert(
		updateManager.isElectronHelperProcess({
			pid: 1,
			exePath: "x",
			commandLine: "masonjar.exe --type=utility",
		}),
		"detects --type= helper",
	);
}

function run() {
	testPickWindowsZipAsset();
	testCompareUpdateAvailable();
	testPickBestRelease();
	testBuildCheckResult();
	testReleaseNotesExcerpt();
	testExpectedWindowsZipName();
	testUpdatePreferencesRoundTrip();
	testBuildApplySpawnCommand();
	testAppendUpdateLogLine();
	testUpdateLockLifecycle();
	testApplyScriptContent();
	testVersionBackupHelpers();
	testIsMandatoryUpdateRequired();
	testCustomBuildUpdateDefaults();
	testCountOtherMasonJarInstancesFromList();
	console.log("test-update-manager: ok");
}

run();
