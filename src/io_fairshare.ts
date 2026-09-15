import * as crypto from "crypto";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { execSync } from "child_process";

export interface IoFairshareSharedConfig {
  enabled: boolean;
  link_mbps: number | "auto";
  headroom: number;
  min_mbps_per_job: number;
  max_mbps_per_job: number | "auto";
  small_file_bytes: number;
  stale_seconds: number;
  nas_path_prefixes: string[];
}

export interface IoFairshareUserConfig {
  enabled?: boolean;
  link_mbps?: number | "auto";
}

export interface IoFairshareRegistryEntry {
  job_id: string;
  pid: number;
  user: string;
  hostname: string;
  label: string;
  started_at: string;
  last_heartbeat: string;
  app_instance_id?: string;
  throttled_bytes_total?: number;
  throttled_mbps_1m?: number;
}

export interface IoFairshareStatus {
  enabled: boolean;
  coordinator_dir: string;
  link_mbps: number;
  headroom: number;
  budget_mbps: number;
  active_jobs: number;
  limit_mbps: number;
  min_mbps_per_job: number;
  max_mbps_per_job: number;
  local_jobs: string[];
  local_throttled_mbps_1m: number;
  nas_path_prefixes: string[];
  shared_config_path: string;
  shared_link_mbps: number | "auto";
}

const DEFAULT_SHARED: IoFairshareSharedConfig = {
  enabled: true,
  link_mbps: "auto",
  headroom: 0.85,
  min_mbps_per_job: 25,
  max_mbps_per_job: "auto",
  small_file_bytes: 256 * 1024,
  stale_seconds: 30,
  nas_path_prefixes: [],
};

let cachedLinkMbps: number | null = null;
let appInstanceId = "";
let fairshareStatusCache: { key: string; at: number; value: IoFairshareStatus } | null = null;

export function setAppInstanceId(id: string): void {
  appInstanceId = String(id || "").trim();
}

export function getAppInstanceId(): string {
  return appInstanceId;
}

export function resetLinkSpeedCache(): void {
  cachedLinkMbps = null;
}

export function defaultCoordinatorDir(): string {
  const override = process.env.MASONJAR_IO_FAIRSHARE_DIR;
  if (override && override.trim().length > 0) {
    return path.resolve(override.trim());
  }
  if (process.platform === "win32") {
    const programData =
      process.env.ProgramData || path.join("C:", "ProgramData");
    return path.join(programData, "MasonJar", "io-fairshare");
  }
  if (process.platform === "darwin") {
    return "/Library/Application Support/MasonJar/io-fairshare";
  }
  return path.join("/var", "run", "masonjar-io-fairshare");
}

export function userConfigPath(homeDir: string): string {
  return path.join(homeDir, "io_fairshare.json");
}

function sharedConfigPath(coordinatorDir: string): string {
  return path.join(coordinatorDir, "config.json");
}

export function getSharedConfigPath(coordinatorDir: string): string {
  return sharedConfigPath(coordinatorDir);
}

