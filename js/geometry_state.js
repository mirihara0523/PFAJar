"use strict";

var crypto = require("crypto");
var fs = require("fs");
var path = require("path");

var branding = require("./branding");
var cziImport = require("./czi_import");
var orientGeometry = require("./orient_geometry");

var META_PROGRESS = "geometry_apply_progress.json";
var META_LAST_RESULT = "geometry_apply_last_result.json";
var META_REPAIR_QUEUE = "geometry_repair_queue.json";

var PREVIEWS_REL = cziImport.PREVIEWS_REL || "data/counting/_previews";
var DAPI_REL = "data/counting/00_dapi";

function metaFilePath(bundleRoot, name) {
	return path.join(bundleRoot, branding.META_DIR, name);
}

function readMetaJson(bundleRoot, name) {
	var p = metaFilePath(bundleRoot, name);
	if (!fs.existsSync(p)) {
		return null;
	}
	try {
		return JSON.parse(fs.readFileSync(p, "utf8"));
	} catch (e) {
		return null;
	}
}

function writeMetaJson(bundleRoot, name, payload) {
	var p = metaFilePath(bundleRoot, name);
	fs.mkdirSync(path.dirname(p), { recursive: true });
	fs.writeFileSync(p, JSON.stringify(payload, null, 2), "utf8");
	return p;
}

/** Remove stale apply progress after CZI re-extract reconcile. */
function clearGeometryApplyMeta(bundleRoot) {
	for (var i = 0; i < 2; i++) {
		var name = i === 0 ? META_PROGRESS : META_LAST_RESULT;
		var p = metaFilePath(bundleRoot, name);
		try {
			if (fs.existsSync(p)) {
				fs.unlinkSync(p);
			}
		} catch (_err) {
			/* best-effort */
		}
	}
}

function geometryOnlyHash(cziImport) {
	var geom = (cziImport && cziImport.geometry) || {};
	return crypto.createHash("sha256").update(JSON.stringify(geom)).digest("hex");
}

function configFingerprint(importCfg) {
	if (!importCfg) {
		return "";
	}
	if (typeof importCfg.config_fingerprint === "string") {
		return importCfg.config_fingerprint;
	}
	return cziImport.cziImportFingerprint
		? cziImport.cziImportFingerprint(importCfg)
		: "";
}

function hasPendingGeometry(cziImport, sliceIds) {
	var geom = (cziImport && cziImport.geometry) || {};
	for (var i = 0; i < sliceIds.length; i++) {
		if (orientGeometry.geometryHasPending(geom[sliceIds[i]])) {
			return true;
		}
	}
	return false;
}

function countPendingGeometry(cziImport, sliceIds) {
	return orientGeometry.countNonIdentityGeometry(
		(cziImport && cziImport.geometry) || {},
		sliceIds,
	);
}

function defaultReferenceBranch(cziImport) {
	cziImport = cziImport || {};
	var role = String(cziImport.primary_signal_role || "signal_somata");
	if (role.indexOf("other:") === 0) {
		return role.slice(6);
	}
	if (role === "signal_somata") {
		return "somata";
	}
	if (role === "signal_nuclei") {
		return "nuclei";
	}
	if (role === "signal_axons") {
		return "axons";
	}
	return "somata";
}

function resolveSliceIds(bundleRoot, importCfg) {
	var ids = cziImport.collectSliceIds(importCfg || {});
	if (ids.length) {
		return ids;
	}
	var seen = {};
	var out = [];
	if (bundleRoot) {
		var dapiDir = path.join(bundleRoot, DAPI_REL);
		if (fs.existsSync(dapiDir)) {
			var dapiFiles = fs.readdirSync(dapiDir);
			for (var d = 0; d < dapiFiles.length; d++) {
				if (!dapiFiles[d].toLowerCase().endsWith(".png")) {
					continue;
				}
				var stem = path.basename(dapiFiles[d], path.extname(dapiFiles[d]));
				if (stem && !seen[stem]) {
					seen[stem] = true;
					out.push(stem);
				}
			}
		}
		var prevDir = path.join(bundleRoot, PREVIEWS_REL);
		if (fs.existsSync(prevDir)) {
			var prevFiles = fs.readdirSync(prevDir);
			for (var p = 0; p < prevFiles.length; p++) {
				var name = prevFiles[p];
				if (!name.toLowerCase().endsWith(".png")) {
					continue;
				}
				var idx = name.indexOf("_");
				if (idx < 0) {
					continue;
				}
				var sid = name.slice(0, idx);
				if (sid && !seen[sid]) {
					seen[sid] = true;
					out.push(sid);
				}
			}
		}
	}
	// Natural sort (s2 before s10), matching CZI import / Orient ordering.
	out.sort(function (a, b) {
		return cziImport.naturalCompare({ sliceId: a }, { sliceId: b });
	});
	return out;
}

