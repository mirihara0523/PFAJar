import * as fs from "fs";
import * as path from "path";

/**
 * Path helpers for the batch queue (main process). Path-resolution logic
 * (active-run leaves, sharpen → max-branch fallback, etc.) lives in
 * `js/pipeline_runs.js` so the renderer preflight matrix and the main
 * runtime stay in lockstep. We require the shared module at runtime to
 * avoid TS module-resolution against `src/js/...`.
 */
type PipelineRunsModule = {
  CANONICAL_ROLES: Record<string, string>;
  RUN_STEP_CONFIG: Record<
    string,
    {
      stepId: string;
      outputRole: string | null;
      branch: string | null;
      inputRoles: string[];
      scriptRoles?: Record<string, string>;
    }
  >;
  resolvePathsForBundleStep: (
    bundleRoot: string,
    roles: Record<string, string>,
    processing: unknown,
    stepId: string,
  ) => Record<string, string>;
  resolveActiveRunLeafAbsForBundle: (
    bundleRoot: string,
    roles: Record<string, string>,
    processing: unknown,
    role: string,
  ) => string;
  resolveInputLeafAbsForStepBundle: (
    bundleRoot: string,
    roles: Record<string, string>,
    processing: unknown,
    stepId: string,
    inputRole: string,
  ) => string;
  resolveRoleBaseAbsForBundle: (
    bundleRoot: string,
    roles: Record<string, string>,
    role: string,
  ) => string;
  migrateActiveRuns: (processing: unknown) => Record<string, string>;
  listImageSliceStems: (dir: string) => string[];
};

let cachedPipelineRuns: PipelineRunsModule | null = null;
function pipelineRunsLib(): PipelineRunsModule {
  if (cachedPipelineRuns) {
    return cachedPipelineRuns;
  }
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const mod = (require as (id: string) => unknown)(
    "./js/pipeline_runs",
  ) as PipelineRunsModule;
  cachedPipelineRuns = mod;
  return mod;
}

const PROJECT_FILENAMES = ["project.masonjar", "project.belljar"];

const IMAGE_EXT_RE = /\.(tif|tiff|png|jpe?g)$/i;

export interface ProjectRoles {
  [role: string]: string;
}

export interface ResolvedStepPaths {
  [key: string]: string;
}

export interface ProjectProcessing {
  active_runs?: Record<string, string>;
  active_prediction_run?: string;
  [key: string]: unknown;
}

export interface ProjectJsonShape {
  name?: string;
  roles?: ProjectRoles;
  processing?: ProjectProcessing;
  settings?: Record<string, unknown>;
  [key: string]: unknown;
}

function canonicalRoles(): Record<string, string> {
  return pipelineRunsLib().CANONICAL_ROLES;
}

function findProjectFilename(bundleRoot: string): string {
  if (!bundleRoot || !fs.existsSync(bundleRoot)) {
    return PROJECT_FILENAMES[0];
  }
  const namedMasonjar: string[] = [];
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(bundleRoot, { withFileTypes: true });
  } catch {
    return PROJECT_FILENAMES[0];
  }
  for (const ent of entries) {
    if (!ent.isFile()) {
      continue;
    }
    if (/\.masonjar$/i.test(ent.name)) {
      namedMasonjar.push(ent.name);
    }
  }
  if (namedMasonjar.length === 1) {
    return namedMasonjar[0];
  }
  if (namedMasonjar.length > 1) {
    const folderSlug = path
      .basename(bundleRoot)
      .replace(/_masonjar$/i, "")
      .replace(/\.(masonjar|belljar)$/i, "");
    const expected = `${folderSlug}.masonjar`;
    if (namedMasonjar.includes(expected)) {
      return expected;
    }
    namedMasonjar.sort();
    return namedMasonjar[0];
  }
  for (const name of PROJECT_FILENAMES) {
    if (fs.existsSync(path.join(bundleRoot, name))) {
      return name;
    }
  }
  return PROJECT_FILENAMES[0];
}

export function isBundleRoot(dir: string): boolean {
  if (!dir || !fs.existsSync(dir)) {
    return false;
  }
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return false;
  }
  for (const ent of entries) {
    if (!ent.isFile()) {
      continue;
    }
    if (/\.masonjar$/i.test(ent.name)) {
      return true;
    }
    if (PROJECT_FILENAMES.includes(ent.name)) {
      return true;
    }
  }
  return false;
}

export function loadProjectJson(bundleRoot: string): ProjectJsonShape {
  const filePath = path.join(bundleRoot, findProjectFilename(bundleRoot));
  const raw = fs.readFileSync(filePath, "utf8");
  return JSON.parse(raw) as ProjectJsonShape;
}

export function saveProjectJson(
  bundleRoot: string,
  data: ProjectJsonShape,
): void {
  const filePath = path.join(bundleRoot, findProjectFilename(bundleRoot));
  data.modified = new Date().toISOString();
  if (!data.created) {
    data.created = data.modified as string;
  }
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf8");
}

