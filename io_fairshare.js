"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.createHeavyJobHandle = exports.applyIoFairsharePythonEnv = exports.endNodeJobTracking = exports.beginNodeJobTracking = exports.unregisterJob = exports.touchJobAsync = exports.touchJob = exports.registerJob = exports.newJobId = exports.getIoFairshareStatus = exports.formatFairshareCompactLine = exports.formatFairshareTitleSuffix = exports.isFairshareEnabled = exports.computeJobLimitMbps = exports.listRegistryEntries = exports.saveUserConfig = exports.loadUserConfig = exports.saveSharedConfig = exports.loadSharedConfig = exports.ensureCoordinatorDir = exports.detectLinkMbps = exports.parseLinkSpeedText = exports.writeJsonAtomic = exports.mergeNasPathPrefixes = exports.normalizeNasPathPrefix = exports.getSharedConfigPath = exports.userConfigPath = exports.defaultCoordinatorDir = exports.resetLinkSpeedCache = exports.getAppInstanceId = exports.setAppInstanceId = void 0;
const crypto = __importStar(require("crypto"));
const fs = __importStar(require("fs"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
const child_process_1 = require("child_process");
const DEFAULT_SHARED = {
    enabled: true,
    link_mbps: "auto",
    headroom: 0.85,
    min_mbps_per_job: 25,
    max_mbps_per_job: "auto",
    small_file_bytes: 256 * 1024,
    stale_seconds: 30,
    nas_path_prefixes: [],
};
let cachedLinkMbps = null;
let appInstanceId = "";
let fairshareStatusCache = null;
function setAppInstanceId(id) {
    appInstanceId = String(id || "").trim();
}
exports.setAppInstanceId = setAppInstanceId;
function getAppInstanceId() {
    return appInstanceId;
}
exports.getAppInstanceId = getAppInstanceId;
function resetLinkSpeedCache() {
    cachedLinkMbps = null;
}
exports.resetLinkSpeedCache = resetLinkSpeedCache;
function defaultCoordinatorDir() {
    const override = process.env.MASONJAR_IO_FAIRSHARE_DIR;
    if (override && override.trim().length > 0) {
        return path.resolve(override.trim());
    }
    if (process.platform === "win32") {
        const programData = process.env.ProgramData || path.join("C:", "ProgramData");
        return path.join(programData, "MasonJar", "io-fairshare");
    }
    if (process.platform === "darwin") {
        return "/Library/Application Support/MasonJar/io-fairshare";
    }
    return path.join("/var", "run", "masonjar-io-fairshare");
}
exports.defaultCoordinatorDir = defaultCoordinatorDir;
function userConfigPath(homeDir) {
    return path.join(homeDir, "io_fairshare.json");
}
exports.userConfigPath = userConfigPath;
function sharedConfigPath(coordinatorDir) {
    return path.join(coordinatorDir, "config.json");
}
function getSharedConfigPath(coordinatorDir) {
    return sharedConfigPath(coordinatorDir);
}
exports.getSharedConfigPath = getSharedConfigPath;
function prefixKey(text) {
    if (process.platform === "win32") {
        return text.replace(/\//g, "\\").toLowerCase();
    }
    return text;
}
/** Normalize a picked folder to a NAS throttle root (drive letter or UNC share). */
function normalizeNasPathPrefix(absPath) {
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
exports.normalizeNasPathPrefix = normalizeNasPathPrefix;
function mergeNasPathPrefixes(existing, added) {
    const out = [];
    const seen = new Set();
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
exports.mergeNasPathPrefixes = mergeNasPathPrefixes;
function registryDir(coordinatorDir) {
    return path.join(coordinatorDir, "registry");
}
function readJsonFile(filePath) {
    try {
        if (!fs.existsSync(filePath)) {
            return null;
        }
        return JSON.parse(fs.readFileSync(filePath, "utf8"));
    }
    catch (_err) {
        return null;
    }
}
function readJsonFileAsync(filePath) {
    return __awaiter(this, void 0, void 0, function* () {
        try {
            const raw = yield fs.promises.readFile(filePath, "utf8");
            return JSON.parse(raw);
        }
        catch (_err) {
            return null;
        }
    });
}
function isRegistryLockError(err) {
    const code = err === null || err === void 0 ? void 0 : err.code;
    return code === "EPERM" || code === "EACCES" || code === "EBUSY";
}
function sleepMs(ms) {
    if (ms <= 0) {
        return;
    }
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
        /* brief spin for registry lock retry */
    }
}
function removeFileBestEffort(filePath) {
    try {
        if (fs.existsSync(filePath)) {
            fs.unlinkSync(filePath);
        }
    }
    catch (_err) {
        /* ignore */
    }
}
function writeJsonWithRetry(filePath, payload, maxAttempts = 5) {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        try {
            fs.writeFileSync(filePath, payload, "utf8");
            return;
        }
        catch (err) {
            if (attempt < maxAttempts - 1 && isRegistryLockError(err)) {
                sleepMs(25 * (attempt + 1));
                continue;
            }
            throw err;
        }
    }
}
let lastRegistryWarnAt = 0;
function warnRegistryBestEffort(message) {
    const now = Date.now();
    if (now - lastRegistryWarnAt < 60000) {
        return;
    }
    lastRegistryWarnAt = now;
    console.warn(message);
}
/** Atomic JSON write; falls back to in-place retry on Windows file locks. */
function writeJsonAtomic(filePath, data) {
    const dir = path.dirname(filePath);
    fs.mkdirSync(dir, { recursive: true });
    const payload = JSON.stringify(data, null, 2);
    const tmp = `${filePath}.${process.pid}.${Date.now()}.tmp`;
    fs.writeFileSync(tmp, payload, "utf8");
    try {
        try {
            fs.renameSync(tmp, filePath);
        }
        catch (err) {
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
    }
    finally {
        removeFileBestEffort(tmp);
    }
}
exports.writeJsonAtomic = writeJsonAtomic;
function parseLinkSpeedText(raw) {
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
exports.parseLinkSpeedText = parseLinkSpeedText;
function detectLinkMbps() {
    if (cachedLinkMbps != null && cachedLinkMbps > 0) {
        return cachedLinkMbps;
    }
    let detected = null;
    try {
        if (process.platform === "win32") {
            const cmd = 'powershell -NoProfile -Command "Get-NetAdapter | Where-Object Status -eq \'Up\' | Sort-Object LinkSpeed -Descending | Select-Object -First 1 -ExpandProperty LinkSpeed"';
            const out = (0, child_process_1.execSync)(cmd, { encoding: "utf8", timeout: 8000 }).trim();
            detected = parseLinkSpeedText(out);
        }
        else if (process.platform === "darwin") {
            try {
                const out = (0, child_process_1.execSync)("networksetup -listallhardwareports", {
                    encoding: "utf8",
                    timeout: 5000,
                });
                const devMatch = out.match(/Device:\s*(en\d+)/);
                if (devMatch) {
                    const ifOut = (0, child_process_1.execSync)(`ifconfig ${devMatch[1]}`, {
                        encoding: "utf8",
                        timeout: 5000,
                    });
                    const media = ifOut.match(/media:\s*[^\n]*?(\\d+)baseT/i);
                    if (media) {
                        detected = parseInt(media[1], 10);
                    }
                }
            }
            catch (_err) {
                /* fall through */
            }
            if (detected == null) {
                detected = 1000;
            }
        }
    }
    catch (_err) {
        detected = null;
    }
    cachedLinkMbps = detected != null && detected > 0 ? detected : 1000;
    return cachedLinkMbps;
}
exports.detectLinkMbps = detectLinkMbps;
function ensureCoordinatorDir(coordinatorDir) {
    try {
        fs.mkdirSync(registryDir(coordinatorDir), { recursive: true });
        const cfgPath = sharedConfigPath(coordinatorDir);
        if (!fs.existsSync(cfgPath)) {
            writeJsonAtomic(cfgPath, DEFAULT_SHARED);
        }
        return true;
    }
    catch (_err) {
        return false;
    }
}
exports.ensureCoordinatorDir = ensureCoordinatorDir;
function loadSharedConfig(coordinatorDir) {
    const parsed = readJsonFile(sharedConfigPath(coordinatorDir));
    return Object.assign(Object.assign({}, DEFAULT_SHARED), (parsed || {}));
}
exports.loadSharedConfig = loadSharedConfig;
function saveSharedConfig(coordinatorDir, patch) {
    ensureCoordinatorDir(coordinatorDir);
    const merged = Object.assign(Object.assign({}, loadSharedConfig(coordinatorDir)), patch);
    writeJsonAtomic(sharedConfigPath(coordinatorDir), merged);
    return merged;
}
exports.saveSharedConfig = saveSharedConfig;
function loadUserConfig(homeDir) {
    return readJsonFile(userConfigPath(homeDir)) || {};
}
exports.loadUserConfig = loadUserConfig;
function saveUserConfig(homeDir, patch) {
    const merged = Object.assign(Object.assign({}, loadUserConfig(homeDir)), patch);
    fs.mkdirSync(homeDir, { recursive: true });
    writeJsonAtomic(userConfigPath(homeDir), merged);
    return merged;
}
exports.saveUserConfig = saveUserConfig;
function resolveLinkMbps(shared, user) {
    const pick = user.link_mbps != null ? user.link_mbps : shared.link_mbps;
    if (pick === "auto") {
        return detectLinkMbps();
    }
    return Math.max(1, Number(pick) || 1000);
}
function resolveMaxMbps(shared, linkMbps) {
    if (shared.max_mbps_per_job === "auto") {
        return Math.max(shared.min_mbps_per_job, linkMbps * shared.headroom);
    }
    return Math.max(shared.min_mbps_per_job, Number(shared.max_mbps_per_job));
}
function listRegistryEntries(coordinatorDir, staleSeconds) {
    const dir = registryDir(coordinatorDir);
    if (!fs.existsSync(dir)) {
        return [];
    }
    const now = Date.now();
    const out = [];
    for (const name of fs.readdirSync(dir)) {
        if (!name.endsWith(".json")) {
            continue;
        }
        const full = path.join(dir, name);
        const entry = readJsonFile(full);
        if (!entry || !entry.last_heartbeat) {
            try {
                fs.unlinkSync(full);
            }
            catch (_err) {
                /* ignore */
            }
            continue;
        }
        const ageMs = now - Date.parse(entry.last_heartbeat);
        if (!Number.isFinite(ageMs) || ageMs > staleSeconds * 1000) {
            try {
                fs.unlinkSync(full);
            }
            catch (_err) {
                /* ignore */
            }
            continue;
        }
        out.push(entry);
    }
    return out;
}
exports.listRegistryEntries = listRegistryEntries;
function computeJobLimitMbps(shared, linkMbps, activeJobs) {
    const jobs = Math.max(1, activeJobs);
    const budget = linkMbps * shared.headroom;
    const maxCap = resolveMaxMbps(shared, linkMbps);
    const raw = budget / jobs;
    return Math.min(maxCap, Math.max(shared.min_mbps_per_job, raw));
}
exports.computeJobLimitMbps = computeJobLimitMbps;
function isFairshareEnabled(coordinatorDir, homeDir) {
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
exports.isFairshareEnabled = isFairshareEnabled;
function sumLocalThrottledMbps1m(entries, instanceId) {
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
function formatFairshareTitleSuffix(status) {
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
exports.formatFairshareTitleSuffix = formatFairshareTitleSuffix;
function formatFairshareCompactLine(status) {
    if (!status || !status.enabled) {
        return "";
    }
    const jobs = status.active_jobs || 0;
    const limit = Math.round(status.limit_mbps || 0);
    let line = "Network share: " +
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
exports.formatFairshareCompactLine = formatFairshareCompactLine;
function getIoFairshareStatus(coordinatorDir, homeDir) {
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
    const value = {
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
exports.getIoFairshareStatus = getIoFairshareStatus;
function newJobId() {
    if (typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }
    return crypto.randomBytes(16).toString("hex");
}
exports.newJobId = newJobId;
function registerJob(coordinatorDir, jobId, label) {
    try {
        ensureCoordinatorDir(coordinatorDir);
        const entry = {
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
    }
    catch (err) {
        warnRegistryBestEffort(`io-fairshare: registry register failed (${jobId}): ${err.message}`);
    }
}
exports.registerJob = registerJob;
function touchJob(coordinatorDir, jobId) {
    try {
        const filePath = path.join(registryDir(coordinatorDir), `${jobId}.json`);
        const entry = readJsonFile(filePath);
        if (!entry) {
            return;
        }
        entry.last_heartbeat = new Date().toISOString();
        writeJsonAtomic(filePath, entry);
    }
    catch (err) {
        warnRegistryBestEffort(`io-fairshare: registry heartbeat failed (${jobId}): ${err.message}`);
    }
}
exports.touchJob = touchJob;
function touchJobAsync(coordinatorDir, jobId) {
    return __awaiter(this, void 0, void 0, function* () {
        try {
            const filePath = path.join(registryDir(coordinatorDir), `${jobId}.json`);
            const entry = yield readJsonFileAsync(filePath);
            if (!entry)
                return;
            entry.last_heartbeat = new Date().toISOString();
            const payload = JSON.stringify(entry, null, 2);
            yield fs.promises.writeFile(filePath, payload, "utf8");
        }
        catch (err) {
            warnRegistryBestEffort(`io-fairshare: async registry heartbeat failed (${jobId}): ${err.message}`);
        }
    });
}
exports.touchJobAsync = touchJobAsync;
function unregisterJob(coordinatorDir, jobId) {
    const filePath = path.join(registryDir(coordinatorDir), `${jobId}.json`);
    try {
        if (fs.existsSync(filePath)) {
            fs.unlinkSync(filePath);
        }
    }
    catch (_err) {
        /* ignore */
    }
}
exports.unregisterJob = unregisterJob;
const activeNodeJobs = new Map();
function beginNodeJobTracking(coordinatorDir, jobId, label) {
    try {
        registerJob(coordinatorDir, jobId, label);
    }
    catch (err) {
        warnRegistryBestEffort(`io-fairshare: begin tracking failed (${jobId}): ${err.message}`);
    }
    const timer = setInterval(() => {
        void touchJobAsync(coordinatorDir, jobId);
    }, 5000);
    if (typeof timer.unref === "function") {
        timer.unref();
    }
    activeNodeJobs.set(jobId, { label, timer });
}
exports.beginNodeJobTracking = beginNodeJobTracking;
function endNodeJobTracking(coordinatorDir, jobId) {
    const tracked = activeNodeJobs.get(jobId);
    if (tracked) {
        clearInterval(tracked.timer);
        activeNodeJobs.delete(jobId);
    }
    unregisterJob(coordinatorDir, jobId);
}
exports.endNodeJobTracking = endNodeJobTracking;
function applyIoFairsharePythonEnv(baseEnv, coordinatorDir, homeDir, jobId, jobLabel) {
    const env = Object.assign({}, baseEnv);
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
exports.applyIoFairsharePythonEnv = applyIoFairsharePythonEnv;
function createHeavyJobHandle(coordinatorDir, homeDir, label, baseEnv) {
    if (!isFairshareEnabled(coordinatorDir, homeDir)) {
        return {
            jobId: "",
            env: Object.assign(Object.assign({}, baseEnv), { MASONJAR_IO_FAIRSHARE: "0" }),
            release: () => undefined,
        };
    }
    const jobId = newJobId();
    beginNodeJobTracking(coordinatorDir, jobId, label);
    const env = applyIoFairsharePythonEnv(baseEnv, coordinatorDir, homeDir, jobId, label);
    return {
        jobId,
        env,
        release: () => endNodeJobTracking(coordinatorDir, jobId),
    };
}
exports.createHeavyJobHandle = createHeavyJobHandle;