function previewPathsForSlice(bundleRoot, sliceId) {
	var prevDir = path.join(bundleRoot, PREVIEWS_REL);
	var paths = [];
	if (!fs.existsSync(prevDir)) {
		return paths;
	}
	var prefix = sliceId + "_";
	var entries = fs.readdirSync(prevDir);
	for (var i = 0; i < entries.length; i++) {
		var name = entries[i];
		if (name.indexOf(prefix) !== 0 || !name.toLowerCase().endsWith(".png")) {
			continue;
		}
		var branch = name.slice(prefix.length, -4);
		paths.push({
			branch: branch,
			rel_path: path.join(PREVIEWS_REL, name),
			abs_path: path.join(prevDir, name),
		});
	}
	return paths;
}

function progressMatches(cziImport, progress) {
	if (!progress) {
		return false;
	}
	var fp = configFingerprint(cziImport);
	var gh = geometryOnlyHash(cziImport);
	return progress.config_fingerprint === fp && progress.geometry_hash === gh;
}

function hasReextractGeometryScope(cziImport) {
	var scope = cziImport && cziImport.reextract_geometry_scope;
	if (!scope || typeof scope !== "object") {
		return false;
	}
	return Object.keys(scope).length > 0;
}

function countPendingInReextractScope(cziImport, scope) {
	var geom = (cziImport && cziImport.geometry) || {};
	var count = 0;
	var keys = Object.keys(scope || {});
	for (var i = 0; i < keys.length; i++) {
		if (orientGeometry.geometryHasPending(geom[keys[i]])) {
			count++;
		}
	}
	return count;
}

function reextractScopeIncludesDapi(scope) {
	var keys = Object.keys(scope || {});
	for (var i = 0; i < keys.length; i++) {
		var roles = scope[keys[i]] || [];
		for (var r = 0; r < roles.length; r++) {
			if (roles[r] === cziImport.ROLE_DAPI) {
				return true;
			}
		}
	}
	return false;
}

