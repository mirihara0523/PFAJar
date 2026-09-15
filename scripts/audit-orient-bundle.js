"use strict";

/**
 * Read-only bundle diagnostic for Orient / geometry workflows.
 * Usage: node scripts/audit-orient-bundle.js <bundleRoot>
 */

var fs = require("fs");
var path = require("path");
var os = require("os");
var { execFileSync } = require("child_process");

var branding = require("../js/branding");
var cziImport = require("../js/czi_import");
var geometryState = require("../js/geometry_state");
var project = require("../js/project");

var DAPI_REL = "data/counting/00_dapi";
var PREVIEWS_REL = cziImport.PREVIEWS_REL || "data/counting/_previews";
var MAX_REL = "data/counting/03_max";

function readJsonIfExists(filePath) {
	if (!fs.existsSync(filePath)) {
		return null;
	}
	try {
		return JSON.parse(fs.readFileSync(filePath, "utf8"));
	} catch (e) {
		return { _parseError: String(e.message || e) };
	}
}

function tailJsonl(filePath, maxLines) {
	if (!fs.existsSync(filePath)) {
		return [];
	}
	var lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/).filter(Boolean);
	return lines.slice(-maxLines).map(function (line) {
		try {
			return JSON.parse(line);
		} catch (e) {
			return { _raw: line };
		}
	});
}

function loadCziImport(bundleRoot) {
	var cfgPath = cziImport.importConfigPath(bundleRoot);
	var raw = readJsonIfExists(cfgPath);
	if (raw && raw.czi_import) {
		return raw.czi_import;
	}
	if (project.isBundleRoot(bundleRoot)) {
		try {
			var proj = project.readProjectJson(bundleRoot);
			if (proj && proj.settings && proj.settings.czi_import) {
				return proj.settings.czi_import;
			}
		} catch (e) {
			/* optional */
		}
	}
	return {};
}

function globSliceTiffs(dir, sliceId, out, relBase) {
	if (!fs.existsSync(dir)) {
		return;
	}
	function walk(abs, rel) {
		var entries = fs.readdirSync(abs, { withFileTypes: true });
		for (var i = 0; i < entries.length; i++) {
			var ent = entries[i];
			var childAbs = path.join(abs, ent.name);
			var childRel = rel ? path.join(rel, ent.name) : ent.name;
			if (ent.isDirectory()) {
				walk(childAbs, childRel);
			} else if (/\.(tif|tiff)$/i.test(ent.name)) {
				var stem = path.basename(ent.name, path.extname(ent.name));
				if (stem === sliceId) {
					out.push(path.join(relBase, childRel).replace(/\\/g, "/"));
				}
			}
		}
	}
	walk(dir, "");
}

function tiffStatsViaPython(absPath) {
	var script =
		"import json,sys\n" +
		"import numpy as np\n" +
		"import tifffile as tiff\n" +
		"p=sys.argv[1]\n" +
		"try:\n" +
		"  a=np.asarray(tiff.imread(p))\n" +
		"  print(json.dumps({'ok':True,'dtype':str(a.dtype),'shape':list(a.shape)," +
		"'min':int(np.min(a)),'max':int(np.max(a)),'mean':float(np.mean(a)),'bytes':__import__('os').path.getsize(p)}))\n" +
		"except Exception as e:\n" +
		"  print(json.dumps({'ok':False,'error':str(e)}))\n";
	var tmp = path.join(os.tmpdir(), "mj-audit-tiff-" + process.pid + ".py");
	fs.writeFileSync(tmp, script, "utf8");
	try {
		var out = execFileSync("python", [tmp, absPath], {
			encoding: "utf8",
			timeout: 30000,
			stdio: ["ignore", "pipe", "pipe"],
		});
		return JSON.parse(out.trim());
	} catch (e) {
		return { ok: false, error: String(e.message || e) };
	} finally {
		try {
			fs.unlinkSync(tmp);
		} catch (ignore) {}
	}
}