function prefixKey(text: string): string {
  if (process.platform === "win32") {
    return text.replace(/\//g, "\\").toLowerCase();
  }
  return text;
}

/** Normalize a picked folder to a NAS throttle root (drive letter or UNC share). */
export function normalizeNasPathPrefix(absPath: string): string | null {
  const raw = String(absPath || "").trim();
  if (!raw) {
    return null;
  }
  if (process.platform === "win32") {
    const normalized = raw.replace(/\//g, "\\");
    if (normalized.startsWith("\\\\")) {
      const parts = normalized.split("\\").filter(Boolean);
      if (parts.length < 2) {
        return null;
      }
      return "\\\\" + parts[0] + "\\" + parts[1];
    }
    const driveMatch = normalized.match(/^([A-Za-z]:)(\\.*)?$/);
    if (driveMatch) {
      return driveMatch[1].toUpperCase() + "\\";
    }
    return null;
  }
  const resolved = path.resolve(raw);
  if (process.platform === "darwin" && resolved.startsWith("/Volumes/")) {
    const parts = resolved.split(path.sep).filter(Boolean);
    if (parts.length >= 2 && parts[0] === "Volumes") {
      return path.join("/", "Volumes", parts[1]);
    }
  }
  if (resolved.startsWith("\\\\") || resolved.startsWith("//")) {
    const unc = resolved.replace(/\//g, "\\");
    const parts = unc.split("\\").filter(Boolean);
    if (parts.length >= 2) {
      return "\\\\" + parts[0] + "\\" + parts[1];
    }
  }
  return null;
}

export function mergeNasPathPrefixes(
  existing: string[],
  added: string[],
): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const all = (existing || []).concat(added || []);
  for (let i = 0; i < all.length; i++) {
    const norm = normalizeNasPathPrefix(all[i]) || String(all[i] || "").trim();
    if (!norm) {
      continue;
    }
    const key = prefixKey(norm);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(norm);
  }
  out.sort(function (a, b) {
    return a.localeCompare(b);
  });
  return out;
}

function registryDir(coordinatorDir: string): string {
  return path.join(coordinatorDir, "registry");
}

function readJsonFile<T>(filePath: string): T | null {
  try {
    if (!fs.existsSync(filePath)) {
      return null;
    }
    return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
  } catch (_err) {
    return null;
  }
}

async function readJsonFileAsync<T>(filePath: string): Promise<T | null> {
  try {
    const raw = await fs.promises.readFile(filePath, "utf8");
    return JSON.parse(raw) as T;
  } catch (_err) {
    return null;
  }
}

function isRegistryLockError(err: unknown): boolean {
  const code = (err as NodeJS.ErrnoException)?.code;
  return code === "EPERM" || code === "EACCES" || code === "EBUSY";
}

function sleepMs(ms: number): void {
  if (ms <= 0) {
    return;
  }
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    /* brief spin for registry lock retry */
  }
}

function removeFileBestEffort(filePath: string): void {
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  } catch (_err) {
    /* ignore */
  }
}

function writeJsonWithRetry(
  filePath: string,
  payload: string,
  maxAttempts = 5,
): void {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      fs.writeFileSync(filePath, payload, "utf8");
      return;
    } catch (err) {
      if (attempt < maxAttempts - 1 && isRegistryLockError(err)) {
        sleepMs(25 * (attempt + 1));
        continue;
      }
      throw err;
    }
  }
}

let lastRegistryWarnAt = 0;

function warnRegistryBestEffort(message: string): void {
  const now = Date.now();
  if (now - lastRegistryWarnAt < 60000) {
    return;
  }
  lastRegistryWarnAt = now;
  console.warn(message);
}

/** Atomic JSON write; falls back to in-place retry on Windows file locks. */
export function writeJsonAtomic(filePath: string, data: unknown): void {
  const dir = path.dirname(filePath);
  fs.mkdirSync(dir, { recursive: true });
  const payload = JSON.stringify(data, null, 2);
  const tmp = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tmp, payload, "utf8");
  try {
    try {
      fs.renameSync(tmp, filePath);
    } catch (err) {
      if (isRegistryLockError(err) && fs.existsSync(filePath)) {
        removeFileBestEffort(tmp);
        writeJsonWithRetry(filePath, payload);
        return;
      }
      if (isRegistryLockError(err)) {
        sleepMs(50);
        fs.renameSync(tmp, filePath);
        return;
      }
      throw err;
    }
  } finally {
    removeFileBestEffort(tmp);
  }
}