function assessGeometryApplyState(bundleRoot, cziImport, options) {
	options = options || {};
	cziImport = cziImport || {};
	var sliceIds = options.sliceIds || resolveSliceIds(bundleRoot, cziImport);
	var previewHealth = options.previewHealth || null;
	var pendingCount = countPendingGeometry(cziImport, sliceIds);
	var reextractScope = cziImport.reextract_geometry_scope;
	var partialReextractApply = hasReextractGeometryScope(cziImport);
	var pendingInScope = partialReextractApply
		? countPendingInReextractScope(cziImport, reextractScope)
		: 0;
	var signals = [];
	var policyState = "healthy";
	var geomHash = geometryOnlyHash(cziImport);
	var cfgFp = configFingerprint(cziImport);

	var progress = readMetaJson(bundleRoot, META_PROGRESS);
	var lastResult = readMetaJson(bundleRoot, META_LAST_RESULT);
	var progressOk = progressMatches(cziImport, progress);

	if (progressOk && progress.completed != null && progress.files_total != null) {
		if (progress.completed < progress.files_total) {
			signals.push("manifest_incomplete");
			policyState = "interrupted";
		} else if (
			pendingCount > 0 &&
			!cziImport.geometry_applied_at &&
			!partialReextractApply
		) {
			signals.push("finalize_pending");
			policyState = "finalize_pending";
		}
	}

	if (lastResult && progressOk && !partialReextractApply) {
		if (lastResult.ok === false) {
			signals.push("last_result_failed");
			policyState = "interrupted";
		} else if (
			lastResult.changed != null &&
			lastResult.files_total != null &&
			lastResult.changed < lastResult.files_total
		) {
			signals.push("last_result_partial");
			policyState = "interrupted";
		}
	}

	// Re-applying when a completed apply already baked the files would stack
	// transforms on already-transformed images. This is the only "pending after
	// apply" danger we can detect reliably (geometry_applied_at is an explicit
	// flag set by finalize, not a guess). The previous mtime-spread and
	// config-pending heuristics (legacy_partial_suspect / mtime_split /
	// partial_pending_subset) false-positived on every fresh multi-channel import
	// — they could not distinguish normal extraction timing or in-progress editing
	// from a genuine interrupted apply — so they were removed. Interrupted/failed
	// applies are caught by the explicit progress / last-result meta files above.
	if (cziImport.geometry_applied_at && pendingCount > 0) {
		signals.push("reapply_stack_risk");
		policyState = "interrupted";
	}

	var previewBlocked = previewHealth && previewHealth.needsRepair;
	var allowApply = true;
	var allowPreviewRepair = true;
	var showRebuildWizard = false;
	var canFinalizeOnly = policyState === "finalize_pending";
	var blockReason = "";

	if (previewBlocked) {
		allowApply = false;
		blockReason = "preview_health";
	}

	if (policyState === "interrupted") {
		allowApply = false;
		showRebuildWizard = true;
		if (!blockReason) {
			blockReason = "interrupted_geometry";
		}
	}

	if (policyState === "finalize_pending") {
		allowApply = false;
		showRebuildWizard = true;
	}

	if (pendingCount === 0 && policyState === "healthy") {
		allowApply = false;
	}

	if (policyState === "interrupted" && pendingCount > 0) {
		allowPreviewRepair = false;
	}

	if (partialReextractApply && pendingInScope > 0 && !previewBlocked) {
		allowApply = true;
		showRebuildWizard = false;
		canFinalizeOnly = false;
		policyState = "reextract_partial";
		if (signals.indexOf("reextract_partial_apply") < 0) {
			signals.push("reextract_partial_apply");
		}
		blockReason = "";
	}

	return {
		policyState: policyState,
		signals: signals,
		sliceIds: sliceIds,
		pendingCount: pendingCount,
		pendingInScope: pendingInScope,
		partialReextractApply: partialReextractApply,
		reextractScopeIncludesDapi: partialReextractApply
			? reextractScopeIncludesDapi(reextractScope)
			: false,
		allowApply: allowApply && !previewBlocked && (partialReextractApply ? pendingInScope > 0 : pendingCount > 0),
		allowPreviewRepair: allowPreviewRepair,
		showRebuildWizard: showRebuildWizard,
		canFinalizeOnly: canFinalizeOnly,
		canResume: !!(progressOk && progress && progress.completed < progress.files_total),
		blockReason: blockReason,
		previewBlocked: !!previewBlocked,
		progress: progress,
		lastResult: lastResult,
		geometryHash: geomHash,
		configFingerprint: cfgFp,
		referenceBranch: defaultReferenceBranch(cziImport),
	};
}

function geometryStateBannerText(state, previewHealth) {
	if (!state) {
		return "";
	}
	if (state.previewBlocked && previewHealth) {
		return cziImport.orientPreviewBannerText(previewHealth);
	}
	if (state.policyState === "interrupted") {
		if (state.signals && state.signals.indexOf("reapply_stack_risk") >= 0) {
			return (
				"Files were already modified; pending geometry on " +
				state.pendingCount +
				" of " +
				state.sliceIds.length +
				" slices. Use Check Orientation Consistency — do not Apply."
			);
		}
		return (
			"Geometry apply was interrupted or files are inconsistent. " +
			"Do not use Apply geometry — use Check Orientation Consistency to audit and repair."
		);
	}
	if (state.policyState === "finalize_pending") {
		return (
			"Geometry files appear fully written but project settings were not finalized. " +
			"Use Finalize only or Check Orientation Consistency."
		);
	}
	if (state.policyState === "reextract_partial") {
		var dapiNote = state.reextractScopeIncludesDapi
			? ""
			: " DAPI and other skipped channels are left unchanged.";
		return (
			"Re-imported channel(s) need saved orientation applied to disk only." +
			dapiNote +
			" Click Confirm geometry."
		);
	}
	return "";
}

function persistLastApplyResult(bundleRoot, cziImport, payload) {
	var entry = Object.assign({}, payload || {}, {
		at: new Date().toISOString(),
		config_fingerprint: configFingerprint(cziImport),
		geometry_hash: geometryOnlyHash(cziImport),
	});
	writeMetaJson(bundleRoot, META_LAST_RESULT, entry);
	return entry;
}

function repairQueuePath(bundleRoot) {
	return metaFilePath(bundleRoot, META_REPAIR_QUEUE);
}

