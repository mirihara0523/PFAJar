"use strict";
var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.UpdateManager = exports.buildCheckResult = exports.expectedWindowsZipName = exports.resolveInstallRoot = exports.isUpdateInProgress = exports.isApplyScriptRunning = exports.buildApplySpawnCommand = exports.CLOSE_OTHER_INSTANCES_MESSAGE = exports.deleteInstallVersionBackups = exports.listInstallVersionBackups = exports.versionBackupDirName = exports.refreshUpdateLockState = exports.writeUpdateLock = exports.releaseUpdateLock = exports.clearOrphanUpdateLock = exports.clearStaleUpdateLock = exports.pathsEqualIgnoreCase = exports.isActiveUpdateLock = exports.isProcessAlive = exports.readUpdateLock = exports.isUpdateLockStale = exports.appendUpdateLogLine = exports.UPDATE_LOCK_STALE_MS = exports.updateFallbackLogPath = exports.updateLogPath = exports.updateLockPath = exports.masonJarTempRoot = exports.countOtherMasonJarInstances = exports.listMasonJarProcesses = exports.countOtherMasonJarInstancesFromList = exports.isElectronHelperProcess = exports.isMandatoryUpdateRequired = exports.compareUpdateAvailable = exports.pickBestRelease = exports.releaseSemver = exports.pickWindowsZipAsset = exports.releaseNotesExcerpt = exports.saveUpdatePreferences = exports.loadUpdatePreferences = exports.updatePreferencesPath = exports.GITHUB_REPO = void 0;
const fs_1 = __importDefault(require("fs"));
const os_1 = __importDefault(require("os"));
const path_1 = __importDefault(require("path"));
const https_1 = __importDefault(require("https"));
const child_process_1 = require("child_process");
const semver = require("semver");
const serverFetch = require("node-fetch");
// Custom builds never check automatically, but an explicit Settings request
// checks this fork's releases rather than the upstream project.
exports.GITHUB_REPO = "mirihara0523-hue/masonjar";
const DEFAULT_PREFS = {
    allow_prerelease: false,
    keep_version_backups: false,
    check_on_startup: false,
};
function updatePreferencesPath(homeDir) {
    return path_1.default.join(homeDir, "update_preferences.json");
}
exports.updatePreferencesPath = updatePreferencesPath;
function loadUpdatePreferences(homeDir) {
    const filePath = updatePreferencesPath(homeDir);
    try {
        if (!fs_1.default.existsSync(filePath)) {
            return Object.assign({}, DEFAULT_PREFS);
        }
        const raw = JSON.parse(fs_1.default.readFileSync(filePath, "utf8"));
        return {
            allow_prerelease: !!raw.allow_prerelease,
            keep_version_backups: !!raw.keep_version_backups,
            check_on_startup: false,
        };
    }
    catch (_a) {
        return Object.assign({}, DEFAULT_PREFS);
    }
}
exports.loadUpdatePreferences = loadUpdatePreferences;
function saveUpdatePreferences(homeDir, patch) {
    const current = loadUpdatePreferences(homeDir);
    const next = {
        allow_prerelease: patch.allow_prerelease != null
            ? !!patch.allow_prerelease
            : current.allow_prerelease,
        keep_version_backups: patch.keep_version_backups != null
            ? !!patch.keep_version_backups
            : current.keep_version_backups,
        check_on_startup: false,
    };
    fs_1.default.mkdirSync(homeDir, { recursive: true });
    fs_1.default.writeFileSync(updatePreferencesPath(homeDir), JSON.stringify(next, null, 2));
    return next;
}
exports.saveUpdatePreferences = saveUpdatePreferences;
function releaseNotesExcerpt(body) {
    const text = String(body || "").trim();
    if (!text) {
        return "";
    }
    const paragraph = text.split(/\n\s*\n/)[0] || text;
    return paragraph.replace(/\r/g, "").trim().slice(0, 600);
}
exports.releaseNotesExcerpt = releaseNotesExcerpt;
function pickWindowsZipAsset(assets, version) {
    if (!assets || !assets.length) {
        return null;
    }
    const expected = `masonjar-win32-x64-${version}.zip`;
    const exact = assets.find((a) => a.name === expected);
    if (exact) {
        return exact;
    }
    const fallback = assets.find((a) => /^masonjar-win32-x64-.+\.zip$/i.test(a.name) &&
        a.browser_download_url);
    return fallback || null;
}
exports.pickWindowsZipAsset = pickWindowsZipAsset;
function releaseSemver(tag) {
    const cleaned = String(tag || "").replace(/^v/i, "");
    return semver.parse(cleaned) || semver.coerce(cleaned);
}
exports.releaseSemver = releaseSemver;
function pickBestRelease(releases) {
    let best = null;
    let bestVer = null;
    for (const rel of releases) {
        if (rel.draft) {
            continue;
        }
        const parsed = releaseSemver(rel.tag_name);
        if (!parsed) {
            continue;
        }
        if (!bestVer || semver.gt(parsed, bestVer)) {
            best = rel;
            bestVer = parsed;
        }
    }
    return best;
}
exports.pickBestRelease = pickBestRelease;
function compareUpdateAvailable(currentVersion, latestVersion) {
    if (!latestVersion) {
        return false;
    }
    const current = semver.coerce(currentVersion);
    const latest = semver.coerce(latestVersion);
    if (!current || !latest) {
        return false;
    }
    return semver.gt(latest, current);
}
exports.compareUpdateAvailable = compareUpdateAvailable;
function isMandatoryUpdateRequired(currentVersion, result) {
    var _a, _b;
    if (result.error || !result.updateAvailable || !result.latest) {
        return false;
    }
    if (result.isPrerelease || ((_a = result.release) === null || _a === void 0 ? void 0 : _a.prerelease) || ((_b = result.release) === null || _b === void 0 ? void 0 : _b.draft)) {
        return false;
    }
    return compareUpdateAvailable(currentVersion, result.latest);
}
exports.isMandatoryUpdateRequired = isMandatoryUpdateRequired;
/** Chromium/Electron child processes always carry --type=… on the command line. */
function isElectronHelperProcess(proc) {
    const cmd = String(proc.commandLine || "");
    return /\s--type=/i.test(cmd);
}
exports.isElectronHelperProcess = isElectronHelperProcess;
function countOtherMasonJarInstancesFromList(processes, myPid, installRoot) {
    const rootNorm = installRoot
        ? path_1.default.normalize(installRoot).replace(/\\/g, "/").toLowerCase()
        : "";
    let count = 0;
    for (const proc of processes) {
        if (proc.pid === myPid) {
            continue;
        }
        if (isElectronHelperProcess(proc)) {
            continue;
        }
        const exeNorm = path_1.default
            .normalize(proc.exePath || "")
            .replace(/\\/g, "/")
            .toLowerCase();
        const cmdNorm = String(proc.commandLine || "")
            .replace(/\\/g, "/")
            .toLowerCase();
        // Without path/cmdline we cannot tell main vs helper — do not block Update Now.
        if (!exeNorm && !cmdNorm.trim()) {
            continue;
        }
        if (!rootNorm) {
            count += 1;
            continue;
        }
        if (exeNorm && exeNorm.startsWith(rootNorm)) {
            count += 1;
            continue;
        }
        // Darwin / incomplete path: match install root in the command line.
        if (!exeNorm && cmdNorm.includes(rootNorm)) {
            count += 1;
        }
    }
    return count;
}
exports.countOtherMasonJarInstancesFromList = countOtherMasonJarInstancesFromList;
function listMasonJarProcessesWindows() {
    try {
        const script = "Get-CimInstance Win32_Process -Filter \"Name='masonjar.exe'\" | " +
            "Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json -Compress";
        const out = (0, child_process_1.execSync)(`powershell -NoProfile -Command ${JSON.stringify(script)}`, {
            encoding: "utf8",
            timeout: 15000,
            windowsHide: true,
        }).trim();
        if (!out) {
            return [];
        }
        const parsed = JSON.parse(out);
        const rows = Array.isArray(parsed) ? parsed : [parsed];
        return rows
            .map((row) => ({
            pid: Number(row.ProcessId),
            exePath: String(row.ExecutablePath || ""),
            commandLine: String(row.CommandLine || ""),
        }))
            .filter((row) => Number.isFinite(row.pid) && row.pid > 0);
    }
    catch (_a) {
        // No CommandLine/path — return empty so we fail open rather than false-block update.
        return [];
    }
}
function listMasonJarProcessesDarwin() {
    try {
        // PID + full args so we can skip Electron --type= helpers.
        const out = (0, child_process_1.execSync)("ps -axo pid=,command= | grep -i '[m]asonjar' || true", {
            encoding: "utf8",
            timeout: 5000,
        }).trim();
        if (!out) {
            return [];
        }
        const rows = [];
        for (const line of out.split(/\r?\n/)) {
            const trimmed = line.trim();
            if (!trimmed) {
                continue;
            }
            const match = /^(\d+)\s+(.*)$/.exec(trimmed);
            if (!match) {
                continue;
            }
            const pid = Number(match[1]);
            const commandLine = match[2] || "";
            if (!Number.isFinite(pid) || pid <= 0) {
                continue;
            }
            // Skip grep/ps noise if any slipped through.
            if (/\bgrep\b/i.test(commandLine) && !/masonjar/i.test(commandLine)) {
                continue;
            }
            rows.push({
                pid,
                exePath: "",
                commandLine,
            });
        }
        return rows;
    }
    catch (_a) {
        return [];
    }
}
function listMasonJarProcesses() {
    if (process.platform === "win32") {
        return listMasonJarProcessesWindows();
    }
    if (process.platform === "darwin") {
        return listMasonJarProcessesDarwin();
    }
    return [];
}
exports.listMasonJarProcesses = listMasonJarProcesses;
function countOtherMasonJarInstances(installRoot, myPid = process.pid, listProcesses = listMasonJarProcesses) {
    return countOtherMasonJarInstancesFromList(listProcesses(), myPid, installRoot);
}
exports.countOtherMasonJarInstances = countOtherMasonJarInstances;
function masonJarTempRoot() {
    return path_1.default.join(os_1.default.tmpdir(), "MasonJar");
}
exports.masonJarTempRoot = masonJarTempRoot;
function updateLockPath() {
    return path_1.default.join(masonJarTempRoot(), "update.lock");
}
exports.updateLockPath = updateLockPath;
function updateLogPath(homeDir) {
    return path_1.default.join(homeDir, "update.log");
}
exports.updateLogPath = updateLogPath;
function updateFallbackLogPath() {
    return path_1.default.join(masonJarTempRoot(), "update-fallback.log");
}
exports.updateFallbackLogPath = updateFallbackLogPath;
exports.UPDATE_LOCK_STALE_MS = 30 * 60 * 1000;
function appendUpdateLogLine(homeDir, message) {
    fs_1.default.mkdirSync(homeDir, { recursive: true });
    const line = `[${new Date().toISOString()}] ${message}\n`;
    fs_1.default.appendFileSync(updateLogPath(homeDir), line, "utf8");
}
exports.appendUpdateLogLine = appendUpdateLogLine;
function isUpdateLockStale(lockPath, maxAgeMs = exports.UPDATE_LOCK_STALE_MS) {
    try {
        if (!fs_1.default.existsSync(lockPath)) {
            return false;
        }
        const stat = fs_1.default.statSync(lockPath);
        return Date.now() - stat.mtimeMs > maxAgeMs;
    }
    catch (_a) {
        return true;
    }
}
exports.isUpdateLockStale = isUpdateLockStale;
function readUpdateLock() {
    const lockPath = updateLockPath();
    try {
        if (!fs_1.default.existsSync(lockPath)) {
            return null;
        }
        const raw = JSON.parse(fs_1.default.readFileSync(lockPath, "utf8"));
        if (!raw || typeof raw.version !== "string") {
            return null;
        }
        return {
            started: String(raw.started || ""),
            version: String(raw.version),
            installRoot: raw.installRoot != null ? String(raw.installRoot) : undefined,
            applyPid: raw.applyPid != null && Number.isFinite(Number(raw.applyPid))
                ? Number(raw.applyPid)
                : undefined,
        };
    }
    catch (_a) {
        return null;
    }
}
exports.readUpdateLock = readUpdateLock;
function isProcessAlive(pid) {
    if (pid == null || !Number.isFinite(pid) || pid <= 0) {
        return false;
    }
    try {
        process.kill(pid, 0);
        return true;
    }
    catch (_a) {
        return false;
    }
}
exports.isProcessAlive = isProcessAlive;
function lockMatchesInstall(payload, installRoot) {
    if (!installRoot || !payload.installRoot) {
        return true;
    }
    return pathsEqualIgnoreCase(payload.installRoot, installRoot);
}
/** True when a non-stale lock exists and apply is actually running. */
function isActiveUpdateLock(installRoot) {
    const lockPath = updateLockPath();
    if (!fs_1.default.existsSync(lockPath) || isUpdateLockStale(lockPath)) {
        return false;
    }
    const payload = readUpdateLock();
    if (!payload) {
        return isApplyScriptRunning();
    }
    if (!lockMatchesInstall(payload, installRoot)) {
        return false;
    }
    if (payload.applyPid != null && isProcessAlive(payload.applyPid)) {
        return true;
    }
    if (isApplyScriptRunning()) {
        return true;
    }
    return false;
}
exports.isActiveUpdateLock = isActiveUpdateLock;
function pathsEqualIgnoreCase(a, b) {
    return (path_1.default.normalize(a).replace(/\\/g, "/").toLowerCase() ===
        path_1.default.normalize(b).replace(/\\/g, "/").toLowerCase());
}
exports.pathsEqualIgnoreCase = pathsEqualIgnoreCase;
function clearStaleUpdateLock() {
    const lockPath = updateLockPath();
    if (!fs_1.default.existsSync(lockPath) || !isUpdateLockStale(lockPath)) {
        return false;
    }
    if (isApplyScriptRunning()) {
        return false;
    }
    const payload = readUpdateLock();
    if ((payload === null || payload === void 0 ? void 0 : payload.applyPid) != null && isProcessAlive(payload.applyPid)) {
        return false;
    }
    try {
        fs_1.default.unlinkSync(lockPath);
        return true;
    }
    catch (_a) {
        return false;
    }
}
exports.clearStaleUpdateLock = clearStaleUpdateLock;
/**
 * Clear a lock only when apply is clearly dead: stale age, or applyPid exited
 * and apply-update.ps1 is not running. Do not clear fresh locks just because
 * Settings opened.
 */