export function parseLinkSpeedText(raw: string): number | null {
  const text = String(raw || "").trim().toLowerCase();
  if (!text) {
    return null;
  }
  const gbps = text.match(/([\d.]+)\s*gbps/);
  if (gbps) {
    return Math.round(parseFloat(gbps[1]) * 1000);
  }
  const mbps = text.match(/([\d.]+)\s*mbps/);
  if (mbps) {
    return Math.round(parseFloat(mbps[1]));
  }
  const kbps = text.match(/([\d.]+)\s*kbps/);
  if (kbps) {
    return Math.max(1, Math.round(parseFloat(kbps[1]) / 1000));
  }
  const digits = text.match(/([\d.]+)/);
  if (digits) {
    const n = parseFloat(digits[1]);
    if (n >= 100) {
      return Math.round(n);
    }
    if (n >= 1) {
      return Math.round(n * 1000);
    }
  }
  return null;
}

export function detectLinkMbps(): number {
  if (cachedLinkMbps != null && cachedLinkMbps > 0) {
    return cachedLinkMbps;
  }
  let detected: number | null = null;
  try {
    if (process.platform === "win32") {
      const cmd =
        'powershell -NoProfile -Command "Get-NetAdapter | Where-Object Status -eq \'Up\' | Sort-Object LinkSpeed -Descending | Select-Object -First 1 -ExpandProperty LinkSpeed"';
      const out = execSync(cmd, { encoding: "utf8", timeout: 8000 }).trim();
      detected = parseLinkSpeedText(out);
    } else if (process.platform === "darwin") {
      try {
        const out = execSync("networksetup -listallhardwareports", {
          encoding: "utf8",
          timeout: 5000,
        });
        const devMatch = out.match(/Device:\s*(en\d+)/);
        if (devMatch) {
          const ifOut = execSync(`ifconfig ${devMatch[1]}`, {
            encoding: "utf8",
            timeout: 5000,
          });
          const media = ifOut.match(/media:\s*[^\n]*?(\\d+)baseT/i);
          if (media) {
            detected = parseInt(media[1], 10);
          }
        }
      } catch (_err) {
        /* fall through */
      }
      if (detected == null) {
        detected = 1000;
      }
    }
  } catch (_err) {
    detected = null;
  }
  cachedLinkMbps = detected != null && detected > 0 ? detected : 1000;
  return cachedLinkMbps;
}

export function ensureCoordinatorDir(coordinatorDir: string): boolean {
  try {
    fs.mkdirSync(registryDir(coordinatorDir), { recursive: true });
    const cfgPath = sharedConfigPath(coordinatorDir);
    if (!fs.existsSync(cfgPath)) {
      writeJsonAtomic(cfgPath, DEFAULT_SHARED);
    }
    return true;
  } catch (_err) {
    return false;
  }
}

export function loadSharedConfig(coordinatorDir: string): IoFairshareSharedConfig {
  const parsed = readJsonFile<Partial<IoFairshareSharedConfig>>(
    sharedConfigPath(coordinatorDir),
  );
  return {
    ...DEFAULT_SHARED,
    ...(parsed || {}),
  };
}

export function saveSharedConfig(
  coordinatorDir: string,
  patch: Partial<IoFairshareSharedConfig>,
): IoFairshareSharedConfig {
  ensureCoordinatorDir(coordinatorDir);
  const merged = { ...loadSharedConfig(coordinatorDir), ...patch };
  writeJsonAtomic(sharedConfigPath(coordinatorDir), merged);
  return merged;
}

export function loadUserConfig(homeDir: string): IoFairshareUserConfig {
  return readJsonFile<IoFairshareUserConfig>(userConfigPath(homeDir)) || {};
}

export function saveUserConfig(
  homeDir: string,
  patch: IoFairshareUserConfig,
): IoFairshareUserConfig {
  const merged = { ...loadUserConfig(homeDir), ...patch };
  fs.mkdirSync(homeDir, { recursive: true });
  writeJsonAtomic(userConfigPath(homeDir), merged);
  return merged;
}