function auditSlice(bundleRoot, sliceId, cziImportCfg, activeMaxRel) {
	var dapi = path.join(bundleRoot, DAPI_REL, sliceId + ".png");
	var prevDir = path.join(bundleRoot, PREVIEWS_REL);
	var previews = [];
	if (fs.existsSync(prevDir)) {
		var prefix = sliceId + "_";
		fs.readdirSync(prevDir).forEach(function (name) {
			if (name.indexOf(prefix) === 0 && name.toLowerCase().endsWith(".png")) {
				previews.push(name);
			}
		});
	}
	var zstacks = [];
	var origBase = path.join(bundleRoot, "data/original_scans");
	globSliceTiffs(origBase, sliceId, zstacks, "data/original_scans");
	var maxPaths = [];
	if (activeMaxRel) {
		var activeLeaf = path.join(bundleRoot, MAX_REL, activeMaxRel);
		var candidate = path.join(activeLeaf, sliceId + ".tif");
		if (fs.existsSync(candidate)) {
			maxPaths.push(path.join(MAX_REL, activeMaxRel, sliceId + ".tif").replace(/\\/g, "/"));
		}
	}
	globSliceTiffs(path.join(bundleRoot, MAX_REL), sliceId, maxPaths, MAX_REL);
	var uniqueMax = maxPaths.filter(function (p, i, arr) {
		return arr.indexOf(p) === i;
	});

	var maxStats = null;
	if (uniqueMax.length) {
		maxStats = tiffStatsViaPython(path.join(bundleRoot, uniqueMax[0]));
	}

	return {
		slice_id: sliceId,
		dapi_png: fs.existsSync(dapi),
		preview_count: previews.length,
		previews: previews.sort(),
		zstack_count: zstacks.length,
		zstacks: zstacks.sort(),
		max_paths: uniqueMax,
		max_stats: maxStats,
	};
}

function main() {
	var bundleRoot = process.argv[2];
	if (!bundleRoot) {
		console.error("Usage: node scripts/audit-orient-bundle.js <bundleRoot>");
		process.exit(1);
	}
	bundleRoot = path.resolve(bundleRoot);
	if (!fs.existsSync(bundleRoot)) {
		console.error("Bundle not found:", bundleRoot);
		process.exit(1);
	}

	var cziImportCfg = loadCziImport(bundleRoot);
	var sliceIds = geometryState.resolveSliceIds(bundleRoot, cziImportCfg);
	var metaDir = path.join(bundleRoot, branding.META_DIR);

	var activeMaxRel = null;
	try {
		if (project.isBundleRoot(bundleRoot)) {
			var proj = project.readProjectJson(bundleRoot);
			if (proj && proj.processing && proj.processing.active_runs) {
				activeMaxRel = proj.processing.active_runs.max || null;
			}
		}
	} catch (e) {
		/* optional */
	}

	var previewHealth = cziImport.assessOrientPreviewHealth(bundleRoot, cziImportCfg);
	var geoState = geometryState.assessGeometryApplyState(bundleRoot, cziImportCfg, {
		sliceIds: sliceIds,
		previewHealth: previewHealth,
	});

	var report = {
		bundleRoot: bundleRoot,
		generated_at: new Date().toISOString(),
		slice_count: sliceIds.length,
		slice_ids: sliceIds,
		active_max_run: activeMaxRel,
		geometry_apply_state: {
			policyState: geoState.policyState,
			signals: geoState.signals,
			allowApply: geoState.allowApply,
			pendingCount: geoState.pendingCount,
		},
		preview_health: {
			needsRepair: previewHealth.needsRepair,
			canApply: previewHealth.canApply,
			blank_dapi_count: (previewHealth.blankDapi || []).length,
		},
		meta: {
			progress: readJsonIfExists(path.join(metaDir, geometryState.META_PROGRESS)),
			last_result: readJsonIfExists(path.join(metaDir, geometryState.META_LAST_RESULT)),
			repair_queue: readJsonIfExists(path.join(metaDir, geometryState.META_REPAIR_QUEUE)),
			history_tail: tailJsonl(path.join(metaDir, "geometry_history.jsonl"), 20),
		},
		slices: sliceIds.map(function (sid) {
			return auditSlice(bundleRoot, sid, cziImportCfg, activeMaxRel);
		}),
	};

	var lowMax = report.slices.filter(function (sl) {
		return sl.max_stats && sl.max_stats.ok && sl.max_stats.max < 10;
	});
	report.warnings = [];
	if (lowMax.length) {
		report.warnings.push(
			lowMax.length + " slice(s) with max TIFF peak below 10: " +
				lowMax.map(function (s) { return s.slice_id; }).join(", "),
		);
	}
	var missingDapi = report.slices.filter(function (sl) { return !sl.dapi_png; });
	if (missingDapi.length) {
		report.warnings.push(missingDapi.length + " slice(s) missing 00_dapi PNG");
	}

	var outPath = path.join(metaDir, "orient_audit_report.json");
	fs.mkdirSync(metaDir, { recursive: true });
	fs.writeFileSync(outPath, JSON.stringify(report, null, 2), "utf8");
	console.log(JSON.stringify(report, null, 2));
	console.error("Wrote", outPath);
}

main();
