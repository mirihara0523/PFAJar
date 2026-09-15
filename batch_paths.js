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
Object.defineProperty(exports, "__esModule", { value: true });
exports.getStepConfig = exports.getStepScriptRoles = exports.ensureMetaDir = exports.metaDir = exports.sliceIdFromFilename = exports.listBundlesInDirectory = exports.listPredictionPkls = exports.countAnnotationPkls = exports.listAnnotationPkls = exports.countImageFiles = exports.listImageFiles = exports.listImageSliceStems = exports.resolvePathsForStep = exports.resolveInputLeafForStep = exports.resolveActiveRunLeafForBundle = exports.resolveRolePath = exports.saveProjectJson = exports.loadProjectJson = exports.isBundleRoot = void 0;
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
let cachedPipelineRuns = null;
function pipelineRunsLib() {
    if (cachedPipelineRuns) {
        return cachedPipelineRuns;
    }
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = require("./js/pipeline_runs");
    cachedPipelineRuns = mod;
    return mod;
}
const PROJECT_FILENAMES = ["project.masonjar", "project.belljar"];
const IMAGE_EXT_RE = /\.(tif|tiff|png|jpe?g)$/i;
function canonicalRoles() {
    return pipelineRunsLib().CANONICAL_ROLES;
}
function findProjectFilename(bundleRoot) {
    if (!bundleRoot || !fs.existsSync(bundleRoot)) {
        return PROJECT_FILENAMES[0];
    }
    const namedMasonjar = [];
    let entries;
    try {
        entries = fs.readdirSync(bundleRoot, { withFileTypes: true });
    }
    catch (_a) {
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
function isBundleRoot(dir) {
    if (!dir || !fs.existsSync(dir)) {
        return false;
    }
    let entries;
    try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
    }
    catch (_a) {
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
exports.isBundleRoot = isBundleRoot;
function loadProjectJson(bundleRoot) {
    const filePath = path.join(bundleRoot, findProjectFilename(bundleRoot));
    const raw = fs.readFileSync(filePath, "utf8");
    return JSON.parse(raw);
}
exports.loadProjectJson = loadProjectJson;
function saveProjectJson(bundleRoot, data) {
    const filePath = path.join(bundleRoot, findProjectFilename(bundleRoot));
    data.modified = new Date().toISOString();
    if (!data.created) {
        data.created = data.modified;
    }
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf8");
}
exports.saveProjectJson = saveProjectJson;
function resolveRolePath(bundleRoot, roles, role) {
    return pipelineRunsLib().resolveRoleBaseAbsForBundle(bundleRoot, roles, role);
}
exports.resolveRolePath = resolveRolePath;
function resolveActiveRunLeafForBundle(bundleRoot, roles, processing, role) {
    return pipelineRunsLib().resolveActiveRunLeafAbsForBundle(bundleRoot, roles, processing, role);
}
exports.resolveActiveRunLeafForBundle = resolveActiveRunLeafForBundle;
function resolveInputLeafForStep(bundleRoot, stepId, role, roles, processing) {
    return pipelineRunsLib().resolveInputLeafAbsForStepBundle(bundleRoot, roles, processing, stepId, role);
}
exports.resolveInputLeafForStep = resolveInputLeafForStep;
function resolvePathsForStep(bundleRoot, stepId) {
    let project;
    try {
        project = loadProjectJson(bundleRoot);
    }
    catch (_a) {
        project = {};
    }
    const roles = project.roles || canonicalRoles();
    return pipelineRunsLib().resolvePathsForBundleStep(bundleRoot, roles, project.processing, stepId);
}
exports.resolvePathsForStep = resolvePathsForStep;
function listImageSliceStems(dir) {
    return pipelineRunsLib().listImageSliceStems(dir);
}
exports.listImageSliceStems = listImageSliceStems;
function listImageFiles(dir) {
    if (!dir || !fs.existsSync(dir)) {
        return [];
    }
    const out = [];
    try {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
            if (!entry.isFile()) {
                continue;
            }
            if (IMAGE_EXT_RE.test(entry.name) ||
                entry.name.toLowerCase().includes(".ome.")) {
                out.push(path.join(dir, entry.name));
            }
        }
    }
    catch (_err) {
        return [];
    }
    return out;
}
exports.listImageFiles = listImageFiles;
function countImageFiles(dir) {
    return listImageFiles(dir).length;
}
exports.countImageFiles = countImageFiles;
const ANNOTATION_RE = /^Annotation_.*\.pkl$/i;
function listAnnotationPkls(dir) {
    if (!dir || !fs.existsSync(dir)) {
        return [];
    }
    const out = [];
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
    }
    catch (_err) {
        return [];
    }
    return out;
}
exports.listAnnotationPkls = listAnnotationPkls;
function countAnnotationPkls(dir) {
    return listAnnotationPkls(dir).length;
}
exports.countAnnotationPkls = countAnnotationPkls;
function listPredictionPkls(dir) {
    if (!dir || !fs.existsSync(dir)) {
        return [];
    }
    const out = [];
    try {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
            if (entry.isFile() && /^Predictions_.*\.pkl$/i.test(entry.name)) {
                out.push(entry.name);
            }
        }
    }
    catch (_err) {
        return [];
    }
    return out;
}
exports.listPredictionPkls = listPredictionPkls;
function listBundlesInDirectory(parentDir) {
    if (!parentDir || !fs.existsSync(parentDir)) {
        return [];
    }
    const out = [];
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
    }
    catch (_err) {
        return [];
    }
    return out.sort();
}
exports.listBundlesInDirectory = listBundlesInDirectory;
function sliceIdFromFilename(filename) {
    let stem = path.parse(filename).name;
    if (/\.ome$/i.test(stem)) {
        stem = path.parse(stem).name;
    }
    const dot = stem.indexOf(".");
    return dot >= 0 ? stem.slice(0, dot) : stem;
}
exports.sliceIdFromFilename = sliceIdFromFilename;
function metaDir(bundleRoot) {
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
exports.metaDir = metaDir;
function ensureMetaDir(bundleRoot) {
    const dir = metaDir(bundleRoot);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
}
exports.ensureMetaDir = ensureMetaDir;
function getStepScriptRoles(stepId) {
    const cfg = pipelineRunsLib().RUN_STEP_CONFIG[stepId];
    if (!cfg || !cfg.scriptRoles) {
        return null;
    }
    return cfg.scriptRoles;
}
exports.getStepScriptRoles = getStepScriptRoles;
function getStepConfig(stepId) {
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
exports.getStepConfig = getStepConfig;