function resolveLinkMbps(
  shared: IoFairshareSharedConfig,
  user: IoFairshareUserConfig,
): number {
  const pick = user.link_mbps != null ? user.link_mbps : shared.link_mbps;
  if (pick === "auto") {
    return detectLinkMbps();
  }
  return Math.max(1, Number(pick) || 1000);
}

function resolveMaxMbps(
  shared: IoFairshareSharedConfig,
  linkMbps: number,
): number {
  if (shared.max_mbps_per_job === "auto") {
    return Math.max(shared.min_mbps_per_job, linkMbps * shared.headroom);
  }
  return Math.max(shared.min_mbps_per_job, Number(shared.max_mbps_per_job));
}

export function listRegistryEntries(
  coordinatorDir: string,
  staleSeconds: number,
): IoFairshareRegistryEntry[] {
  const dir = registryDir(coordinatorDir);
  if (!fs.existsSync(dir)) {
    return [];
  }
  const now = Date.now();
  const out: IoFairshareRegistryEntry[] = [];
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".json")) {
      continue;
    }
    const full = path.join(dir, name);
    const entry = readJsonFile<IoFairshareRegistryEntry>(full);
    if (!entry || !entry.last_heartbeat) {
      try {
        fs.unlinkSync(full);
      } catch (_err) {
        /* ignore */
      }
      continue;
    }
    const ageMs = now - Date.parse(entry.last_heartbeat);
    if (!Number.isFinite(ageMs) || ageMs > staleSeconds * 1000) {
      try {
        fs.unlinkSync(full);
      } catch (_err) {
        /* ignore */
      }
      continue;
    }
    out.push(entry);
  }
  return out;
}

export function computeJobLimitMbps(
  shared: IoFairshareSharedConfig,
  linkMbps: number,
  activeJobs: number,
): number {
  const jobs = Math.max(1, activeJobs);
  const budget = linkMbps * shared.headroom;
  const maxCap = resolveMaxMbps(shared, linkMbps);
  const raw = budget / jobs;
  return Math.min(maxCap, Math.max(shared.min_mbps_per_job, raw));
}

export function isFairshareEnabled(
  coordinatorDir: string,
  homeDir: string,
): boolean {
  if (process.env.MASONJAR_IO_FAIRSHARE === "0") {
    return false;
  }
  const user = loadUserConfig(homeDir);
  if (user.enabled === false) {
    return false;
  }
  const shared = loadSharedConfig(coordinatorDir);
  return shared.enabled !== false;
}

function sumLocalThrottledMbps1m(
  entries: IoFairshareRegistryEntry[],
  instanceId: string,
): number {
  if (!instanceId) {
    return 0;
  }
  let total = 0;
  for (const entry of entries) {
    if (entry.app_instance_id !== instanceId) {
      continue;
    }
    const rate = Number(entry.throttled_mbps_1m);
    if (Number.isFinite(rate) && rate > 0) {
      total += rate;
    }
  }
  return total;
}

export function formatFairshareTitleSuffix(status: IoFairshareStatus): string {
  if (!status.enabled) {
    return "";
  }
  const jobs = status.active_jobs || 0;
  const limit = Math.round(status.limit_mbps || 0);
  const parts = [`${jobs} job${jobs === 1 ? "" : "s"}`, `~${limit} Mbps`];
  const nas = Number(status.local_throttled_mbps_1m);
  if (Number.isFinite(nas) && nas >= 0.5) {
    parts.push(`NAS ${Math.round(nas)} Mbps`);
  }
  return " · " + parts.join(" · ");
}

export function formatFairshareCompactLine(status: IoFairshareStatus): string {
  if (!status || !status.enabled) {
    return "";
  }
  const jobs = status.active_jobs || 0;
  const limit = Math.round(status.limit_mbps || 0);
  let line =
    "Network share: " +
    jobs +
    " active job" +
    (jobs === 1 ? "" : "s") +
    " on this machine · ~" +
    limit +
    " Mbps for new pipeline I/O";
  const nas = Number(status.local_throttled_mbps_1m);
  if (Number.isFinite(nas) && nas >= 0.5) {
    line += " · NAS " + Math.round(nas) + " Mbps";
  }
  line += " · configure in Start → Settings → Network";
  return line;
}

