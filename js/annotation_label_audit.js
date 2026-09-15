"use strict";

var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var branding = require("./branding");
var project = require("./project");

var AUDIT_FILENAME = "annotation_label_audit.json";
var pendingAuditRequest = null;

function auditCachePath(annodir) {
	if (!annodir) return "";
	return path.join(annodir, branding.META_DIR, AUDIT_FILENAME);
}

function readAuditCache(annodir) {
	var p = auditCachePath(annodir);
	if (!p || !fs.existsSync(p)) {
		return null;
	}
	try {
		var raw = fs.readFileSync(p, "utf8");
		var data = JSON.parse(raw);
		return data && typeof data === "object" ? data : null;
	} catch (_e) {
		return null;
	}
}

function newestAnnotationMtime(annodir) {
	if (!annodir || !fs.existsSync(annodir)) {
		return 0;
	}
	var newest = 0;
	try {
		var names = fs.readdirSync(annodir);
		for (var i = 0; i < names.length; i++) {
			var name = names[i];
			if (name.indexOf("Annotation_") !== 0 || !/\.pkl$/i.test(name)) {
				continue;
			}
			var stat = fs.statSync(path.join(annodir, name));
			if (stat.mtimeMs > newest) {
				newest = stat.mtimeMs;
			}
		}
	} catch (_err) {
		return 0;
	}
	return newest;
}

function isAuditStale(annodir) {
	var cache = readAuditCache(annodir);
	if (!cache) {
		return true;
	}
	var cachePath = auditCachePath(annodir);
	var cacheMtime = 0;
	try {
		cacheMtime = fs.statSync(cachePath).mtimeMs;
	} catch (_e) {
		return true;
	}
	return newestAnnotationMtime(annodir) > cacheMtime;
}

function requestAuditRefresh(annodir, structPath) {
	if (!annodir) {
		return Promise.resolve({ ok: false, error: "missing annodir" });
	}
	if (pendingAuditRequest) {
		return pendingAuditRequest;
	}
	pendingAuditRequest = new Promise(function (resolve) {
		function onResult(_event, payload) {
			ipc.removeListener("annotationLabelAuditResult", onResult);
			pendingAuditRequest = null;
			if (payload && payload.ok && payload.audit) {
				resolve(payload);
				return;
			}
			resolve(payload || { ok: false, error: "audit failed" });
		}
		ipc.on("annotationLabelAuditResult", onResult);
		ipc.send("runAnnotationLabelAudit", [annodir, structPath || ""]);
	});
	return pendingAuditRequest;
}

function ensureAudit(annodir, structPath) {
	var cache = readAuditCache(annodir);
	if (cache && !isAuditStale(annodir)) {
		return Promise.resolve({ ok: true, audit: cache });
	}
	return requestAuditRefresh(annodir, structPath).then(function (payload) {
		if (payload && payload.ok && payload.audit) {
			return payload;
		}
		return { ok: false, audit: cache, error: payload && payload.error };
	});
}

function intensityOutputMtime(annodir, bundleRoot) {
	var newest = 0;
	try {
		var pipelineRuns = require("./pipeline_runs");
		var leaf = pipelineRuns.resolveActiveRunLeafAbs("intensity");
		if (leaf && fs.existsSync(leaf)) {
			var manifest = path.join(leaf, "run_manifest.json");
			if (fs.existsSync(manifest)) {
				newest = Math.max(newest, fs.statSync(manifest).mtimeMs);
			}
			var files = fs.readdirSync(leaf);
			for (var i = 0; i < files.length; i++) {
				if (!/\.pkl$/i.test(files[i])) continue;
				newest = Math.max(newest, fs.statSync(path.join(leaf, files[i])).mtimeMs);
			}
		}
	} catch (_e) {
		// ignore
	}
	return newest;
}

function annotationsNewerThanIntensity(annodir, bundleRoot) {
	var annoMtime = newestAnnotationMtime(annodir);
	var intensityMtime = intensityOutputMtime(annodir, bundleRoot);
	if (!annoMtime) return false;
	if (!intensityMtime) return false;
	return annoMtime > intensityMtime;
}

function formatIntensityAuditBanner(audit, parcelSummary, options) {
	options = options || {};
	var parts = [];
	if (options.staleIntensity) {
		parts.push(
			"Annotations changed in Viewer/Editor since your last Isolate Regions run. " +
				"<strong>Re-run Isolate Regions</strong> before using PKL outputs.",
		);
	}
	var summary = audit && audit.summary ? audit.summary : null;
	if (summary && summary.any_issues) {
		var counts = summary.issue_counts || {};
		if (counts.mixed_st_levels) {
			parts.push(
				"Annotation labels mix multiple CCF levels (e.g. areas + layers). " +
					"<strong>Include cortical layers</strong> should match the finest level you painted; " +
					"otherwise ROIs may be incomplete. <strong>Re-run Isolate Regions</strong> after changing annotations.",
			);
		}
		if (counts.layer_on_coarse_parcellation) {
			parts.push(
				"Layer-level labels are present but align parcellation is coarser. Enable " +
					"<strong>Include cortical layers</strong> or run " +
					'<a href="./parcellation_wizard.html">Parcellation (bulk)</a> at cortical layers tier, then re-run Isolate Regions.',
			);
		}
		if (counts.parcellation_metadata_mismatch) {
			parts.push(
				"Painted labels don't match declared parcellation tier. " +
					'<a href="./parcellation_wizard.html">Parcellation wizard</a> or paint at one tier in Viewer/Editor, then re-run Isolate Regions.',
			);
		}
	}
	if (!parts.length) {
		return "";
	}
	var links =
		' <a href="./parcellation_wizard.html">Parcellation</a> · ' +
		'<a href="./adjust.html">Viewer/Editor</a>';
	return parts.join(" ") + links;
}

function auditSuggestsIncludeLayers(audit) {
	var summary = audit && audit.summary ? audit.summary : null;
	if (!summary || !summary.issue_counts) {
		return false;
	}
	return !!(
		summary.issue_counts.mixed_st_levels ||
		summary.issue_counts.layer_on_coarse_parcellation
	);
}

function summarizeAuditIssues(audit) {
	var summary = audit && audit.summary ? audit.summary : null;
	if (!summary || !summary.any_issues) {
		return "";
	}
	var n = summary.slices_with_issues || 0;
	var codes = Object.keys(summary.issue_counts || {});
	if (!codes.length) {
		return n + " slice(s) with label resolution issues";
	}
	return (
		"Mixed label resolutions on " +
		n +
		" slice(s): " +
		codes.join(", ")
	);
}

module.exports = {
	auditCachePath: auditCachePath,
	readAuditCache: readAuditCache,
	isAuditStale: isAuditStale,
	requestAuditRefresh: requestAuditRefresh,
	ensureAudit: ensureAudit,
	newestAnnotationMtime: newestAnnotationMtime,
	annotationsNewerThanIntensity: annotationsNewerThanIntensity,
	formatIntensityAuditBanner: formatIntensityAuditBanner,
	auditSuggestsIncludeLayers: auditSuggestsIncludeLayers,
	summarizeAuditIssues: summarizeAuditIssues,
};