function readRepairQueue(bundleRoot) {
	return readMetaJson(bundleRoot, META_REPAIR_QUEUE);
}

function writeRepairQueue(bundleRoot, queue) {
	return writeMetaJson(bundleRoot, META_REPAIR_QUEUE, queue);
}

function mergeProbeIntoQueue(bundleRoot, probeResult, cziImport) {
	var existing = readRepairQueue(bundleRoot) || {};
	var existingById = {};
	var existingSlices = existing.slices || [];
	for (var e = 0; e < existingSlices.length; e++) {
		if (existingSlices[e].slice_id) {
			existingById[existingSlices[e].slice_id] = existingSlices[e];
		}
	}
	var slices = ((probeResult && probeResult.slices) || []).slice();
	for (var i = 0; i < slices.length; i++) {
		var prev = existingById[slices[i].slice_id];
		if (prev && prev.confirmed_ops && prev.confirmed_ops.length) {
			slices[i].confirmed_ops = prev.confirmed_ops.slice();
			slices[i].needs_manual_review = false;
		}
	}
	var queue = {
		slices: slices,
		reference_branch:
			(probeResult && probeResult.reference_branch) ||
			existing.reference_branch ||
			defaultReferenceBranch(cziImport),
		per_branch_reference_slice:
			(probeResult && probeResult.per_branch_reference_slice) ||
			existing.per_branch_reference_slice ||
			{},
		scanned_at: new Date().toISOString(),
		summary: (probeResult && probeResult.summary) || {},
		config_fingerprint: configFingerprint(cziImport),
		geometry_hash: geometryOnlyHash(cziImport),
	};
	writeRepairQueue(bundleRoot, queue);
	return queue;
}

function summarizeQueue(queue) {
	queue = queue || {};
	var slices = queue.slices || [];
	var ok = 0;
	var review = 0;
	var autoRepair = 0;
	for (var i = 0; i < slices.length; i++) {
		var sl = slices[i];
		if (sl.needs_manual_review) {
			review += 1;
		} else if (sl.issue === "ok") {
			ok += 1;
		} else if (sl.auto_repairable) {
			autoRepair += 1;
		}
	}
	return {
		total: slices.length,
		ok: ok,
		needReview: review,
		autoRepairable: autoRepair,
	};
}

function slicesNeedingReview(queue) {
	return (queue.slices || []).filter(function (sl) {
		return sl.needs_manual_review;
	});
}

function slicesAwaitingReviewConfirmation(queue) {
	return (queue.slices || []).filter(function (sl) {
		return sl.needs_manual_review && sl.confirmed_ops == null;
	});
}

function sliceReviewIsSettled(sl) {
	return sl.confirmed_ops != null;
}

function sliceHasConfirmedOps(sl) {
	var ops = sl && sl.confirmed_ops;
	return !!(ops && ops.length && !orientGeometry.isIdentityGeometry({ ops: ops }));
}

function resolveRepairStrategy(ch, sl) {
	var strategy =
		ch.confirmed_strategy || ch.suggested_strategy || "derivatives_from_original";
	if (strategy === "skip" && sliceHasConfirmedOps(sl)) {
		return "derivatives_from_original";
	}
	return strategy;
}

function buildConfirmedGeometryFromQueue(queue) {
	var geometry = {};
	var slices = queue.slices || [];
	for (var i = 0; i < slices.length; i++) {
		var sl = slices[i];
		if (!sliceHasConfirmedOps(sl)) {
			continue;
		}
		geometry[sl.slice_id] = { ops: sl.confirmed_ops.slice() };
	}
	return geometry;
}

/** Full pipeline geometry map for repair: manual confirms + probe suggested_ops. */
function buildRepairGeometryFromQueue(queue) {
	var geometry = {};
	var slices = queue.slices || [];
	for (var i = 0; i < slices.length; i++) {
		var sl = slices[i];
		if (sliceReviewIsSettled(sl) && !sliceHasConfirmedOps(sl)) {
			continue;
		}
		if (sliceHasConfirmedOps(sl)) {
			geometry[sl.slice_id] = { ops: sl.confirmed_ops.slice() };
			continue;
		}
		if (sl.auto_repairable && sl.suggested_ops && sl.suggested_ops.length) {
			geometry[sl.slice_id] = { ops: sl.suggested_ops.slice() };
		}
	}
	return geometry;
}