export function getIoFairshareStatus(
  coordinatorDir: string,
  homeDir: string,
): IoFairshareStatus {
  const cacheKey = `${coordinatorDir}\u0000${homeDir}`;
  const now = Date.now();
  if (fairshareStatusCache && fairshareStatusCache.key === cacheKey && now - fairshareStatusCache.at < 1500) {
    return fairshareStatusCache.value;
  }
  ensureCoordinatorDir(coordinatorDir);
  const shared = loadSharedConfig(coordinatorDir);
  const user = loadUserConfig(homeDir);
  const enabled = isFairshareEnabled(coordinatorDir, homeDir);
  const linkMbps = resolveLinkMbps(shared, user);
  const entries = listRegistryEntries(coordinatorDir, shared.stale_seconds);
  const activeJobs = Math.max(1, entries.length);
  const budget = linkMbps * shared.headroom;
  const limit = computeJobLimitMbps(shared, linkMbps, entries.length || 1);
  const maxCap = resolveMaxMbps(shared, linkMbps);
  const localJobs = entries
    .filter((e) => e.pid === process.pid || e.hostname === os.hostname())
    .map((e) => e.label);
  const localThrottledMbps = sumLocalThrottledMbps1m(entries, appInstanceId);
  const value: IoFairshareStatus = {
    enabled,
    coordinator_dir: coordinatorDir,
    link_mbps: linkMbps,
    headroom: shared.headroom,
    budget_mbps: budget,
    active_jobs: entries.length,
    limit_mbps: enabled ? limit : maxCap,
    min_mbps_per_job: shared.min_mbps_per_job,
    max_mbps_per_job: maxCap,
    local_jobs: localJobs,
    local_throttled_mbps_1m: localThrottledMbps,
    nas_path_prefixes: mergeNasPathPrefixes(shared.nas_path_prefixes || [], []),
    shared_config_path: sharedConfigPath(coordinatorDir),
    shared_link_mbps: shared.link_mbps,
  };
  fairshareStatusCache = { key: cacheKey, at: now, value };
  return value;
}

export function newJobId(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return crypto.randomBytes(16).toString("hex");
}

export function registerJob(
  coordinatorDir: string,
  jobId: string,
  label: string,
): void {
  try {
    ensureCoordinatorDir(coordinatorDir);
    const entry: IoFairshareRegistryEntry = {
      job_id: jobId,
      pid: process.pid,
      user: os.userInfo().username,
      hostname: os.hostname(),
      label,
      started_at: new Date().toISOString(),
      last_heartbeat: new Date().toISOString(),
    };
    if (appInstanceId) {
      entry.app_instance_id = appInstanceId;
    }
    writeJsonAtomic(path.join(registryDir(coordinatorDir), `${jobId}.json`), entry);
  } catch (err) {
    warnRegistryBestEffort(
      `io-fairshare: registry register failed (${jobId}): ${(err as Error).message}`,
    );
  }
}

export function touchJob(coordinatorDir: string, jobId: string): void {
  try {
    const filePath = path.join(registryDir(coordinatorDir), `${jobId}.json`);
    const entry = readJsonFile<IoFairshareRegistryEntry>(filePath);
    if (!entry) {
      return;
    }
    entry.last_heartbeat = new Date().toISOString();
    writeJsonAtomic(filePath, entry);
  } catch (err) {
    warnRegistryBestEffort(
      `io-fairshare: registry heartbeat failed (${jobId}): ${(err as Error).message}`,
    );
  }
}