function clearOrphanUpdateLock() {
    const lockPath = updateLockPath();
    if (!fs_1.default.existsSync(lockPath)) {
        return false;
    }
    if (isApplyScriptRunning()) {
        return false;
    }
    const payload = readUpdateLock();
    if ((payload === null || payload === void 0 ? void 0 : payload.applyPid) != null && isProcessAlive(payload.applyPid)) {
        return false;
    }
    if (isUpdateLockStale(lockPath)) {
        try {
            fs_1.default.unlinkSync(lockPath);
            return true;
        }
        catch (_a) {
            return false;
        }
    }
    if ((payload === null || payload === void 0 ? void 0 : payload.applyPid) != null && !isProcessAlive(payload.applyPid)) {
        try {
            const age = Date.now() - fs_1.default.statSync(lockPath).mtimeMs;
            // Allow brief handoff window after spawn before treating as orphan.
            if (age < 60000) {
                return false;
            }
            fs_1.default.unlinkSync(lockPath);
            return true;
        }
        catch (_b) {
            return false;
        }
    }
    return false;
}
exports.clearOrphanUpdateLock = clearOrphanUpdateLock;
function releaseUpdateLock() {
    const lockPath = updateLockPath();
    if (fs_1.default.existsSync(lockPath)) {
        fs_1.default.unlinkSync(lockPath);
    }
}
exports.releaseUpdateLock = releaseUpdateLock;
function writeUpdateLock(version, opts) {
    const lockPath = updateLockPath();
    fs_1.default.mkdirSync(path_1.default.dirname(lockPath), { recursive: true });
    const payload = {
        started: new Date().toISOString(),
        version,
    };
    if (opts === null || opts === void 0 ? void 0 : opts.installRoot) {
        payload.installRoot = opts.installRoot;
    }
    if ((opts === null || opts === void 0 ? void 0 : opts.applyPid) != null && Number.isFinite(opts.applyPid)) {
        payload.applyPid = opts.applyPid;
    }
    fs_1.default.writeFileSync(lockPath, JSON.stringify(payload));
}
exports.writeUpdateLock = writeUpdateLock;
function refreshUpdateLockState() {
    const clearedStale = clearStaleUpdateLock();
    const clearedOrphan = clearOrphanUpdateLock();
    return { clearedStale, clearedOrphan };
}
exports.refreshUpdateLockState = refreshUpdateLockState;
function versionBackupDirName(installRoot, oldVersion) {
    return `${installRoot}.backup-${oldVersion}`;
}
exports.versionBackupDirName = versionBackupDirName;
function listInstallVersionBackups(installRoot) {
    if (!installRoot) {
        return [];
    }
    const parent = path_1.default.dirname(installRoot);
    const base = path_1.default.basename(installRoot);
    const prefix = `${base}.backup-`;
    try {
        if (!fs_1.default.existsSync(parent)) {
            return [];
        }
        return fs_1.default
            .readdirSync(parent, { withFileTypes: true })
            .filter((ent) => ent.isDirectory() && ent.name.startsWith(prefix))
            .map((ent) => path_1.default.join(parent, ent.name))
            .sort((a, b) => a.localeCompare(b));
    }
    catch (_a) {
        return [];
    }
}
exports.listInstallVersionBackups = listInstallVersionBackups;
function deleteInstallVersionBackups(installRoot) {
    const found = listInstallVersionBackups(installRoot);
    const deleted = [];
    const errors = [];
    for (const dir of found) {
        try {
            fs_1.default.rmSync(dir, { recursive: true, force: true });
            deleted.push(dir);
        }
        catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            errors.push(`${dir}: ${msg}`);
        }
    }
    return { ok: errors.length === 0, deleted, errors };
}
exports.deleteInstallVersionBackups = deleteInstallVersionBackups;
exports.CLOSE_OTHER_INSTANCES_MESSAGE = "Please close all other running instances of Mason Jar before updating.";
function buildApplySpawnCommand(scriptPath) {
    // Break away from Electron's Windows Job Object via `cmd /c start` so the
    // apply script survives app.quit(). The short-lived cmd PID is not the apply
    // process — apply-update.ps1 rewrites update.lock with its own $PID on start.
    return {
        command: "cmd.exe",
        args: [
            "/c",
            "start",
            "",
            "/b",
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            scriptPath,
        ],
    };
}
exports.buildApplySpawnCommand = buildApplySpawnCommand;
/** Best-effort: true if apply-update.ps1 is still running (Windows). */
function isApplyScriptRunning() {
    if (process.platform !== "win32") {
        return false;
    }
    try {
        // Match `-File …apply-update.ps1` only — not this detection query's own CommandLine.
        const script = "$p = Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" -ErrorAction SilentlyContinue | " +
            "Where-Object { $_.CommandLine -and ($_.CommandLine -match '(?i)-File\\s+.*apply-update\\.ps1') }; " +
            "if ($p) { '1' } else { '0' }";
        const out = (0, child_process_1.execSync)(`powershell -NoProfile -Command ${JSON.stringify(script)}`, {
            encoding: "utf8",
            timeout: 10000,
            windowsHide: true,
        }).trim();
        return out === "1";
    }
    catch (_a) {
        return false;
    }
}
exports.isApplyScriptRunning = isApplyScriptRunning;
function isUpdateInProgress(installRoot) {
    refreshUpdateLockState();
    return isActiveUpdateLock(installRoot);
}
exports.isUpdateInProgress = isUpdateInProgress;
function resolveInstallRoot(isPackaged) {
    if (!isPackaged) {
        return null;
    }
    return path_1.default.dirname(process.execPath);
}
exports.resolveInstallRoot = resolveInstallRoot;
function expectedWindowsZipName(version) {
    return `masonjar-win32-x64-${version}.zip`;
}
exports.expectedWindowsZipName = expectedWindowsZipName;
function githubHeaders(userAgent) {
    return {
        "User-Agent": userAgent,
        Accept: "application/vnd.github+json",
    };
}
function fetchJson(url, userAgent) {
    return __awaiter(this, void 0, void 0, function* () {
        const response = yield serverFetch(url, { headers: githubHeaders(userAgent) });
        if (!response.ok) {
            return { ok: false, status: response.status };
        }
        const data = (yield response.json());
        return { ok: true, status: response.status, data };
    });
}
function buildCheckResult(currentVersion, release) {
    if (!release) {
        return {
            updateAvailable: false,
            current: currentVersion,
            latest: null,
            isPrerelease: false,
            releaseUrl: null,
            releaseNotesExcerpt: "",
            windowsAsset: null,
            release: null,
        };
    }
    const latestCoerced = semver.coerce(release.tag_name);
    const latest = latestCoerced ? latestCoerced.version : null;
    const windowsAsset = latest != null ? pickWindowsZipAsset(release.assets, latest) : null;
    return {
        updateAvailable: compareUpdateAvailable(currentVersion, latest),
        current: currentVersion,
        latest,
        isPrerelease: !!release.prerelease,
        releaseUrl: release.html_url || null,
        releaseNotesExcerpt: releaseNotesExcerpt(release.body),
        windowsAsset,
        release,
    };
}
exports.buildCheckResult = buildCheckResult;
class UpdateManager {
    constructor(homeDir, currentVersion, isPackaged) {
        this.homeDir = homeDir;
        this.currentVersion = currentVersion;
        this.isPackaged = isPackaged;
        this.cachedCheck = null;
        this.stagedVersion = null;
        this.stagedExtractDir = null;
        this.downloadInFlight = false;
    }
    getCachedCheck() {
        return this.cachedCheck;
    }
    setCachedCheck(result) {
        this.cachedCheck = result;
    }
    getPreferences() {
        return loadUpdatePreferences(this.homeDir);
    }
    savePreferences(patch) {
        return saveUpdatePreferences(this.homeDir, patch);
    }
    checkForUpdatesDetailed(allowPrerelease) {
        return __awaiter(this, void 0, void 0, function* () {
            const prefs = allowPrerelease != null
                ? { allow_prerelease: !!allowPrerelease }
                : this.getPreferences();
            const userAgent = `MasonJar/${this.currentVersion}`;
            try {
                let release = null;
                if (prefs.allow_prerelease) {
                    const url = `https://api.github.com/repos/${exports.GITHUB_REPO}/releases?per_page=30`;
                    const res = yield fetchJson(url, userAgent);
                    if (!res.ok) {
                        const err = Object.assign(Object.assign({}, buildCheckResult(this.currentVersion, null)), { error: `GitHub API returned ${res.status}` });
                        this.cachedCheck = err;
                        return err;
                    }
                    release = pickBestRelease(res.data || []);
                }
                else {
                    const url = `https://api.github.com/repos/${exports.GITHUB_REPO}/releases/latest`;
                    const res = yield fetchJson(url, userAgent);
                    if (res.status === 404) {
                        const empty = buildCheckResult(this.currentVersion, null);
                        this.cachedCheck = empty;
                        return empty;
                    }
                    if (!res.ok || !res.data) {
                        const err = Object.assign(Object.assign({}, buildCheckResult(this.currentVersion, null)), { error: `GitHub API returned ${res.status}` });
                        this.cachedCheck = err;
                        return err;
                    }
                    release = res.data;
                }
                const result = buildCheckResult(this.currentVersion, release);
                this.cachedCheck = result;
                return result;
            }
            catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                const err = Object.assign(Object.assign({}, buildCheckResult(this.currentVersion, null)), { error: msg });
                this.cachedCheck = err;
                return err;
            }
        });
    }
    checkLatestStableRelease() {
        return __awaiter(this, void 0, void 0, function* () {
            const userAgent = `MasonJar/${this.currentVersion}`;
            try {
                const url = `https://api.github.com/repos/${exports.GITHUB_REPO}/releases/latest`;
                const res = yield fetchJson(url, userAgent);
                if (res.status === 404) {
                    const empty = buildCheckResult(this.currentVersion, null);
                    this.cachedCheck = empty;
                    return empty;
                }
                if (!res.ok || !res.data) {
                    const err = Object.assign(Object.assign({}, buildCheckResult(this.currentVersion, null)), { error: `GitHub API returned ${res.status}` });
                    this.cachedCheck = err;
                    return err;
                }
                const result = buildCheckResult(this.currentVersion, res.data);
                this.cachedCheck = result;
                return result;
            }
            catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                const err = Object.assign(Object.assign({}, buildCheckResult(this.currentVersion, null)), { error: msg });
                this.cachedCheck = err;
                return err;
            }
        });
    }
    getApplyInfo() {
        refreshUpdateLockState();
        const installRoot = resolveInstallRoot(this.isPackaged);
        return {
            canApplyInApp: this.isPackaged && process.platform === "win32" && !!installRoot,
            platform: process.platform,
            isPackaged: this.isPackaged,
            installRoot,
            logPath: updateLogPath(this.homeDir),
            stagingReady: !!this.stagedExtractDir && !!this.stagedVersion,
            stagedVersion: this.stagedVersion,
            updateInProgress: isUpdateInProgress(installRoot) || this.downloadInFlight,
        };
    }
    updatesDir() {
        return path_1.default.join(masonJarTempRoot(), "updates");
    }
    zipPathForVersion(version) {
        return path_1.default.join(this.updatesDir(), `masonjar-${version}.zip`);
    }
    extractDirForVersion(version) {
        return path_1.default.join(this.updatesDir(), `extract-${version}`);
    }
    findStagedInstallFolder(extractDir) {
        const direct = path_1.default.join(extractDir, "masonjar-win32-x64");
        if (fs_1.default.existsSync(path_1.default.join(direct, "masonjar.exe"))) {
            return direct;
        }
        if (fs_1.default.existsSync(path_1.default.join(extractDir, "masonjar.exe"))) {
            return extractDir;
        }
        const entries = fs_1.default.readdirSync(extractDir, { withFileTypes: true });
        for (const ent of entries) {
            if (!ent.isDirectory()) {
                continue;
            }
            const candidate = path_1.default.join(extractDir, ent.name);
            if (fs_1.default.existsSync(path_1.default.join(candidate, "masonjar.exe"))) {
                return candidate;
            }
        }
        return null;
    }
    restoreStagedFromDisk(version) {
        if (!version) {
            return false;
        }
        const extractDir = this.extractDirForVersion(version);
        if (!fs_1.default.existsSync(extractDir)) {
            return false;
        }
        const staged = this.findStagedInstallFolder(extractDir);
        if (!staged) {
            return false;
        }
        this.stagedVersion = version;
        this.stagedExtractDir = staged;
        return true;
    }
    isStagingReadyForVersion(version) {
        if (!version || !this.stagedExtractDir || this.stagedVersion !== version) {
            return false;
        }
        return fs_1.default.existsSync(path_1.default.join(this.stagedExtractDir, "masonjar.exe"));
    }
    downloadWindowsUpdate(onProgress) {
        return __awaiter(this, void 0, void 0, function* () {
            if (process.platform !== "win32") {
                return { ok: false, error: "Windows-only download" };
            }
            if (this.downloadInFlight) {
                return { ok: false, error: "Download already in progress" };
            }
            const check = this.cachedCheck;
            if (!(check === null || check === void 0 ? void 0 : check.windowsAsset) || !check.latest) {
                return { ok: false, error: "No Windows update asset available" };
            }
            this.downloadInFlight = true;
            const version = check.latest;
            const zipPath = this.zipPathForVersion(version);
            const extractDir = this.extractDirForVersion(version);
            try {
                fs_1.default.mkdirSync(this.updatesDir(), { recursive: true });
                if (fs_1.default.existsSync(extractDir)) {
                    fs_1.default.rmSync(extractDir, { recursive: true, force: true });
                }
                onProgress(0, "Starting download…");
                yield this.streamDownload(check.windowsAsset.browser_download_url, zipPath, check.windowsAsset.size, onProgress);
                onProgress(95, "Extracting update…");
                yield this.extractZip(zipPath, extractDir);
                const staged = this.findStagedInstallFolder(extractDir);
                if (!staged) {
                    return {
                        ok: false,
                        error: "Extracted package does not contain masonjar.exe",
                    };
                }
                this.stagedVersion = version;
                this.stagedExtractDir = staged;
                onProgress(100, "Ready to install");
                return { ok: true };
            }
            catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                return { ok: false, error: msg };
            }
            finally {
                this.downloadInFlight = false;
            }
        });
    }
    streamDownload(url, target, totalBytesHint, onProgress) {
        return new Promise((resolve, reject) => {
            const file = fs_1.default.createWriteStream(target, { highWaterMark: 64 * 1024 });
            const request = https_1.default.get(url, (response) => {
                if (response.statusCode &&
                    response.statusCode >= 300 &&
                    response.statusCode < 400 &&
                    response.headers.location) {
                    file.close();
                    fs_1.default.unlink(target, () => {
                        this.streamDownload(response.headers.location, target, totalBytesHint, onProgress)
                            .then(resolve)
                            .catch(reject);
                    });
                    return;
                }
                if (response.statusCode !== 200) {
                    reject(new Error(`Download failed: HTTP ${response.statusCode}`));
                    return;
                }
                const totalBytes = parseInt(String(response.headers["content-length"] || "0"), 10) ||
                    totalBytesHint ||
                    0;
                let received = 0;
                let lastPct = -1;
                response.on("data", (chunk) => {
                    received += chunk.length;
                    if (totalBytes > 0) {
                        const pct = Math.min(90, Math.floor((received / totalBytes) * 90));
                        if (pct !== lastPct) {
                            lastPct = pct;
                            onProgress(pct, `Downloading… ${Math.round((received / totalBytes) * 100)}%`);
                        }
                    }
                    else {
                        onProgress(10, `Downloading… ${Math.round(received / 1024 / 1024)} MB`);
                    }
                });
                response.pipe(file);
                file.on("finish", () => {
                    file.close();
                    resolve();
                });
            });
            request.on("error", (err) => {
                fs_1.default.unlink(target, () => reject(err));
            });
            file.on("error", (err) => {
                fs_1.default.unlink(target, () => reject(err));
            });
        });
    }
    extractZip(zipPath, extractDir) {
        fs_1.default.mkdirSync(extractDir, { recursive: true });
        const ps = [
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            `Expand-Archive -LiteralPath '${zipPath.replace(/'/g, "''")}' -DestinationPath '${extractDir.replace(/'/g, "''")}' -Force`,
        ];
        return new Promise((resolve, reject) => {
            const child = (0, child_process_1.spawn)("powershell.exe", ps, { windowsHide: true });
            let stderr = "";
            child.stderr.on("data", (d) => {
                stderr += String(d);
            });
            child.on("error", reject);
            child.on("close", (code) => {
                if (code === 0) {
                    resolve();
                }
                else {
                    reject(new Error(stderr.trim() || `Expand-Archive exited ${code}`));
                }
            });
        });
    }
    writeApplyScript(installRoot, stagingDir, oldVersion, newVersion, keepBackup = false) {
        const scriptPath = path_1.default.join(masonJarTempRoot(), "apply-update.ps1");
        const logPath = updateLogPath(this.homeDir);
        const fallbackLogPath = updateFallbackLogPath();
        const backupDir = versionBackupDirName(installRoot, oldVersion);
        const exePath = path_1.default.join(installRoot, "masonjar.exe");
        const lockPath = updateLockPath();
        // Electron packaged layout: package.json lives under resources/app (app.getAppPath()).
        // Root package.json is only a compatibility shim for older apply scripts / odd layouts.
        const stagingExe = path_1.default.join(stagingDir, "masonjar.exe");
        const keepBackupLiteral = keepBackup ? "$true" : "$false";
        const ps1 = `
$ErrorActionPreference = 'Stop'
$LogPath = '${logPath.replace(/'/g, "''")}'
$FallbackLogPath = '${fallbackLogPath.replace(/'/g, "''")}'
$InstallRoot = '${installRoot.replace(/'/g, "''")}'
$StagingDir = '${stagingDir.replace(/'/g, "''")}'
$StagingExe = '${stagingExe.replace(/'/g, "''")}'
$BackupDir = '${backupDir.replace(/'/g, "''")}'
$ExePath = '${exePath.replace(/'/g, "''")}'
$LockPath = '${lockPath.replace(/'/g, "''")}'
$TargetVersion = '${newVersion.replace(/'/g, "''")}'
$KeepBackup = ${keepBackupLiteral}
$Elevated = $args -contains '-Elevated'

function Write-Log([string]$Message) {
  $line = "[$(Get-Date -Format o)] $Message"
  try {
    $parent = Split-Path -Parent $LogPath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
  } catch {
    try {
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $FallbackLogPath) | Out-Null
      Add-Content -LiteralPath $FallbackLogPath -Value $line -Encoding UTF8
    } catch {
      # best-effort logging only
    }
  }
}

function Register-ApplyLock {
  try {
    $payload = @{
      started = (Get-Date).ToUniversalTime().ToString('o')
      version = $TargetVersion
      installRoot = $InstallRoot
      applyPid = $PID
    } | ConvertTo-Json -Compress
    $parent = Split-Path -Parent $LockPath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Set-Content -LiteralPath $LockPath -Value $payload -Encoding UTF8
    Write-Log "Registered update.lock applyPid=$PID"
  } catch {
    Write-Log "WARN: failed to rewrite update.lock: $($_.Exception.Message)"
  }
}

function Get-InstallProcesses {
  $procs = @()
  try {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='masonjar.exe'" -ErrorAction Stop |
      Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -ieq $ExePath) })
  } catch {
    Write-Log 'WARN: CIM process query failed; falling back to Get-Process by name'
    $procs = @(Get-Process -Name masonjar -ErrorAction SilentlyContinue)
  }
  return @($procs)
}

function Wait-InstallProcesses {
  $deadline = (Get-Date).AddMinutes(5)
  while ((Get-Date) -lt $deadline) {
    $procs = Get-InstallProcesses
    if (-not $procs -or $procs.Count -eq 0) {
      Start-Sleep -Seconds 2
      return
    }
    Start-Sleep -Milliseconds 500
  }
  $still = Get-InstallProcesses
  if ($still -and $still.Count -gt 0) {
    throw "Mason Jar is still running after waiting 5 minutes; aborting update to avoid replacing files in use. Close all instances and try again."
  }
  Start-Sleep -Seconds 2
}

function Assert-UpdatePaths {
  if (-not (Test-Path -LiteralPath $InstallRoot)) {
    throw "Install folder was moved or deleted: $InstallRoot"
  }
  if (-not (Test-Path -LiteralPath $StagingDir)) {
    throw "Staged update folder was moved or deleted: $StagingDir"
  }
  if (-not (Test-Path -LiteralPath $StagingExe)) {
    throw "Staged update is missing masonjar.exe"
  }
}

function Invoke-RobocopyChecked([string]$Source, [string]$Dest, [string]$Label) {
  cmd /c robocopy "$Source" "$Dest" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP
  $rc = $LASTEXITCODE
  Write-Log "$Label robocopy exit code $rc"
  if ($rc -ge 8) {
    throw "$Label robocopy failed with exit code $rc"
  }
}

function Merge-WithRetries {
  param([int]$MaxAttempts = 3)
  for ($i = 1; $i -le $MaxAttempts; $i++) {
    try {
      Assert-UpdatePaths
      Write-Log "Merge attempt $i from $StagingDir"
      Invoke-RobocopyChecked -Source $StagingDir -Dest $InstallRoot -Label "merge"
      return
    } catch {
      Write-Log $_.Exception.Message
      if ($i -ge $MaxAttempts) { throw }
      Start-Sleep -Seconds 2
    }
  }
}

try {
  Write-Log "Apply update process started pid=$PID"
  Register-ApplyLock
  Write-Log "Apply update started (elevated=$Elevated keepBackup=$KeepBackup) ${oldVersion} -> ${newVersion}"
  Wait-InstallProcesses
  Assert-UpdatePaths

  if (-not $Elevated) {
    $probe = Join-Path $InstallRoot '.masonjar_update_write_probe'
    $needsElevation = $false
    try {
      Set-Content -LiteralPath $probe -Value 'ok' -Encoding ASCII -ErrorAction Stop
      Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    } catch {
      $needsElevation = $true
    }
    if ($needsElevation) {
      Write-Log 'Install folder not writable; requesting elevation'
      $elev = Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File', $MyInvocation.MyCommand.Path, '-Elevated') -Wait -PassThru
      if ($null -eq $elev -or $elev.ExitCode -ne 0) {
        $code = if ($null -ne $elev) { $elev.ExitCode } else { 'null' }
        throw "Elevated apply failed or was cancelled (exit=$code)"
      }
      Write-Log "Elevated apply finished with exit code $($elev.ExitCode)"
      exit 0
    }
  }

  Assert-UpdatePaths
  if ($KeepBackup) {
    if (Test-Path -LiteralPath $BackupDir) {
      Remove-Item -LiteralPath $BackupDir -Recurse -Force
    }
    Write-Log "Backing up to $BackupDir"
    Invoke-RobocopyChecked -Source $InstallRoot -Dest $BackupDir -Label "backup"
  } else {
    Write-Log 'Skipping version backup (keep_version_backups=false)'
  }

  Merge-WithRetries

  $appPkg = Join-Path $InstallRoot 'resources\\app\\package.json'
  $rootPkg = Join-Path $InstallRoot 'package.json'
  $PkgPath = $null
  if (Test-Path -LiteralPath $appPkg) {
    $PkgPath = $appPkg
  } elseif (Test-Path -LiteralPath $rootPkg) {
    $PkgPath = $rootPkg
  }

  $mergeOk = $false
  if ($PkgPath) {
    $pkgText = Get-Content -LiteralPath $PkgPath -Raw
    if ($pkgText -notmatch ('"version"\\s*:\\s*"' + [regex]::Escape($TargetVersion) + '"')) {
      Write-Log "ERROR: package.json at $PkgPath after merge does not report version $TargetVersion; not relaunching"
      throw "package.json version mismatch after merge"
    } else {
      Write-Log "Verified package.json version $TargetVersion at $PkgPath"
      $mergeOk = $true
    }
  } else {
    Write-Log 'ERROR: package.json missing after merge (checked resources\\app\\package.json and root); not relaunching'
    throw "package.json missing after merge"
  }

  if (-not $mergeOk) {
    throw "Merge verification failed"
  }

  if (Test-Path -LiteralPath $LockPath) {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    Write-Log 'Released update.lock before relaunch'
  }

  Write-Log 'Relaunching Mason Jar'
  try {
    Start-Process -FilePath $ExePath -WorkingDirectory $InstallRoot
    Write-Log 'Relaunch via Start-Process succeeded'
  } catch {
    Write-Log "Start-Process failed: $($_.Exception.Message); trying cmd start"
    cmd /c start "" "$ExePath"
    Write-Log 'Relaunch via cmd start invoked'
  }
  Write-Log 'Apply update finished successfully'
} catch {
  Write-Log "Apply update failed: $($_.Exception.Message)"
  Write-Log "See update log for details. Re-open Mason Jar and try Update Now, or install the zip manually from GitHub."
  exit 1
} finally {
  if (Test-Path -LiteralPath $LockPath) {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
  }
}
`.trim();
        fs_1.default.mkdirSync(path_1.default.dirname(scriptPath), { recursive: true });
        fs_1.default.writeFileSync(scriptPath, ps1, "utf8");
        return scriptPath;
    }
    refuseIfOtherInstances(installRoot) {
        if (!installRoot) {
            return { ok: true };
        }
        const others = countOtherMasonJarInstances(installRoot);
        if (others > 0) {
            return { ok: false, error: exports.CLOSE_OTHER_INSTANCES_MESSAGE };
        }
        return { ok: true };
    }
    prepareWindowsApply() {
        if (!this.isPackaged || process.platform !== "win32") {
            return {
                ok: false,
                error: "In-app updates apply only to the packaged Windows app.",
            };
        }
        refreshUpdateLockState();
        const installRoot = resolveInstallRoot(this.isPackaged);
        if (isActiveUpdateLock(installRoot)) {
            return { ok: false, error: "Another update is already in progress." };
        }
        const peers = this.refuseIfOtherInstances(installRoot);
        if (!peers.ok) {
            return peers;
        }
        if (!installRoot || !this.stagedExtractDir || !this.stagedVersion) {
            return { ok: false, error: "Download and extract an update first." };
        }
        if (!fs_1.default.existsSync(installRoot)) {
            return {
                ok: false,
                error: "Install folder was moved or deleted.",
            };
        }
        if (!fs_1.default.existsSync(path_1.default.join(this.stagedExtractDir, "masonjar.exe"))) {
            return { ok: false, error: "Staged update is missing masonjar.exe." };
        }
        const prefs = this.getPreferences();
        const scriptPath = this.writeApplyScript(installRoot, this.stagedExtractDir, this.currentVersion, this.stagedVersion, !!prefs.keep_version_backups);
        return { ok: true, scriptPath, stagedVersion: this.stagedVersion };
    }
    launchApplyAndQuit(scriptPath, stagedVersion, quit) {
        const installRoot = resolveInstallRoot(this.isPackaged);
        appendUpdateLogLine(this.homeDir, `Preparing apply: script=${scriptPath} installRoot=${installRoot || "?"}`);
        appendUpdateLogLine(this.homeDir, `Fallback log path: ${updateFallbackLogPath()}`);
        return new Promise((resolve) => {
            const { command, args } = buildApplySpawnCommand(scriptPath);
            let settled = false;
            const finish = (result) => {
                if (settled) {
                    return;
                }
                settled = true;
                resolve(result);
            };
            try {
                const child = (0, child_process_1.spawn)(command, args, {
                    detached: true,
                    stdio: "ignore",
                    windowsHide: true,
                });
                child.on("error", (err) => {
                    appendUpdateLogLine(this.homeDir, `Spawn failed: ${err.message}`);
                    releaseUpdateLock();
                    finish({ ok: false, error: err.message });
                });
                child.unref();
                if (!child.pid) {
                    appendUpdateLogLine(this.homeDir, "Spawn failed: no PID returned");
                    releaseUpdateLock();
                    finish({ ok: false, error: "Failed to start updater process" });
                    return;
                }
                writeUpdateLock(stagedVersion, {
                    installRoot,
                    applyPid: child.pid,
                });
                appendUpdateLogLine(this.homeDir, `Updater breakaway launcher pid=${child.pid} (apply-update.ps1 will rewrite lock with its own PID)`);
                setTimeout(() => {
                    if (settled) {
                        return;
                    }
                    quit();
                    finish({ ok: true });
                }, 1000);
            }
            catch (error) {
                const msg = error instanceof Error ? error.message : String(error);
                appendUpdateLogLine(this.homeDir, `Spawn exception: ${msg}`);
                releaseUpdateLock();
                finish({ ok: false, error: msg });
            }
        });
    }
    runWindowsUpdateNow(onProgress, quit) {
        var _a;
        return __awaiter(this, void 0, void 0, function* () {
            const lockState = refreshUpdateLockState();
            const installRoot = resolveInstallRoot(this.isPackaged);
            if (isActiveUpdateLock(installRoot)) {
                return {
                    ok: false,
                    error: "Another update is already in progress.",
                    lockCleared: lockState.clearedStale || lockState.clearedOrphan,
                };
            }
            const peers = this.refuseIfOtherInstances(installRoot);
            if (!peers.ok) {
                return Object.assign(Object.assign({}, peers), { lockCleared: lockState.clearedStale || lockState.clearedOrphan });
            }
            const latest = (_a = this.cachedCheck) === null || _a === void 0 ? void 0 : _a.latest;
            if (!this.isStagingReadyForVersion(latest)) {
                this.restoreStagedFromDisk(latest);
            }
            if (!this.isStagingReadyForVersion(latest)) {
                const download = yield this.downloadWindowsUpdate(onProgress);
                if (!download.ok) {
                    releaseUpdateLock();
                    return Object.assign(Object.assign({}, download), { lockCleared: lockState.clearedStale || lockState.clearedOrphan });
                }
            }
            else {
                onProgress(100, "Using downloaded update…");
            }
            onProgress(100, "Installing update…");
            const prepared = this.prepareWindowsApply();
            if (!prepared.ok) {
                releaseUpdateLock();
                return Object.assign(Object.assign({}, prepared), { lockCleared: lockState.clearedStale || lockState.clearedOrphan });
            }
            const applied = yield this.launchApplyAndQuit(prepared.scriptPath, prepared.stagedVersion || this.stagedVersion || latest || "", quit);
            if (!applied.ok) {
                releaseUpdateLock();
            }
            return Object.assign(Object.assign({}, applied), { lockCleared: lockState.clearedStale || lockState.clearedOrphan });
        });
    }
}
exports.UpdateManager = UpdateManager;