function suggestedOpsForReviewSlice(sl) {
	if (!sl) {
		return [];
	}
	if (sl.suggested_ops && sl.suggested_ops.length) {
		return sl.suggested_ops.slice();
	}
	if (sl.pending_ops && sl.pending_ops.length) {
		return sl.pending_ops.slice();
	}
	return [];
}

function buildAutoRepairTargetsFromQueue(queue) {
	var targets = [];
	var slices = queue.slices || [];
	for (var i = 0; i < slices.length; i++) {
		var sl = slices[i];
		if (sliceReviewIsSettled(sl) || sliceHasConfirmedOps(sl)) {
			continue;
		}
		var ops = sl.pending_ops || [];
		if (!ops.length && sl.issue === "ok") {
			continue;
		}
		var channels = sl.channels || [];
		for (var c = 0; c < channels.length; c++) {
			var ch = channels[c];
			if (ch.suggested_strategy === "skip") {
				continue;
			}
			targets.push({
				slice_id: sl.slice_id,
				branch: ch.branch,
				rel_path: ch.rel_path,
				strategy:
					ch.confirmed_strategy || ch.suggested_strategy || "derivatives_from_original",
				ops: ops,
			});
		}
	}
	return targets;
}

function buildRepairTargetsFromQueue(queue) {
	var targets = [];
	var slices = queue.slices || [];
	for (var i = 0; i < slices.length; i++) {
		var sl = slices[i];
		if (sliceReviewIsSettled(sl) && !sliceHasConfirmedOps(sl)) {
			continue;
		}
		var ops = sl.confirmed_ops != null ? sl.confirmed_ops : sl.pending_ops || [];
		if (!ops.length && sl.issue === "ok") {
			continue;
		}
		var channels = sl.channels || [];
		for (var c = 0; c < channels.length; c++) {
			var ch = channels[c];
			if (ch.suggested_strategy === "skip" && sl.confirmed_ops == null) {
				continue;
			}
			targets.push({
				slice_id: sl.slice_id,
				branch: ch.branch,
				rel_path: ch.rel_path,
				strategy: resolveRepairStrategy(ch, sl),
				ops: ops.slice(),
			});
		}
	}
	return targets;
}

function writeCziImportConfig(bundleRoot, importCfg, extra, opts) {
	opts = opts || {};
	var meta = path.join(bundleRoot, branding.META_DIR);
	fs.mkdirSync(meta, { recursive: true });
	var cfgPath = cziImport.importConfigPath(bundleRoot);
	var payload = Object.assign({}, importCfg || {}, extra || {});
	var omitKeys = opts.omitKeys || [];
	for (var k = 0; k < omitKeys.length; k++) {
		delete payload[omitKeys[k]];
	}
	payload.config_fingerprint = configFingerprint(payload) || cziImport.cziImportFingerprint(payload);
	payload.geometry_hash = geometryOnlyHash(payload);
	fs.writeFileSync(cfgPath, JSON.stringify({ czi_import: payload }, null, 2), "utf8");
	return cfgPath;
}

function finalizeGeometryAfterApply(bundleRoot, importCfg, options) {
	options = options || {};
	var ids = options.sliceIds || resolveSliceIds(bundleRoot, importCfg);
	if (!importCfg.geometry) {
		importCfg.geometry = {};
	}
	orientGeometry.resetGeometryMap(importCfg.geometry, ids);
	importCfg.geometry_applied_at = new Date().toISOString();
	var payload = options.payload || {};
	if (payload.files_total != null) {
		importCfg.geometry_applied_files_total = payload.files_total;
	} else if (options.files_total != null) {
		importCfg.geometry_applied_files_total = options.files_total;
	}
	delete importCfg.reextract_geometry_scope;
	var configExtra = Object.assign({}, options.configExtra || {});
	if (options.applySource) {
		configExtra.apply_source = options.applySource;
	}
	var omitKeys = options.omitConfigKeys;
	if (omitKeys && omitKeys.length) {
		writeCziImportConfig(bundleRoot, importCfg, configExtra, { omitKeys: omitKeys });
	} else {
		writeCziImportConfig(bundleRoot, importCfg, configExtra);
	}
	return importCfg;
}