export async function touchJobAsync(coordinatorDir: string, jobId: string): Promise<void> {
  try {
    const filePath = path.join(registryDir(coordinatorDir), `${jobId}.json`);
    const entry = await readJsonFileAsync<IoFairshareRegistryEntry>(filePath);
    if (!entry) return;
    entry.last_heartbeat = new Date().toISOString();
    const payload = JSON.stringify(entry, null, 2);
    await fs.promises.writeFile(filePath, payload, "utf8");
  } catch (err) {
    warnRegistryBestEffort(`io-fairshare: async registry heartbeat failed (${jobId}): ${(err as Error).message}`);
  }
}

export function unregisterJob(coordinatorDir: string, jobId: string): void {
  const filePath = path.join(registryDir(coordinatorDir), `${jobId}.json`);
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  } catch (_err) {
    /* ignore */
  }
}

const activeNodeJobs = new Map<string, { label: string; timer: NodeJS.Timeout }>();

export function beginNodeJobTracking(
  coordinatorDir: string,
  jobId: string,
  label: string,
): void {
  try {
    registerJob(coordinatorDir, jobId, label);
  } catch (err) {
    warnRegistryBestEffort(
      `io-fairshare: begin tracking failed (${jobId}): ${(err as Error).message}`,
    );
  }
  const timer = setInterval(() => {
    void touchJobAsync(coordinatorDir, jobId);
  }, 5000);
  if (typeof timer.unref === "function") {
    timer.unref();
  }
  activeNodeJobs.set(jobId, { label, timer });
}

export function endNodeJobTracking(coordinatorDir: string, jobId: string): void {
  const tracked = activeNodeJobs.get(jobId);
  if (tracked) {
    clearInterval(tracked.timer);
    activeNodeJobs.delete(jobId);
  }
  unregisterJob(coordinatorDir, jobId);
}

export function applyIoFairsharePythonEnv(
  baseEnv: NodeJS.ProcessEnv,
  coordinatorDir: string,
  homeDir: string,
  jobId: string,
  jobLabel: string,
): NodeJS.ProcessEnv {
  const env = { ...baseEnv };
  if (!isFairshareEnabled(coordinatorDir, homeDir)) {
    env.MASONJAR_IO_FAIRSHARE = "0";
    return env;
  }
  ensureCoordinatorDir(coordinatorDir);
  const status = getIoFairshareStatus(coordinatorDir, homeDir);
  env.MASONJAR_IO_FAIRSHARE = "1";
  env.MASONJAR_IO_FAIRSHARE_DIR = coordinatorDir;
  env.MASONJAR_IO_JOB_ID = jobId;
  env.MASONJAR_IO_JOB_LABEL = jobLabel;
  env.MASONJAR_IO_LINK_MBPS = String(status.link_mbps);
  env.MASONJAR_IO_HEADROOM = String(status.headroom);
  env.MASONJAR_IO_MIN_MBPS = String(status.min_mbps_per_job);
  env.MASONJAR_IO_MAX_MBPS = String(status.max_mbps_per_job);
  if (appInstanceId) {
    env.MASONJAR_IO_APP_INSTANCE_ID = appInstanceId;
  }
  return env;
}

export function createHeavyJobHandle(
  coordinatorDir: string,
  homeDir: string,
  label: string,
  baseEnv: NodeJS.ProcessEnv,
): { jobId: string; env: NodeJS.ProcessEnv; release: () => void } {
  if (!isFairshareEnabled(coordinatorDir, homeDir)) {
    return {
      jobId: "",
      env: { ...baseEnv, MASONJAR_IO_FAIRSHARE: "0" },
      release: () => undefined,
    };
  }
  const jobId = newJobId();
  beginNodeJobTracking(coordinatorDir, jobId, label);
  const env = applyIoFairsharePythonEnv(
    baseEnv,
    coordinatorDir,
    homeDir,
    jobId,
    label,
  );
  return {
    jobId,
    env,
    release: () => endNodeJobTracking(coordinatorDir, jobId),
  };
}