export function resolveRolePath(
  bundleRoot: string,
  roles: ProjectRoles,
  role: string,
): string {
  return pipelineRunsLib().resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
}

export function resolveActiveRunLeafForBundle(
  bundleRoot: string,
  roles: ProjectRoles,
  processing: ProjectProcessing | undefined,
  role: string,
): string {
  return pipelineRunsLib().resolveActiveRunLeafAbsForBundle(
    bundleRoot,
    roles,
    processing,
    role,
  );
}

export function resolveInputLeafForStep(
  bundleRoot: string,
  stepId: string,
  role: string,
  roles: ProjectRoles,
  processing: ProjectProcessing | undefined,
): string {
  return pipelineRunsLib().resolveInputLeafAbsForStepBundle(
    bundleRoot,
    roles,
    processing,
    stepId,
    role,
  );
}

export function resolvePathsForStep(
  bundleRoot: string,
  stepId: string,
): ResolvedStepPaths {
  let project: ProjectJsonShape;
  try {
    project = loadProjectJson(bundleRoot);
  } catch {
    project = {};
  }
  const roles = project.roles || canonicalRoles();
  return pipelineRunsLib().resolvePathsForBundleStep(
    bundleRoot,
    roles,
    project.processing,
    stepId,
  );
}

export function listImageSliceStems(dir: string): string[] {
  return pipelineRunsLib().listImageSliceStems(dir);
}

export function listImageFiles(dir: string): string[] {
  if (!dir || !fs.existsSync(dir)) {
    return [];
  }
  const out: string[] = [];
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) {
        continue;
      }
      if (
        IMAGE_EXT_RE.test(entry.name) ||
        entry.name.toLowerCase().includes(".ome.")
      ) {
        out.push(path.join(dir, entry.name));
      }
    }
  } catch (_err) {
    return [];
  }
  return out;
}

export function countImageFiles(dir: string): number {
  return listImageFiles(dir).length;
}

const ANNOTATION_RE = /^Annotation_.*\.pkl$/i;

export function listAnnotationPkls(dir: string): string[] {
  if (!dir || !fs.existsSync(dir)) {
    return [];
  }
  const out: string[] = [];
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) {
        continue;
      }
      if (ANNOTATION_RE.test(entry.name) || /\.pkl$/i.test(entry.name)) {
        out.push(entry.name);
      }
    }
  } catch (_err) {
    return [];
  }
  return out;
}

export function countAnnotationPkls(dir: string): number {
  return listAnnotationPkls(dir).length;
}

export function listPredictionPkls(dir: string): string[] {
  if (!dir || !fs.existsSync(dir)) {
    return [];
  }
  const out: string[] = [];
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile() && /^Predictions_.*\.pkl$/i.test(entry.name)) {
        out.push(entry.name);
      }
    }
  } catch (_err) {
    return [];
  }
  return out;
}

export function listBundlesInDirectory(parentDir: string): string[] {
  if (!parentDir || !fs.existsSync(parentDir)) {
    return [];
  }
  const out: string[] = [];
  try {
    const entries = fs.readdirSync(parentDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) {
        continue;
      }
      const full = path.join(parentDir, entry.name);
      if (isBundleRoot(full)) {
        out.push(full);
      }
    }
  } catch (_err) {
    return [];
  }
  return out.sort();
}

export function sliceIdFromFilename(filename: string): string {
  let stem = path.parse(filename).name;
  if (/\.ome$/i.test(stem)) {
    stem = path.parse(stem).name;
  }
  const dot = stem.indexOf(".");
  return dot >= 0 ? stem.slice(0, dot) : stem;
}

export function metaDir(bundleRoot: string): string {
  const masonMeta = path.join(bundleRoot, ".masonjar");
  if (fs.existsSync(masonMeta)) {
    return masonMeta;
  }
  const legacyMeta = path.join(bundleRoot, ".belljar");
  if (fs.existsSync(legacyMeta)) {
    return legacyMeta;
  }
  return masonMeta;
}

export function ensureMetaDir(bundleRoot: string): string {
  const dir = metaDir(bundleRoot);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

export function getStepScriptRoles(
  stepId: string,
): Record<string, string> | null {
  const cfg = pipelineRunsLib().RUN_STEP_CONFIG[stepId];
  if (!cfg || !cfg.scriptRoles) {
    return null;
  }
  return cfg.scriptRoles;
}

export function getStepConfig(
  stepId: string,
): {
  outputRole: string | null;
  branch: string | null;
  scriptRoles: Record<string, string>;
} | null {
  const cfg = pipelineRunsLib().RUN_STEP_CONFIG[stepId];
  if (!cfg) {
    return null;
  }
  return {
    outputRole: cfg.outputRole,
    branch: cfg.branch,
    scriptRoles: cfg.scriptRoles || {},
  };
}