function reconcileGeometryOnOpen(bundleRoot, projectData) {
	var result = { changed: false, workspaceBanner: null };
	if (!projectData || !projectData.settings || !projectData.settings.czi_import) {
		return result;
	}
	var cziImport = projectData.settings.czi_import;
	var sliceIds = resolveSliceIds(bundleRoot, cziImport);
	var last = readMetaJson(bundleRoot, META_LAST_RESULT);
	var pending = hasPendingGeometry(cziImport, sliceIds);

	if (pending && last && last.ok === true) {
		var hash = geometryOnlyHash(cziImport);
		if (!last.geometry_hash || last.geometry_hash === hash) {
			if (!cziImport.geometry) {
				cziImport.geometry = {};
			}
			orientGeometry.resetGeometryMap(cziImport.geometry, sliceIds);
			writeCziImportConfig(bundleRoot, cziImport);
			result.changed = true;
			console.log(
				"[geometry_state] reconcileGeometryOnOpen: cleared stale pending geometry after successful apply meta",
			);
			pending = false;
		}
	}

	var geoState = assessGeometryApplyState(bundleRoot, cziImport, { sliceIds: sliceIds });
	if (geoState.policyState === "interrupted" || geoState.policyState === "finalize_pending") {
		result.workspaceBanner = {
			policyState: geoState.policyState,
			message: geometryStateBannerText(geoState),
		};
	}
	return result;
}

function shouldShowGeometryWorkspaceBanner(workspaceBanner) {
	return !!(workspaceBanner && workspaceBanner.message);
}

function batchGeometryPreflight(projectPath, cziImport) {
	var bundleRoot = projectPath;
	var state = assessGeometryApplyState(bundleRoot, cziImport || {});
	if (state.policyState === "interrupted" || state.policyState === "finalize_pending") {
		return {
			tone: "red",
			label: "blocked",
			reason: "Interrupted geometry — run Check Orientation Consistency first.",
		};
	}
	if (!hasPendingGeometry(cziImport || {}, state.sliceIds)) {
		return {
			tone: "amber",
			label: "no-op",
			reason: "No pending geometry — will skip.",
		};
	}
	return {
		tone: "green",
		label: "ready",
		reason: state.pendingCount + " slice(s) with pending geometry",
	};
}

module.exports = {
	META_PROGRESS: META_PROGRESS,
	META_LAST_RESULT: META_LAST_RESULT,
	META_REPAIR_QUEUE: META_REPAIR_QUEUE,
	metaFilePath: metaFilePath,
	readMetaJson: readMetaJson,
	writeMetaJson: writeMetaJson,
	geometryOnlyHash: geometryOnlyHash,
	configFingerprint: configFingerprint,
	hasPendingGeometry: hasPendingGeometry,
	countPendingGeometry: countPendingGeometry,
	defaultReferenceBranch: defaultReferenceBranch,
	resolveSliceIds: resolveSliceIds,
	previewPathsForSlice: previewPathsForSlice,
	assessGeometryApplyState: assessGeometryApplyState,
	clearGeometryApplyMeta: clearGeometryApplyMeta,
	hasReextractGeometryScope: hasReextractGeometryScope,
	geometryStateBannerText: geometryStateBannerText,
	persistLastApplyResult: persistLastApplyResult,
	repairQueuePath: repairQueuePath,
	readRepairQueue: readRepairQueue,
	writeRepairQueue: writeRepairQueue,
	mergeProbeIntoQueue: mergeProbeIntoQueue,
	summarizeQueue: summarizeQueue,
	slicesNeedingReview: slicesNeedingReview,
	slicesAwaitingReviewConfirmation: slicesAwaitingReviewConfirmation,
	sliceReviewIsSettled: sliceReviewIsSettled,
	sliceHasConfirmedOps: sliceHasConfirmedOps,
	buildConfirmedGeometryFromQueue: buildConfirmedGeometryFromQueue,
	buildRepairGeometryFromQueue: buildRepairGeometryFromQueue,
	suggestedOpsForReviewSlice: suggestedOpsForReviewSlice,
	buildAutoRepairTargetsFromQueue: buildAutoRepairTargetsFromQueue,
	buildRepairTargetsFromQueue: buildRepairTargetsFromQueue,
	batchGeometryPreflight: batchGeometryPreflight,
	writeCziImportConfig: writeCziImportConfig,
	finalizeGeometryAfterApply: finalizeGeometryAfterApply,
	reconcileGeometryOnOpen: reconcileGeometryOnOpen,
	shouldShowGeometryWorkspaceBanner: shouldShowGeometryWorkspaceBanner,
};
