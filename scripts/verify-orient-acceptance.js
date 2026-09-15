"use strict";

/**
 * Post-fix acceptance checks for Orient / geometry on a bundle (filesystem level).
 * Usage: node scripts/verify-orient-acceptance.js <bundleRoot>
 */

var fs = require("fs");
var path = require("path");

var orientSlicePlan = require("../js/orient_slice_plan");
var geometryState = require("../js/geometry_state");
var cziImport = require("../js/czi_import");
var project = require("../js/project");

function loadCziImport(bundleRoot) {
	var cfgPath = cziImport.importConfigPath(bundleRoot);
	if (fs.existsSync(cfgPath)) {
		try {
			var raw = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
			return raw.czi_import || raw;
		} catch (e) {
			return {};
		}
	}
	if (project.isBundleRoot(bundleRoot)) {
		var proj = project.readProjectJson(bundleRoot);
		return (proj.settings && proj.settings.czi_import) || {};
	}
	return {};
}

function collectIndexDapiIds(bundleRoot) {
	var dapiDir = path.join(bundleRoot, "data/counting/00_dapi");
	if (!fs.existsSync(dapiDir)) {
		return [];
	}
	return fs
		.readdirSync(dapiDir)
		.filter(function (n) {
			return n.toLowerCase().endsWith(".png");
		})
		.map(function (n) {
			return path.basename(n, path.extname(n));
		});
}

function main() {
	var bundleRoot = path.resolve(process.argv[2] || "");
	if (!bundleRoot || !fs.existsSync(bundleRoot)) {
		console.error("Usage: node scripts/verify-orient-acceptance.js <bundleRoot>");
		process.exit(1);
	}
	var czi = loadCziImport(bundleRoot);
	var orderIds = cziImport.collectSliceIds(czi);
	var indexIds = collectIndexDapiIds(bundleRoot);
	var merged = orientSlicePlan.mergeOrientSliceIds(orderIds, indexIds);
	var previewHealth = cziImport.assessOrientPreviewHealth(bundleRoot, czi);
	var geoState = geometryState.assessGeometryApplyState(bundleRoot, czi, {
		sliceIds: merged,
		previewHealth: previewHealth,
	});

	var missingDapi = merged.filter(function (sid) {
		return !fs.existsSync(path.join(bundleRoot, "data/counting/00_dapi", sid + ".png"));
	});
	var missingPreviews = merged.filter(function (sid) {
		var prevDir = path.join(bundleRoot, "data/counting/_previews");
		if (!fs.existsSync(prevDir)) {
			return true;
		}
		return !fs.readdirSync(prevDir).some(function (n) {
			return n.indexOf(sid + "_") === 0 && n.toLowerCase().endsWith(".png");
		});
	});

	var checks = [
		{ name: "slice_plan_count", ok: merged.length >= 1, detail: merged.length + " slices in plan" },
		{
			name: "slice_plan_matches_order",
			ok: !orderIds.length || merged.length >= orderIds.length,
			detail: "order=" + orderIds.length + " merged=" + merged.length,
		},
		{
			name: "geometry_policy_healthy_or_pending",
			ok: geoState.policyState === "healthy" || geoState.pendingCount > 0,
			detail: geoState.policyState + " pending=" + geoState.pendingCount,
		},
		{
			name: "all_slices_have_dapi",
			ok: missingDapi.length === 0,
			detail: missingDapi.length ? "missing: " + missingDapi.slice(0, 5).join(", ") : "ok",
		},
		{
			name: "all_slices_have_preview",
			ok: missingPreviews.length === 0,
			detail: missingPreviews.length ? "missing: " + missingPreviews.slice(0, 5).join(", ") : "ok",
		},
	];

	var failed = checks.filter(function (c) {
		return !c.ok;
	});
	console.log(JSON.stringify({ bundleRoot: bundleRoot, checks: checks, pass: failed.length === 0 }, null, 2));
	process.exit(failed.length ? 1 : 0);
}

main();
