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
exports.killAllPythonJobs = exports.runPythonJob = exports.describePythonShellFailure = exports.getActivePythonJobCount = exports.appendPythonJobLog = exports.FORCE_SHELL_SCRIPTS = exports.WORKER_ALLOWLIST = void 0;
/**
 * Central supervisor for Electron-spawned Python children.
 * Always finalizes on close/error (not only on "Done!"), logs jobs to
 * ~/.masonjar/python_jobs.ndjson, and optionally routes allowlisted scripts
 * through a long-lived in-process worker (py/masonjar_worker.py).
 */
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const io_fairshare_1 = require("./io_fairshare");
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { PythonShell } = require("python-shell");
const activeJobs = new Map();
let jobSeq = 0;
let cachedBuildTag = null;
function buildTag() {
    if (cachedBuildTag) {
        return cachedBuildTag;
    }
    try {
        const pkgPath = path.join(__dirname, "package.json");
        const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
        const ver = pkg.version || "0.0.0";
        const devMarker = process.env.MASONJAR_DEV === "1" ||
            fs.existsSync(path.join(__dirname, "src", "main.ts"));
        cachedBuildTag = devMarker ? `${ver}-dev` : ver;
    }
    catch (_err) {
        cachedBuildTag = "unknown";
    }
    return cachedBuildTag;
}
/** Terminate the long-lived worker process so in-process runpy jobs cannot continue. */
function hardStopWorker() {
    const shell = workerShell;
    workerShell = null;
    workerReady = false;
    workerStartPromise = null;
    if (!shell) {
        return;
    }
    try {
        shell.kill();
    }
    catch (_err) {
        // ignore
    }
}
function invokeKill(record) {
    var _a;
    try {
        (_a = record.onKill) === null || _a === void 0 ? void 0 : _a.call(record);
    }
    catch (_err) {
        // ignore
    }
    if (record.via === "worker") {
        // Cooperative flag first (harmless if script ignores it).
        sendWorker({ cmd: "cancel", id: record.jobId });
        const pending = workerPending.get(record.jobId);
        if (pending) {
            workerPending.delete(record.jobId);
            finalizeJob(record, { err: new Error("cancelled"), code: 1, signal: "SIGTERM" }, pending.homeDir);
        }
        // Hard-stop: masonjar_worker runs scripts in-process via runpy; cancel alone
        // cannot preempt blocked CZI/IO. Kill the worker so writes stop promptly.
        hardStopWorker();
        return;
    }
    const shell = record.pyshell;
    if (shell) {
        try {
            shell.kill();
        }
        catch (_err) {
            // ignore
        }
    }
}
/** Scripts that may run inside the long-lived worker (no Qt/Napari). */
exports.WORKER_ALLOWLIST = new Set([
    "index_metadata.py",
    "max.py",
    "sharpen.py",
    "top_hat.py",
    "basic_correct.py",
    "region.py",
    "count.py",
    "collate.py",
    "find_neurons.py",
    "export_roi_dual_tif.py",
    "apply_parcellation.py",
    "dapi_cleanup.py",
    "tissue_cleanup.py",
    "czi_probe.py",
    "czi_extract.py",
    "apply_geometry.py",
    "geometry_fingerprint_probe.py",
    "annotation_label_audit.py",
]);
/** GUI / interactive — always one-shot shell. */
exports.FORCE_SHELL_SCRIPTS = new Set([
    "map.py",
    "adjust.py",
]);
function envForWorker(env) {
    const out = {};
    for (const [k, v] of Object.entries(env)) {
        if (v == null || v === "") {
            continue;
        }
        if (k.startsWith("MASONJAR_") ||
            k === "PYTHONIOENCODING" ||
            k === "PYTORCH_ENABLE_MPS_FALLBACK" ||
            k.startsWith("OMP_") ||
            k.startsWith("MKL_") ||
            k.startsWith("TORCH_")) {
            out[k] = String(v);
        }
    }
    return out;
}
function workerEnabled() {
    const v = process.env.MASONJAR_PYTHON_WORKER;
    if (v === "0" || v === "false" || v === "off") {
        return false;
    }
    // Default on so the lab gets churn reduction; set MASONJAR_PYTHON_WORKER=0 to disable.
    return true;
}
function useWorkerFor(script, forceShell) {
    if (forceShell || exports.FORCE_SHELL_SCRIPTS.has(script)) {
        return false;
    }
    if (!workerEnabled()) {
        return false;
    }
    return exports.WORKER_ALLOWLIST.has(script);
}
function appendPythonJobLog(homeDir, record) {
    try {
        const dir = homeDir;
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
        const file = path.join(dir, "python_jobs.ndjson");
        fs.appendFileSync(file, `${JSON.stringify(record)}\n`, "utf8");
    }
    catch (_err) {
        // best effort
    }
}
exports.appendPythonJobLog = appendPythonJobLog;
function getActivePythonJobCount() {
    return activeJobs.size;
}
exports.getActivePythonJobCount = getActivePythonJobCount;
function describePythonShellFailure(err, code, signal) {
    var _a;
    const c = typeof code === "number" ? code : null;
    const hasErr = err != null && err !== false;
    const badExit = c != null && c !== 0;
    if (!hasErr && !badExit) {
        return null;
    }
    let msg = "";
    if (hasErr && typeof err === "object" && err !== null) {
        const m = err.message;
        if (typeof m === "string" && m.length > 0) {
            msg = m;
        }
    }
    if (!msg && hasErr) {
        msg = String((_a = err === null || err === void 0 ? void 0 : err.message) !== null && _a !== void 0 ? _a : err);
    }
    const bits = [];
    if (badExit) {
        bits.push(`Python exited with code ${c}`);
    }
    if (msg) {
        bits.push(msg);
    }
    if (typeof signal === "string" && signal.length > 0) {
        bits.push(`signal: ${signal}`);
    }
    return bits.join(" · ") || "Python reported an error.";
}
exports.describePythonShellFailure = describePythonShellFailure;
function finalizeJob(record, exit, homeDir) {
    var _a;
    if (record.finished) {
        return;
    }
    record.finished = true;
    record.exit = exit;
    try {
        record.releaseFairshare();
    }
    catch (_err) {
        // ignore
    }
    if (record.killChannel && record.ipcMain) {
        try {
            record.ipcMain.removeAllListeners(record.killChannel);
        }
        catch (_err) {
            // ignore
        }
    }
    const pyshell = record.pyshell;
    let pid = null;
    if (pyshell) {
        try {
            const child = pyshell.childProcess;
            if (child && typeof child.pid === "number") {
                pid = child.pid;
            }
        }
        catch (_err) {
            // ignore
        }
        try {
            pyshell.removeAllListeners();
        }
        catch (_err) {
            // ignore
        }
        try {
            const child = pyshell.childProcess;
            if (child && typeof child.unref === "function") {
                child.unref();
            }
        }
        catch (_err) {
            // ignore
        }
    }
    record.pyshell = null;
    activeJobs.delete(record.jobId);
    appendPythonJobLog(homeDir, {
        ts: new Date().toISOString(),
        event: "end",
        jobId: record.jobId,
        script: record.script,
        pid,
        code: typeof exit.code === "number" ? exit.code : null,
        signal: typeof exit.signal === "string" ? exit.signal : null,
        label: record.label,
        via: record.via,
        build: record.build,
        gui: record.gui,
        error: exit.err != null && exit.err !== false
            ? String((_a = exit.err.message) !== null && _a !== void 0 ? _a : exit.err)
            : undefined,
    });
    for (const w of record.waiters) {
        try {
            w(exit);
        }
        catch (_err) {
            // ignore
        }
    }
    record.waiters = [];
}
let workerShell = null;
let workerReady = false;
let workerBuf = "";
const workerPending = new Map();
let workerStartPromise = null;
function ensureWorker(pythonPath, scriptPath, baseEnv, onLogError) {
    if (workerShell && workerReady) {
        return Promise.resolve();
    }
    if (workerStartPromise) {
        return workerStartPromise;
    }
    workerStartPromise = new Promise((resolve, reject) => {
        try {
            const options = {
                mode: "text",
                pythonPath,
                scriptPath,
                args: [],
                env: Object.assign({}, baseEnv),
            };
            const shell = new PythonShell("masonjar_worker.py", options);
            workerShell = shell;
            workerReady = false;
            workerBuf = "";
            const onLine = (line) => {
                var _a, _b, _c, _d;
                let msg;
                try {
                    msg = JSON.parse(line);
                }
                catch (_err) {
                    return;
                }
                if (msg.type === "ready") {
                    workerReady = true;
                    resolve();
                    return;
                }
                const id = msg.id;
                if (!id) {
                    return;
                }
                const pending = workerPending.get(id);
                if (!pending) {
                    return;
                }
                if (msg.type === "line" && typeof msg.data === "string") {
                    (_a = pending.onMessage) === null || _a === void 0 ? void 0 : _a.call(pending, msg.data);
                    return;
                }
                if (msg.type === "stderr" && typeof msg.data === "string") {
                    (_b = pending.onStderr) === null || _b === void 0 ? void 0 : _b.call(pending, msg.data);
                    return;
                }
                if (msg.type === "done") {
                    workerPending.delete(id);
                    finalizeJob(pending.record, { err: null, code: (_c = msg.code) !== null && _c !== void 0 ? _c : 0, signal: null }, pending.homeDir);
                    return;
                }
                if (msg.type === "error") {
                    workerPending.delete(id);
                    finalizeJob(pending.record, {
                        err: new Error(msg.message || "worker error"),
                        code: (_d = msg.code) !== null && _d !== void 0 ? _d : 1,
                        signal: null,
                    }, pending.homeDir);
                }
            };
            shell.on("message", (message) => {
                onLine(message);
            });
            shell.on("stderr", (stderr) => {
                onLogError === null || onLogError === void 0 ? void 0 : onLogError(stderr);
            });
            shell.on("close", () => {
                workerShell = null;
                workerReady = false;
                workerStartPromise = null;
                for (const [id, pending] of workerPending) {
                    workerPending.delete(id);
                    finalizeJob(pending.record, { err: new Error("worker exited"), code: 1, signal: null }, pending.homeDir);
                }
            });
            shell.on("error", (err) => {
                onLogError === null || onLogError === void 0 ? void 0 : onLogError(err);
                workerShell = null;
                workerReady = false;
                workerStartPromise = null;
                reject(err);
            });
            // ready may arrive as first message; also timeout
            setTimeout(() => {
                if (!workerReady && workerShell === shell) {
                    workerReady = true;
                    resolve();
                }
            }, 5000);
        }
        catch (err) {
            workerStartPromise = null;
            reject(err);
        }
    });
    return workerStartPromise;
}
function sendWorker(obj) {
    if (!workerShell) {
        return;
    }
    try {
        workerShell.send(JSON.stringify(obj));
    }
    catch (_err) {
        // ignore
    }
}
function shutdownWorker() {
    return __awaiter(this, void 0, void 0, function* () {
        if (!workerShell) {
            return;
        }
        const shell = workerShell;
        sendWorker({ cmd: "shutdown" });
        yield new Promise((resolve) => {
            const t = setTimeout(() => {
                try {
                    shell.kill();
                }
                catch (_err) {
                    // ignore
                }
                resolve();
            }, 2000);
            shell.on("close", () => {
                clearTimeout(t);
                resolve();
            });
        });
        workerShell = null;
        workerReady = false;
        workerStartPromise = null;
    });
}
function resolveFairshareHandle(opts) {
    if (opts.fairshareEnv) {
        return {
            jobId: String(opts.fairshareEnv.MASONJAR_IO_JOB_ID || ""),
            env: Object.assign({}, opts.fairshareEnv),
            release: () => undefined,
        };
    }
    if (opts.label && opts.label.length > 0) {
        return (0, io_fairshare_1.createHeavyJobHandle)(opts.ioFairshareDir, opts.homeDir, opts.label, opts.baseEnv);
    }
    return {
        jobId: "",
        env: Object.assign({}, opts.baseEnv),
        release: () => undefined,
    };
}
function runViaWorker(opts) {
    const jobId = `w${++jobSeq}_${Date.now()}`;
    const fair = resolveFairshareHandle(opts);
    const record = {
        jobId,
        script: opts.script,
        label: opts.label,
        via: "worker",
        pyshell: null,
        releaseFairshare: fair.release,
        killChannel: opts.killChannel,
        ipcMain: opts.ipcMain,
        onKill: opts.onKill,
        build: buildTag(),
        finished: false,
        exit: null,
        waiters: [],
        kill: () => invokeKill(record),
    };
    activeJobs.set(jobId, record);
    appendPythonJobLog(opts.homeDir, {
        ts: new Date().toISOString(),
        event: "start",
        jobId,
        script: opts.script,
        label: opts.label,
        via: "worker",
        build: record.build,
    });
    if (opts.killChannel && opts.ipcMain) {
        opts.ipcMain.once(opts.killChannel, () => {
            invokeKill(record);
        });
    }
    const pending = {
        jobId,
        record,
        homeDir: opts.homeDir,
        onMessage: opts.onMessage,
        onStderr: opts.onStderr,
    };
    workerPending.set(jobId, pending);
    void ensureWorker(opts.pythonPath, opts.scriptPath, opts.baseEnv, opts.onLogError)
        .then(() => {
        sendWorker({
            cmd: "run",
            id: jobId,
            script: opts.script,
            args: opts.args,
            env: envForWorker(fair.env),
        });
    })
        .catch((err) => {
        workerPending.delete(jobId);
        finalizeJob(record, { err, code: 1, signal: null }, opts.homeDir);
    });
    return makeHandle(record, opts.homeDir);
}
function runViaShell(opts) {
    const jobId = `s${++jobSeq}_${Date.now()}`;
    const fair = resolveFairshareHandle(opts);
    const options = {
        mode: "text",
        pythonPath: opts.pythonPath,
        scriptPath: opts.scriptPath,
        args: opts.args,
        env: fair.env,
    };
    const pyshell = new PythonShell(opts.script, options);
    const record = {
        jobId,
        script: opts.script,
        label: opts.label,
        via: "shell",
        pyshell,
        releaseFairshare: fair.release,
        killChannel: opts.killChannel,
        ipcMain: opts.ipcMain,
        onKill: opts.onKill,
        gui: opts.forceShell === true || exports.FORCE_SHELL_SCRIPTS.has(opts.script),
        build: buildTag(),
        finished: false,
        exit: null,
        waiters: [],
        kill: () => invokeKill(record),
    };
    activeJobs.set(jobId, record);
    let pid = null;
    try {
        const child = pyshell.childProcess;
        if (child && typeof child.pid === "number") {
            pid = child.pid;
        }
    }
    catch (_err) {
        // ignore
    }
    appendPythonJobLog(opts.homeDir, {
        ts: new Date().toISOString(),
        event: "start",
        jobId,
        script: opts.script,
        pid,
        label: opts.label,
        via: "shell",
        build: record.build,
        gui: record.gui,
    });
    if (opts.killChannel && opts.ipcMain) {
        opts.ipcMain.once(opts.killChannel, () => {
            invokeKill(record);
        });
    }
    if (opts.onMessage) {
        pyshell.on("message", (message) => {
            var _a;
            (_a = opts.onMessage) === null || _a === void 0 ? void 0 : _a.call(opts, message);
        });
    }
    if (opts.onStderr) {
        pyshell.on("stderr", (stderr) => {
            var _a;
            (_a = opts.onStderr) === null || _a === void 0 ? void 0 : _a.call(opts, stderr.replace(/\r?\n$/, ""));
        });
    }
    pyshell.on("error", (err) => {
        var _a;
        (_a = opts.onLogError) === null || _a === void 0 ? void 0 : _a.call(opts, err);
        finalizeJob(record, { err, code: 1, signal: null }, opts.homeDir);
    });
    pyshell.on("close", (code, signal) => {
        finalizeJob(record, { err: null, code, signal }, opts.homeDir);
    });
    return makeHandle(record, opts.homeDir);
}
function makeHandle(record, homeDir) {
    return {
        jobId: record.jobId,
        script: record.script,
        pyshell: record.pyshell,
        via: record.via,
        pid: () => {
            const shell = record.pyshell;
            if (!shell) {
                return undefined;
            }
            try {
                const child = shell.childProcess;
                return child === null || child === void 0 ? void 0 : child.pid;
            }
            catch (_err) {
                return undefined;
            }
        },
        kill: () => record.kill(),
        wait: () => new Promise((resolve) => {
            if (record.finished && record.exit) {
                resolve(record.exit);
                return;
            }
            record.waiters.push(resolve);
        }),
        end: () => new Promise((resolve) => {
            if (record.finished && record.exit) {
                resolve(record.exit);
                return;
            }
            record.waiters.push(resolve);
            const shell = record.pyshell;
            if (shell && record.via === "shell") {
                try {
                    shell.end((err, code, signal) => {
                        // close handler will finalize; if already finalized, resolve here
                        if (record.finished && record.exit) {
                            resolve(record.exit);
                        }
                        else {
                            finalizeJob(record, { err, code, signal }, homeDir);
                            resolve(record.exit || { err, code, signal });
                        }
                    });
                }
                catch (_err) {
                    record.kill();
                }
            }
        }),
    };
}
/**
 * Start a supervised Python job (one-shot shell or long-lived worker).
 * Callers may attach additional listeners only via onMessage/onStderr options,
 * or use handle.pyshell when via==="shell" (e.g. pyshell.send).
 */
function runPythonJob(opts) {
    if (useWorkerFor(opts.script, opts.forceShell)) {
        return runViaWorker(opts);
    }
    return runViaShell(opts);
}
exports.runPythonJob = runPythonJob;
function killAllPythonJobs(timeoutMs = 5000) {
    return __awaiter(this, void 0, void 0, function* () {
        const jobs = Array.from(activeJobs.values());
        for (const job of jobs) {
            try {
                job.kill();
            }
            catch (_err) {
                // ignore
            }
        }
        yield Promise.race([
            Promise.all(jobs.map((j) => new Promise((resolve) => {
                if (j.finished) {
                    resolve();
                    return;
                }
                j.waiters.push(() => resolve());
            }))),
            new Promise((resolve) => setTimeout(resolve, timeoutMs)),
        ]);
        yield shutdownWorker();
    });
}
exports.killAllPythonJobs = killAllPythonJobs;
