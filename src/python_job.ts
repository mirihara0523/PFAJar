/**
 * Central supervisor for Electron-spawned Python children.
 * Always finalizes on close/error (not only on "Done!"), logs jobs to
 * ~/.masonjar/python_jobs.ndjson, and optionally routes allowlisted scripts
 * through a long-lived in-process worker (py/masonjar_worker.py).
 */
import * as fs from "fs";
import * as path from "path";
import { createHeavyJobHandle } from "./io_fairshare";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { PythonShell } = require("python-shell");

export type PythonJobLogEvent = {
  ts: string;
  event: "start" | "end";
  jobId: string;
  script: string;
  pid?: number | null;
  code?: number | null;
  signal?: string | null;
  label?: string;
  via?: "shell" | "worker";
  /** App version + optional -dev suffix for mixed-fleet correlation. */
  build?: string;
  /** True for forceShell GUI tools (map.py, adjust.py). */
  gui?: boolean;
  error?: string;
};

export type RunPythonJobOptions = {
  script: string;
  args: string[];
  pythonPath: string;
  scriptPath: string;
  /** Fairshare label; when set, registers a heavy job handle. */
  label?: string;
  /**
   * Reuse an existing fairshare env (e.g. parent project_index session).
   * When set, skips createHeavyJobHandle and does not release on job end.
   */
  fairshareEnv?: NodeJS.ProcessEnv;
  homeDir: string;
  ioFairshareDir: string;
  baseEnv: NodeJS.ProcessEnv;
  /** When set, registers ipcMain.once(killChannel) → kill(). */
  killChannel?: string;
  /** Electron ipcMain (required if killChannel set). */
  ipcMain?: {
    once: (channel: string, listener: (...args: unknown[]) => void) => void;
    removeAllListeners: (channel: string) => void;
  };
  onMessage?: (message: string) => void;
  onStderr?: (line: string) => void;
  onLogError?: (err: unknown) => void;
  /** Force one-shot PythonShell even if script is worker-allowlisted. */
  forceShell?: boolean;
  /** Called when killChannel fires or handle.kill() runs (GUI cleanup hooks). */
  onKill?: () => void;
};

export type PythonJobHandle = {
  jobId: string;
  script: string;
  /** Underlying PythonShell when via=shell; null when via=worker. */
  pyshell: InstanceType<typeof PythonShell> | null;
  via: "shell" | "worker";
  pid: () => number | undefined;
  kill: () => void;
  /** Resolves when the child has fully exited and cleanup ran. */
  wait: () => Promise<PythonJobExit>;
  /** Drain stdio and wait for exit (python-shell end). No-op for worker jobs that already finished. */
  end: () => Promise<PythonJobExit>;
};

export type PythonJobExit = {
  err: unknown;
  code: unknown;
  signal: unknown;
};

type JobRecord = {
  jobId: string;
  script: string;
  label?: string;
  via: "shell" | "worker";
  pyshell: InstanceType<typeof PythonShell> | null;
  releaseFairshare: () => void;
  killChannel?: string;
  ipcMain?: RunPythonJobOptions["ipcMain"];
  onKill?: () => void;
  gui?: boolean;
  build?: string;
  finished: boolean;
  exit: PythonJobExit | null;
  waiters: Array<(exit: PythonJobExit) => void>;
  kill: () => void;
};

const activeJobs = new Map<string, JobRecord>();
let jobSeq = 0;

let cachedBuildTag: string | null = null;

function buildTag(): string {
  if (cachedBuildTag) {
    return cachedBuildTag;
  }
  try {
    const pkgPath = path.join(__dirname, "package.json");
    const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8")) as { version?: string };
    const ver = pkg.version || "0.0.0";
    const devMarker =
      process.env.MASONJAR_DEV === "1" ||
      fs.existsSync(path.join(__dirname, "src", "main.ts"));
    cachedBuildTag = devMarker ? `${ver}-dev` : ver;
  } catch (_err) {
    cachedBuildTag = "unknown";
  }
  return cachedBuildTag;
}

/** Terminate the long-lived worker process so in-process runpy jobs cannot continue. */
function hardStopWorker(): void {
  const shell = workerShell;
  workerShell = null;
  workerReady = false;
  workerStartPromise = null;
  if (!shell) {
    return;
  }
  try {
    shell.kill();
  } catch (_err) {
    // ignore
  }
}

