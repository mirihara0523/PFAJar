import fs from "fs";
import os from "os";
import path from "path";
import https from "https";
import { spawn, execSync } from "child_process";

const semver = require("semver");
const serverFetch = require("node-fetch");

// Custom builds never check automatically, but an explicit Settings request
// checks this fork's releases rather than the upstream project.
export const GITHUB_REPO = "mirihara0523-hue/masonjar";

export interface UpdatePreferences {
  allow_prerelease: boolean;
  keep_version_backups: boolean;
  check_on_startup: boolean;
}

export interface UpdateLockPayload {
  started: string;
  version: string;
  installRoot?: string;
  applyPid?: number;
}

export interface ReleaseAsset {
  name: string;
  browser_download_url: string;
  size: number;
}

export interface GitHubRelease {
  tag_name: string;
  html_url: string;
  body: string;
  prerelease: boolean;
  draft: boolean;
  assets: ReleaseAsset[];
}

export interface UpdateCheckResult {
  updateAvailable: boolean;
  current: string;
  latest: string | null;
  isPrerelease: boolean;
  releaseUrl: string | null;
  releaseNotesExcerpt: string;
  windowsAsset: ReleaseAsset | null;
  release: GitHubRelease | null;
  error?: string;
}

export interface UpdateApplyInfo {
  canApplyInApp: boolean;
  platform: NodeJS.Platform;
  isPackaged: boolean;
  installRoot: string | null;
  logPath: string;
  stagingReady: boolean;
  stagedVersion: string | null;
  updateInProgress: boolean;
}

const DEFAULT_PREFS: UpdatePreferences = {
  allow_prerelease: false,
  keep_version_backups: false,
  check_on_startup: false,
};

export function updatePreferencesPath(homeDir: string): string {
  return path.join(homeDir, "update_preferences.json");
}