function invokeKill(record: JobRecord): void {
  try {
    record.onKill?.();
  } catch (_err) {
    // ignore
  }
  if (record.via === "worker") {
    // Cooperative flag first (harmless if script ignores it).
    sendWorker({ cmd: "cancel", id: record.jobId });
    const pending = workerPending.get(record.jobId);
    if (pending) {
      workerPending.delete(record.jobId);
      finalizeJob(
        record,
        { err: new Error("cancelled"), code: 1, signal: "SIGTERM" },
        pending.homeDir,
      );
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
    } catch (_err) {
      // ignore
    }
  }
}

/** Scripts that may run inside the long-lived worker (no Qt/Napari). */
export const WORKER_ALLOWLIST = new Set([
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
export const FORCE_SHELL_SCRIPTS = new Set([
  "map.py",
  "adjust.py",
]);

function envForWorker(env: NodeJS.ProcessEnv): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) {
    if (v == null || v === "") {
      continue;
    }
    if (
      k.startsWith("MASONJAR_") ||
      k === "PYTHONIOENCODING" ||
      k === "PYTORCH_ENABLE_MPS_FALLBACK" ||
      k.startsWith("OMP_") ||
      k.startsWith("MKL_") ||
      k.startsWith("TORCH_")
    ) {
      out[k] = String(v);
    }
  }
  return out;
}

function workerEnabled(): boolean {
  const v = process.env.MASONJAR_PYTHON_WORKER;
  if (v === "0" || v === "false" || v === "off") {
    return false;
  }
  // Default on so the lab gets churn reduction; set MASONJAR_PYTHON_WORKER=0 to disable.
  return true;
}

function useWorkerFor(script: string, forceShell?: boolean): boolean {
  if (forceShell || FORCE_SHELL_SCRIPTS.has(script)) {
    return false;
  }
  if (!workerEnabled()) {
    return false;
  }
  return WORKER_ALLOWLIST.has(script);
}

export function appendPythonJobLog(
  homeDir: string,
  record: PythonJobLogEvent,
): void {
  try {
    const dir = homeDir;
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    const file = path.join(dir, "python_jobs.ndjson");
    fs.appendFileSync(file, `${JSON.stringify(record)}\n`, "utf8");
  } catch (_err) {
    // best effort
  }
}

export function getActivePythonJobCount(): number {
  return activeJobs.size;
}

export function describePythonShellFailure(
  err: unknown,
  code: unknown,
  signal: unknown,
): string | null {
  const c = typeof code === "number" ? code : null;
  const hasErr = err != null && err !== false;
  const badExit = c != null && c !== 0;
  if (!hasErr && !badExit) {
    return null;
  }
  let msg = "";
  if (hasErr && typeof err === "object" && err !== null) {
    const m = (err as { message?: string }).message;
    if (typeof m === "string" && m.length > 0) {
      msg = m;
    }
  }
  if (!msg && hasErr) {
    msg = String((err as { message?: unknown })?.message ?? err);
  }
  const bits: string[] = [];
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

function finalizeJob(record: JobRecord, exit: PythonJobExit, homeDir: string): void {
  if (record.finished) {
    return;
  }
  record.finished = true;
  record.exit = exit;
  try {
    record.releaseFairshare();
  } catch (_err) {
    // ignore
  }
  if (record.killChannel && record.ipcMain) {
    try {
      record.ipcMain.removeAllListeners(record.killChannel);
    } catch (_err) {
      // ignore
    }
  }
  const pyshell = record.pyshell;
  let pid: number | null = null;
  if (pyshell) {
    try {
      const child = (pyshell as { childProcess?: { pid?: number } }).childProcess;
      if (child && typeof child.pid === "number") {
        pid = child.pid;
      }
    } catch (_err) {
      // ignore
    }
    try {
      pyshell.removeAllListeners();
    } catch (_err) {
      // ignore
    }
    try {
      const child = (pyshell as { childProcess?: { unref?: () => void } }).childProcess;
      if (child && typeof child.unref === "function") {
        child.unref();
      }
    } catch (_err) {
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
    error:
      exit.err != null && exit.err !== false
        ? String((exit.err as { message?: string }).message ?? exit.err)
        : undefined,
  });
  for (const w of record.waiters) {
    try {
      w(exit);
    } catch (_err) {
      // ignore
    }
  }
  record.waiters = [];
}

// --- Long-lived worker ----------------------------------------------------

type WorkerPending = {
  jobId: string;
  record: JobRecord;
  homeDir: string;
  onMessage?: (message: string) => void;
  onStderr?: (line: string) => void;
};

let workerShell: InstanceType<typeof PythonShell> | null = null;
let workerReady = false;
let workerBuf = "";
const workerPending = new Map<string, WorkerPending>();
let workerStartPromise: Promise<void> | null = null;

function ensureWorker(
  pythonPath: string,
  scriptPath: string,
  baseEnv: NodeJS.ProcessEnv,
  onLogError?: (err: unknown) => void,
): Promise<void> {
  if (workerShell && workerReady) {
    return Promise.resolve();
  }
  if (workerStartPromise) {
    return workerStartPromise;
  }
  workerStartPromise = new Promise((resolve, reject) => {
    try {
      const options = {
        mode: "text" as const,
        pythonPath,
        scriptPath,
        args: [],
        env: { ...baseEnv },
      };
      const shell = new PythonShell("masonjar_worker.py", options);
      workerShell = shell;
      workerReady = false;
      workerBuf = "";

      const onLine = (line: string) => {
        let msg: { type?: string; id?: string; data?: string; code?: number; message?: string };
        try {
          msg = JSON.parse(line);
        } catch (_err) {
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
          pending.onMessage?.(msg.data);
          return;
        }
        if (msg.type === "stderr" && typeof msg.data === "string") {
          pending.onStderr?.(msg.data);
          return;
        }
        if (msg.type === "done") {
          workerPending.delete(id);
          finalizeJob(
            pending.record,
            { err: null, code: msg.code ?? 0, signal: null },
            pending.homeDir,
          );
          return;
        }
        if (msg.type === "error") {
          workerPending.delete(id);
          finalizeJob(
            pending.record,
            {
              err: new Error(msg.message || "worker error"),
              code: msg.code ?? 1,
              signal: null,
            },
            pending.homeDir,
          );
        }
      };

      shell.on("message", (message: string) => {
        onLine(message);
      });
      shell.on("stderr", (stderr: string) => {
        onLogError?.(stderr);
      });
      shell.on("close", () => {
        workerShell = null;
        workerReady = false;
        workerStartPromise = null;
        for (const [id, pending] of workerPending) {
          workerPending.delete(id);
          finalizeJob(
            pending.record,
            { err: new Error("worker exited"), code: 1, signal: null },
            pending.homeDir,
          );
        }
      });
      shell.on("error", (err: unknown) => {
        onLogError?.(err);
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
    } catch (err) {
      workerStartPromise = null;
      reject(err);
    }
  });
  return workerStartPromise;
}

function sendWorker(obj: Record<string, unknown>): void {
  if (!workerShell) {
    return;
  }
  try {
    workerShell.send(JSON.stringify(obj));
  } catch (_err) {
    // ignore
  }
}

async function shutdownWorker(): Promise<void> {
  if (!workerShell) {
    return;
  }
  const shell = workerShell;
  sendWorker({ cmd: "shutdown" });
  await new Promise<void>((resolve) => {
    const t = setTimeout(() => {
      try {
        shell.kill();
      } catch (_err) {
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
}

function resolveFairshareHandle(opts: RunPythonJobOptions): {
  jobId: string;
  env: NodeJS.ProcessEnv;
  release: () => void;
} {
  if (opts.fairshareEnv) {
    return {
      jobId: String(opts.fairshareEnv.MASONJAR_IO_JOB_ID || ""),
      env: { ...opts.fairshareEnv },
      release: () => undefined,
    };
  }
  if (opts.label && opts.label.length > 0) {
    return createHeavyJobHandle(
      opts.ioFairshareDir,
      opts.homeDir,
      opts.label,
      opts.baseEnv,
    );
  }
  return {
    jobId: "",
    env: { ...opts.baseEnv },
    release: () => undefined,
  };
}

function runViaWorker(opts: RunPythonJobOptions): PythonJobHandle {
  const jobId = `w${++jobSeq}_${Date.now()}`;
  const fair = resolveFairshareHandle(opts);

  const record: JobRecord = {
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

  const pending: WorkerPending = {
    jobId,
    record,
    homeDir: opts.homeDir,
    onMessage: opts.onMessage,
    onStderr: opts.onStderr,
  };
  workerPending.set(jobId, pending);

  void ensureWorker(
    opts.pythonPath,
    opts.scriptPath,
    opts.baseEnv,
    opts.onLogError,
  )
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
      finalizeJob(
        record,
        { err, code: 1, signal: null },
        opts.homeDir,
      );
    });

  return makeHandle(record, opts.homeDir);
}

function runViaShell(opts: RunPythonJobOptions): PythonJobHandle {
  const jobId = `s${++jobSeq}_${Date.now()}`;
  const fair = resolveFairshareHandle(opts);

  const options = {
    mode: "text" as const,
    pythonPath: opts.pythonPath,
    scriptPath: opts.scriptPath,
    args: opts.args,
    env: fair.env,
  };
  const pyshell = new PythonShell(opts.script, options);

  const record: JobRecord = {
    jobId,
    script: opts.script,
    label: opts.label,
    via: "shell",
    pyshell,
    releaseFairshare: fair.release,
    killChannel: opts.killChannel,
    ipcMain: opts.ipcMain,
    onKill: opts.onKill,
    gui: opts.forceShell === true || FORCE_SHELL_SCRIPTS.has(opts.script),
    build: buildTag(),
    finished: false,
    exit: null,
    waiters: [],
    kill: () => invokeKill(record),
  };
  activeJobs.set(jobId, record);

  let pid: number | null = null;
  try {
    const child = (pyshell as { childProcess?: { pid?: number } }).childProcess;
    if (child && typeof child.pid === "number") {
      pid = child.pid;
    }
  } catch (_err) {
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
    pyshell.on("message", (message: string) => {
      opts.onMessage?.(message);
    });
  }
  if (opts.onStderr) {
    pyshell.on("stderr", (stderr: string) => {
      opts.onStderr?.(stderr.replace(/\r?\n$/, ""));
    });
  }

  pyshell.on("error", (err: unknown) => {
    opts.onLogError?.(err);
    finalizeJob(record, { err, code: 1, signal: null }, opts.homeDir);
  });

  pyshell.on("close", (code: unknown, signal: unknown) => {
    finalizeJob(record, { err: null, code, signal }, opts.homeDir);
  });

  return makeHandle(record, opts.homeDir);
}

function makeHandle(record: JobRecord, homeDir: string): PythonJobHandle {
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
        const child = (shell as { childProcess?: { pid?: number } }).childProcess;
        return child?.pid;
      } catch (_err) {
        return undefined;
      }
    },
    kill: () => record.kill(),
    wait: () =>
      new Promise<PythonJobExit>((resolve) => {
        if (record.finished && record.exit) {
          resolve(record.exit);
          return;
        }
        record.waiters.push(resolve);
      }),
    end: () =>
      new Promise<PythonJobExit>((resolve) => {
        if (record.finished && record.exit) {
          resolve(record.exit);
          return;
        }
        record.waiters.push(resolve);
        const shell = record.pyshell;
        if (shell && record.via === "shell") {
          try {
            shell.end((err: unknown, code: unknown, signal: unknown) => {
              // close handler will finalize; if already finalized, resolve here
              if (record.finished && record.exit) {
                resolve(record.exit);
              } else {
                finalizeJob(record, { err, code, signal }, homeDir);
                resolve(record.exit || { err, code, signal });
              }
            });
          } catch (_err) {
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
export function runPythonJob(opts: RunPythonJobOptions): PythonJobHandle {
  if (useWorkerFor(opts.script, opts.forceShell)) {
    return runViaWorker(opts);
  }
  return runViaShell(opts);
}

export async function killAllPythonJobs(timeoutMs = 5000): Promise<void> {
  const jobs = Array.from(activeJobs.values());
  for (const job of jobs) {
    try {
      job.kill();
    } catch (_err) {
      // ignore
    }
  }
  await Promise.race([
    Promise.all(
      jobs.map(
        (j) =>
          new Promise<void>((resolve) => {
            if (j.finished) {
              resolve();
              return;
            }
            j.waiters.push(() => resolve());
          }),
      ),
    ),
    new Promise<void>((resolve) => setTimeout(resolve, timeoutMs)),
  ]);
  await shutdownWorker();
}