export function loadUpdatePreferences(homeDir: string): UpdatePreferences {
  const filePath = updatePreferencesPath(homeDir);
  try {
    if (!fs.existsSync(filePath)) {
      return { ...DEFAULT_PREFS };
    }
    const raw = JSON.parse(fs.readFileSync(filePath, "utf8")) as Partial<UpdatePreferences>;
    return {
      allow_prerelease: !!raw.allow_prerelease,
      keep_version_backups: !!raw.keep_version_backups,
      check_on_startup: false,
    };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export function saveUpdatePreferences(
  homeDir: string,
  patch: Partial<UpdatePreferences>,
): UpdatePreferences {
  const current = loadUpdatePreferences(homeDir);
  const next: UpdatePreferences = {
    allow_prerelease:
      patch.allow_prerelease != null
        ? !!patch.allow_prerelease
        : current.allow_prerelease,
    keep_version_backups:
      patch.keep_version_backups != null
        ? !!patch.keep_version_backups
        : current.keep_version_backups,
    check_on_startup: false,
  };
  fs.mkdirSync(homeDir, { recursive: true });
  fs.writeFileSync(updatePreferencesPath(homeDir), JSON.stringify(next, null, 2));
  return next;
}

export function releaseNotesExcerpt(body: string | null | undefined): string {
  const text = String(body || "").trim();
  if (!text) {
    return "";
  }
  const paragraph = text.split(/\n\s*\n/)[0] || text;
  return paragraph.replace(/\r/g, "").trim().slice(0, 600);
}

export function pickWindowsZipAsset(
  assets: ReleaseAsset[] | undefined,
  version: string,
): ReleaseAsset | null {
  if (!assets || !assets.length) {
    return null;
  }
  const expected = `masonjar-win32-x64-${version}.zip`;
  const exact = assets.find((a) => a.name === expected);
  if (exact) {
    return exact;
  }
  const fallback = assets.find(
    (a) =>
      /^masonjar-win32-x64-.+\.zip$/i.test(a.name) &&
      a.browser_download_url,
  );
  return fallback || null;
}

export function releaseSemver(tag: string): ReturnType<typeof semver.parse> {
  const cleaned = String(tag || "").replace(/^v/i, "");
  return semver.parse(cleaned) || semver.coerce(cleaned);
}

export function pickBestRelease(
  releases: GitHubRelease[],
): GitHubRelease | null {
  let best: GitHubRelease | null = null;
  let bestVer: ReturnType<typeof semver.parse> = null;
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

export function compareUpdateAvailable(
  currentVersion: string,
  latestVersion: string | null,
): boolean {
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

export function isMandatoryUpdateRequired(
  currentVersion: string,
  result: UpdateCheckResult,
): boolean {
  if (result.error || !result.updateAvailable || !result.latest) {
    return false;
  }
  if (result.isPrerelease || result.release?.prerelease || result.release?.draft) {
    return false;
  }
  return compareUpdateAvailable(currentVersion, result.latest);
}

export type MasonJarProcessInfo = {
  pid: number;
  exePath: string;
  /** Full command line when available (used to skip Electron --type= helpers). */
  commandLine?: string;
};

/** Chromium/Electron child processes always carry --type=… on the command line. */
export function isElectronHelperProcess(proc: MasonJarProcessInfo): boolean {
  const cmd = String(proc.commandLine || "");
  return /\s--type=/i.test(cmd);
}

export function countOtherMasonJarInstancesFromList(
  processes: MasonJarProcessInfo[],
  myPid: number,
  installRoot?: string | null,
): number {
  const rootNorm = installRoot
    ? path.normalize(installRoot).replace(/\\/g, "/").toLowerCase()
    : "";
  let count = 0;
  for (const proc of processes) {
    if (proc.pid === myPid) {
      continue;
    }
    if (isElectronHelperProcess(proc)) {
      continue;
    }
    const exeNorm = path
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

function listMasonJarProcessesWindows(): MasonJarProcessInfo[] {
  try {
    const script =
      "Get-CimInstance Win32_Process -Filter \"Name='masonjar.exe'\" | " +
      "Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json -Compress";
    const out = execSync(
      `powershell -NoProfile -Command ${JSON.stringify(script)}`,
      {
        encoding: "utf8",
        timeout: 15000,
        windowsHide: true,
      },
    ).trim();
    if (!out) {
      return [];
    }
    const parsed = JSON.parse(out) as
      | {
          ProcessId?: number;
          ExecutablePath?: string;
          CommandLine?: string;
        }
      | Array<{
          ProcessId?: number;
          ExecutablePath?: string;
          CommandLine?: string;
        }>;
    const rows = Array.isArray(parsed) ? parsed : [parsed];
    return rows
      .map((row) => ({
        pid: Number(row.ProcessId),
        exePath: String(row.ExecutablePath || ""),
        commandLine: String(row.CommandLine || ""),
      }))
      .filter((row) => Number.isFinite(row.pid) && row.pid > 0);
  } catch {
    // No CommandLine/path — return empty so we fail open rather than false-block update.
    return [];
  }
}

function listMasonJarProcessesDarwin(): MasonJarProcessInfo[] {
  try {
    // PID + full args so we can skip Electron --type= helpers.
    const out = execSync(
      "ps -axo pid=,command= | grep -i '[m]asonjar' || true",
      {
        encoding: "utf8",
        timeout: 5000,
      },
    ).trim();
    if (!out) {
      return [];
    }
    const rows: MasonJarProcessInfo[] = [];
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
  } catch {
    return [];
  }
}

export function listMasonJarProcesses(): MasonJarProcessInfo[] {
  if (process.platform === "win32") {
    return listMasonJarProcessesWindows();
  }
  if (process.platform === "darwin") {
    return listMasonJarProcessesDarwin();
  }
  return [];
}

export function countOtherMasonJarInstances(
  installRoot?: string | null,
  myPid: number = process.pid,
  listProcesses: () => MasonJarProcessInfo[] = listMasonJarProcesses,
): number {
  return countOtherMasonJarInstancesFromList(
    listProcesses(),
    myPid,
    installRoot,
  );
}

export function masonJarTempRoot(): string {
  return path.join(os.tmpdir(), "MasonJar");
}

export function updateLockPath(): string {
  return path.join(masonJarTempRoot(), "update.lock");
}

export function updateLogPath(homeDir: string): string {
  return path.join(homeDir, "update.log");
}

export function updateFallbackLogPath(): string {
  return path.join(masonJarTempRoot(), "update-fallback.log");
}

export const UPDATE_LOCK_STALE_MS = 30 * 60 * 1000;

export function appendUpdateLogLine(homeDir: string, message: string): void {
  fs.mkdirSync(homeDir, { recursive: true });
  const line = `[${new Date().toISOString()}] ${message}\n`;
  fs.appendFileSync(updateLogPath(homeDir), line, "utf8");
}

export function isUpdateLockStale(
  lockPath: string,
  maxAgeMs: number = UPDATE_LOCK_STALE_MS,
): boolean {
  try {
    if (!fs.existsSync(lockPath)) {
      return false;
    }
    const stat = fs.statSync(lockPath);
    return Date.now() - stat.mtimeMs > maxAgeMs;
  } catch {
    return true;
  }
}

export function readUpdateLock(): UpdateLockPayload | null {
  const lockPath = updateLockPath();
  try {
    if (!fs.existsSync(lockPath)) {
      return null;
    }
    const raw = JSON.parse(fs.readFileSync(lockPath, "utf8")) as Partial<UpdateLockPayload>;
    if (!raw || typeof raw.version !== "string") {
      return null;
    }
    return {
      started: String(raw.started || ""),
      version: String(raw.version),
      installRoot:
        raw.installRoot != null ? String(raw.installRoot) : undefined,
      applyPid:
        raw.applyPid != null && Number.isFinite(Number(raw.applyPid))
          ? Number(raw.applyPid)
          : undefined,
    };
  } catch {
    return null;
  }
}

export function isProcessAlive(pid: number | undefined | null): boolean {
  if (pid == null || !Number.isFinite(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function lockMatchesInstall(
  payload: UpdateLockPayload,
  installRoot?: string | null,
): boolean {
  if (!installRoot || !payload.installRoot) {
    return true;
  }
  return pathsEqualIgnoreCase(payload.installRoot, installRoot);
}

/** True when a non-stale lock exists and apply is actually running. */
export function isActiveUpdateLock(
  installRoot?: string | null,
): boolean {
  const lockPath = updateLockPath();
  if (!fs.existsSync(lockPath) || isUpdateLockStale(lockPath)) {
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

export function pathsEqualIgnoreCase(a: string, b: string): boolean {
  return (
    path.normalize(a).replace(/\\/g, "/").toLowerCase() ===
    path.normalize(b).replace(/\\/g, "/").toLowerCase()
  );
}

export function clearStaleUpdateLock(): boolean {
  const lockPath = updateLockPath();
  if (!fs.existsSync(lockPath) || !isUpdateLockStale(lockPath)) {
    return false;
  }
  if (isApplyScriptRunning()) {
    return false;
  }
  const payload = readUpdateLock();
  if (payload?.applyPid != null && isProcessAlive(payload.applyPid)) {
    return false;
  }
  try {
    fs.unlinkSync(lockPath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Clear a lock only when apply is clearly dead: stale age, or applyPid exited
 * and apply-update.ps1 is not running. Do not clear fresh locks just because
 * Settings opened.
 */
export function clearOrphanUpdateLock(): boolean {
  const lockPath = updateLockPath();
  if (!fs.existsSync(lockPath)) {
    return false;
  }
  if (isApplyScriptRunning()) {
    return false;
  }
  const payload = readUpdateLock();
  if (payload?.applyPid != null && isProcessAlive(payload.applyPid)) {
    return false;
  }
  if (isUpdateLockStale(lockPath)) {
    try {
      fs.unlinkSync(lockPath);
      return true;
    } catch {
      return false;
    }
  }
  if (payload?.applyPid != null && !isProcessAlive(payload.applyPid)) {
    try {
      const age = Date.now() - fs.statSync(lockPath).mtimeMs;
      // Allow brief handoff window after spawn before treating as orphan.
      if (age < 60_000) {
        return false;
      }
      fs.unlinkSync(lockPath);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

export function releaseUpdateLock(): void {
  const lockPath = updateLockPath();
  if (fs.existsSync(lockPath)) {
    fs.unlinkSync(lockPath);
  }
}

export function writeUpdateLock(
  version: string,
  opts?: { installRoot?: string | null; applyPid?: number },
): void {
  const lockPath = updateLockPath();
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  const payload: UpdateLockPayload = {
    started: new Date().toISOString(),
    version,
  };
  if (opts?.installRoot) {
    payload.installRoot = opts.installRoot;
  }
  if (opts?.applyPid != null && Number.isFinite(opts.applyPid)) {
    payload.applyPid = opts.applyPid;
  }
  fs.writeFileSync(lockPath, JSON.stringify(payload));
}

export function refreshUpdateLockState(): {
  clearedStale: boolean;
  clearedOrphan: boolean;
} {
  const clearedStale = clearStaleUpdateLock();
  const clearedOrphan = clearOrphanUpdateLock();
  return { clearedStale, clearedOrphan };
}

export function versionBackupDirName(installRoot: string, oldVersion: string): string {
  return `${installRoot}.backup-${oldVersion}`;
}

export function listInstallVersionBackups(installRoot: string): string[] {
  if (!installRoot) {
    return [];
  }
  const parent = path.dirname(installRoot);
  const base = path.basename(installRoot);
  const prefix = `${base}.backup-`;
  try {
    if (!fs.existsSync(parent)) {
      return [];
    }
    return fs
      .readdirSync(parent, { withFileTypes: true })
      .filter((ent) => ent.isDirectory() && ent.name.startsWith(prefix))
      .map((ent) => path.join(parent, ent.name))
      .sort((a, b) => a.localeCompare(b));
  } catch {
    return [];
  }
}

export function deleteInstallVersionBackups(installRoot: string): {
  ok: boolean;
  deleted: string[];
  errors: string[];
} {
  const found = listInstallVersionBackups(installRoot);
  const deleted: string[] = [];
  const errors: string[] = [];
  for (const dir of found) {
    try {
      fs.rmSync(dir, { recursive: true, force: true });
      deleted.push(dir);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      errors.push(`${dir}: ${msg}`);
    }
  }
  return { ok: errors.length === 0, deleted, errors };
}

export const CLOSE_OTHER_INSTANCES_MESSAGE =
  "Please close all other running instances of Mason Jar before updating.";

export function buildApplySpawnCommand(scriptPath: string): {
  command: string;
  args: string[];
} {
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

/** Best-effort: true if apply-update.ps1 is still running (Windows). */
export function isApplyScriptRunning(): boolean {
  if (process.platform !== "win32") {
    return false;
  }
  try {
    // Match `-File …apply-update.ps1` only — not this detection query's own CommandLine.
    const script =
      "$p = Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" -ErrorAction SilentlyContinue | " +
      "Where-Object { $_.CommandLine -and ($_.CommandLine -match '(?i)-File\\s+.*apply-update\\.ps1') }; " +
      "if ($p) { '1' } else { '0' }";
    const out = execSync(
      `powershell -NoProfile -Command ${JSON.stringify(script)}`,
      {
        encoding: "utf8",
        timeout: 10000,
        windowsHide: true,
      },
    ).trim();
    return out === "1";
  } catch {
    return false;
  }
}

export function isUpdateInProgress(installRoot?: string | null): boolean {
  refreshUpdateLockState();
  return isActiveUpdateLock(installRoot);
}

export function resolveInstallRoot(isPackaged: boolean): string | null {
  if (!isPackaged) {
    return null;
  }
  return path.dirname(process.execPath);
}

export function expectedWindowsZipName(version: string): string {
  return `masonjar-win32-x64-${version}.zip`;
}

function githubHeaders(userAgent: string): Record<string, string> {
  return {
    "User-Agent": userAgent,
    Accept: "application/vnd.github+json",
  };
}

async function fetchJson<T>(
  url: string,
  userAgent: string,
): Promise<{ ok: boolean; status: number; data?: T }> {
  const response = await serverFetch(url, { headers: githubHeaders(userAgent) });
  if (!response.ok) {
    return { ok: false, status: response.status };
  }
  const data = (await response.json()) as T;
  return { ok: true, status: response.status, data };
}

export function buildCheckResult(
  currentVersion: string,
  release: GitHubRelease | null,
): UpdateCheckResult {
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
  const windowsAsset =
    latest != null ? pickWindowsZipAsset(release.assets, latest) : null;
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

export class UpdateManager {
  private cachedCheck: UpdateCheckResult | null = null;
  private stagedVersion: string | null = null;
  private stagedExtractDir: string | null = null;
  private downloadInFlight = false;

  constructor(
    private readonly homeDir: string,
    private readonly currentVersion: string,
    private readonly isPackaged: boolean,
  ) {}

  getCachedCheck(): UpdateCheckResult | null {
    return this.cachedCheck;
  }

  setCachedCheck(result: UpdateCheckResult | null): void {
    this.cachedCheck = result;
  }

  getPreferences(): UpdatePreferences {
    return loadUpdatePreferences(this.homeDir);
  }

  savePreferences(patch: Partial<UpdatePreferences>): UpdatePreferences {
    return saveUpdatePreferences(this.homeDir, patch);
  }

  async checkForUpdatesDetailed(
    allowPrerelease?: boolean,
  ): Promise<UpdateCheckResult> {
    const prefs =
      allowPrerelease != null
        ? { allow_prerelease: !!allowPrerelease }
        : this.getPreferences();
    const userAgent = `MasonJar/${this.currentVersion}`;

    try {
      let release: GitHubRelease | null = null;
      if (prefs.allow_prerelease) {
        const url = `https://api.github.com/repos/${GITHUB_REPO}/releases?per_page=30`;
        const res = await fetchJson<GitHubRelease[]>(url, userAgent);
        if (!res.ok) {
          const err: UpdateCheckResult = {
            ...buildCheckResult(this.currentVersion, null),
            error: `GitHub API returned ${res.status}`,
          };
          this.cachedCheck = err;
          return err;
        }
        release = pickBestRelease(res.data || []);
      } else {
        const url = `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`;
        const res = await fetchJson<GitHubRelease>(url, userAgent);
        if (res.status === 404) {
          const empty = buildCheckResult(this.currentVersion, null);
          this.cachedCheck = empty;
          return empty;
        }
        if (!res.ok || !res.data) {
          const err: UpdateCheckResult = {
            ...buildCheckResult(this.currentVersion, null),
            error: `GitHub API returned ${res.status}`,
          };
          this.cachedCheck = err;
          return err;
        }
        release = res.data;
      }

      const result = buildCheckResult(this.currentVersion, release);
      this.cachedCheck = result;
      return result;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      const err: UpdateCheckResult = {
        ...buildCheckResult(this.currentVersion, null),
        error: msg,
      };
      this.cachedCheck = err;
      return err;
    }
  }

  async checkLatestStableRelease(): Promise<UpdateCheckResult> {
    const userAgent = `MasonJar/${this.currentVersion}`;
    try {
      const url = `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`;
      const res = await fetchJson<GitHubRelease>(url, userAgent);
      if (res.status === 404) {
        const empty = buildCheckResult(this.currentVersion, null);
        this.cachedCheck = empty;
        return empty;
      }
      if (!res.ok || !res.data) {
        const err: UpdateCheckResult = {
          ...buildCheckResult(this.currentVersion, null),
          error: `GitHub API returned ${res.status}`,
        };
        this.cachedCheck = err;
        return err;
      }
      const result = buildCheckResult(this.currentVersion, res.data);
      this.cachedCheck = result;
      return result;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      const err: UpdateCheckResult = {
        ...buildCheckResult(this.currentVersion, null),
        error: msg,
      };
      this.cachedCheck = err;
      return err;
    }
  }

  getApplyInfo(): UpdateApplyInfo {
    refreshUpdateLockState();
    const installRoot = resolveInstallRoot(this.isPackaged);
    return {
      canApplyInApp:
        this.isPackaged && process.platform === "win32" && !!installRoot,
      platform: process.platform,
      isPackaged: this.isPackaged,
      installRoot,
      logPath: updateLogPath(this.homeDir),
      stagingReady: !!this.stagedExtractDir && !!this.stagedVersion,
      stagedVersion: this.stagedVersion,
      updateInProgress:
        isUpdateInProgress(installRoot) || this.downloadInFlight,
    };
  }

  private updatesDir(): string {
    return path.join(masonJarTempRoot(), "updates");
  }

  private zipPathForVersion(version: string): string {
    return path.join(this.updatesDir(), `masonjar-${version}.zip`);
  }

  private extractDirForVersion(version: string): string {
    return path.join(this.updatesDir(), `extract-${version}`);
  }

  findStagedInstallFolder(extractDir: string): string | null {
    const direct = path.join(extractDir, "masonjar-win32-x64");
    if (fs.existsSync(path.join(direct, "masonjar.exe"))) {
      return direct;
    }
    if (fs.existsSync(path.join(extractDir, "masonjar.exe"))) {
      return extractDir;
    }
    const entries = fs.readdirSync(extractDir, { withFileTypes: true });
    for (const ent of entries) {
      if (!ent.isDirectory()) {
        continue;
      }
      const candidate = path.join(extractDir, ent.name);
      if (fs.existsSync(path.join(candidate, "masonjar.exe"))) {
        return candidate;
      }
    }
    return null;
  }

  private restoreStagedFromDisk(version: string | null | undefined): boolean {
    if (!version) {
      return false;
    }
    const extractDir = this.extractDirForVersion(version);
    if (!fs.existsSync(extractDir)) {
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

  private isStagingReadyForVersion(version: string | null | undefined): boolean {
    if (!version || !this.stagedExtractDir || this.stagedVersion !== version) {
      return false;
    }
    return fs.existsSync(path.join(this.stagedExtractDir, "masonjar.exe"));
  }

  async downloadWindowsUpdate(
    onProgress: (percent: number, message: string) => void,
  ): Promise<{ ok: boolean; error?: string }> {
    if (process.platform !== "win32") {
      return { ok: false, error: "Windows-only download" };
    }
    if (this.downloadInFlight) {
      return { ok: false, error: "Download already in progress" };
    }
    const check = this.cachedCheck;
    if (!check?.windowsAsset || !check.latest) {
      return { ok: false, error: "No Windows update asset available" };
    }

    this.downloadInFlight = true;
    const version = check.latest;
    const zipPath = this.zipPathForVersion(version);
    const extractDir = this.extractDirForVersion(version);

    try {
      fs.mkdirSync(this.updatesDir(), { recursive: true });
      if (fs.existsSync(extractDir)) {
        fs.rmSync(extractDir, { recursive: true, force: true });
      }

      onProgress(0, "Starting download…");
      await this.streamDownload(
        check.windowsAsset.browser_download_url,
        zipPath,
        check.windowsAsset.size,
        onProgress,
      );

      onProgress(95, "Extracting update…");
      await this.extractZip(zipPath, extractDir);

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
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return { ok: false, error: msg };
    } finally {
      this.downloadInFlight = false;
    }
  }

  private streamDownload(
    url: string,
    target: string,
    totalBytesHint: number,
    onProgress: (percent: number, message: string) => void,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const file = fs.createWriteStream(target, { highWaterMark: 64 * 1024 });
      const request = https.get(url, (response) => {
        if (
          response.statusCode &&
          response.statusCode >= 300 &&
          response.statusCode < 400 &&
          response.headers.location
        ) {
          file.close();
          fs.unlink(target, () => {
            this.streamDownload(
              response.headers.location as string,
              target,
              totalBytesHint,
              onProgress,
            )
              .then(resolve)
              .catch(reject);
          });
          return;
        }
        if (response.statusCode !== 200) {
          reject(new Error(`Download failed: HTTP ${response.statusCode}`));
          return;
        }
        const totalBytes =
          parseInt(String(response.headers["content-length"] || "0"), 10) ||
          totalBytesHint ||
          0;
        let received = 0;
        let lastPct = -1;
        response.on("data", (chunk: Buffer) => {
          received += chunk.length;
          if (totalBytes > 0) {
            const pct = Math.min(90, Math.floor((received / totalBytes) * 90));
            if (pct !== lastPct) {
              lastPct = pct;
              onProgress(
                pct,
                `Downloading… ${Math.round((received / totalBytes) * 100)}%`,
              );
            }
          } else {
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
        fs.unlink(target, () => reject(err));
      });
      file.on("error", (err) => {
        fs.unlink(target, () => reject(err));
      });
    });
  }

  private extractZip(zipPath: string, extractDir: string): Promise<void> {
    fs.mkdirSync(extractDir, { recursive: true });
    const ps = [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      `Expand-Archive -LiteralPath '${zipPath.replace(/'/g, "''")}' -DestinationPath '${extractDir.replace(/'/g, "''")}' -Force`,
    ];
    return new Promise((resolve, reject) => {
      const child = spawn("powershell.exe", ps, { windowsHide: true });
      let stderr = "";
      child.stderr.on("data", (d) => {
        stderr += String(d);
      });
      child.on("error", reject);
      child.on("close", (code) => {
        if (code === 0) {
          resolve();
        } else {
          reject(new Error(stderr.trim() || `Expand-Archive exited ${code}`));
        }
      });
    });
  }

  writeApplyScript(
    installRoot: string,
    stagingDir: string,
    oldVersion: string,
    newVersion: string,
    keepBackup: boolean = false,
  ): string {
    const scriptPath = path.join(masonJarTempRoot(), "apply-update.ps1");
    const logPath = updateLogPath(this.homeDir);
    const fallbackLogPath = updateFallbackLogPath();
    const backupDir = versionBackupDirName(installRoot, oldVersion);
    const exePath = path.join(installRoot, "masonjar.exe");
    const lockPath = updateLockPath();
    // Electron packaged layout: package.json lives under resources/app (app.getAppPath()).
    // Root package.json is only a compatibility shim for older apply scripts / odd layouts.
    const stagingExe = path.join(stagingDir, "masonjar.exe");
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

    fs.mkdirSync(path.dirname(scriptPath), { recursive: true });
    fs.writeFileSync(scriptPath, ps1, "utf8");
    return scriptPath;
  }

  refuseIfOtherInstances(installRoot: string | null): {
    ok: boolean;
    error?: string;
  } {
    if (!installRoot) {
      return { ok: true };
    }
    const others = countOtherMasonJarInstances(installRoot);
    if (others > 0) {
      return { ok: false, error: CLOSE_OTHER_INSTANCES_MESSAGE };
    }
    return { ok: true };
  }

  prepareWindowsApply(): {
    ok: boolean;
    error?: string;
    scriptPath?: string;
    stagedVersion?: string;
  } {
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
    if (!fs.existsSync(installRoot)) {
      return {
        ok: false,
        error: "Install folder was moved or deleted.",
      };
    }
    if (!fs.existsSync(path.join(this.stagedExtractDir, "masonjar.exe"))) {
      return { ok: false, error: "Staged update is missing masonjar.exe." };
    }

    const prefs = this.getPreferences();
    const scriptPath = this.writeApplyScript(
      installRoot,
      this.stagedExtractDir,
      this.currentVersion,
      this.stagedVersion,
      !!prefs.keep_version_backups,
    );
    return { ok: true, scriptPath, stagedVersion: this.stagedVersion };
  }

  launchApplyAndQuit(
    scriptPath: string,
    stagedVersion: string,
    quit: () => void,
  ): Promise<{ ok: boolean; error?: string }> {
    const installRoot = resolveInstallRoot(this.isPackaged);
    appendUpdateLogLine(
      this.homeDir,
      `Preparing apply: script=${scriptPath} installRoot=${installRoot || "?"}`,
    );
    appendUpdateLogLine(
      this.homeDir,
      `Fallback log path: ${updateFallbackLogPath()}`,
    );

    return new Promise((resolve) => {
      const { command, args } = buildApplySpawnCommand(scriptPath);
      let settled = false;
      const finish = (result: { ok: boolean; error?: string }) => {
        if (settled) {
          return;
        }
        settled = true;
        resolve(result);
      };

      try {
        const child = spawn(command, args, {
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
        appendUpdateLogLine(
          this.homeDir,
          `Updater breakaway launcher pid=${child.pid} (apply-update.ps1 will rewrite lock with its own PID)`,
        );
        setTimeout(() => {
          if (settled) {
            return;
          }
          quit();
          finish({ ok: true });
        }, 1000);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        appendUpdateLogLine(this.homeDir, `Spawn exception: ${msg}`);
        releaseUpdateLock();
        finish({ ok: false, error: msg });
      }
    });
  }

  async runWindowsUpdateNow(
    onProgress: (percent: number, message: string) => void,
    quit: () => void,
  ): Promise<{ ok: boolean; error?: string; lockCleared?: boolean }> {
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
      return {
        ...peers,
        lockCleared: lockState.clearedStale || lockState.clearedOrphan,
      };
    }

    const latest = this.cachedCheck?.latest;
    if (!this.isStagingReadyForVersion(latest)) {
      this.restoreStagedFromDisk(latest);
    }

    if (!this.isStagingReadyForVersion(latest)) {
      const download = await this.downloadWindowsUpdate(onProgress);
      if (!download.ok) {
        releaseUpdateLock();
        return {
          ...download,
          lockCleared: lockState.clearedStale || lockState.clearedOrphan,
        };
      }
    } else {
      onProgress(100, "Using downloaded update…");
    }

    onProgress(100, "Installing update…");
    const prepared = this.prepareWindowsApply();
    if (!prepared.ok) {
      releaseUpdateLock();
      return {
        ...prepared,
        lockCleared: lockState.clearedStale || lockState.clearedOrphan,
      };
    }
    const applied = await this.launchApplyAndQuit(
      prepared.scriptPath!,
      prepared.stagedVersion || this.stagedVersion || latest || "",
      quit,
    );
    if (!applied.ok) {
      releaseUpdateLock();
    }
    return {
      ...applied,
      lockCleared: lockState.clearedStale || lockState.clearedOrphan,
    };
  }
}
