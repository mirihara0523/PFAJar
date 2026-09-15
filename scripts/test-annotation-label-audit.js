"use strict";

var assert = require("assert");
var fs = require("fs");
var path = require("path");
var helpers = require("./test-helpers");
var branding = require("../js/branding");
var labelAudit = require("../js/annotation_label_audit");

function testStaleWhenNoCache(tmp) {
	var leaf = path.join(tmp, "align");
	fs.mkdirSync(leaf, { recursive: true });
	fs.writeFileSync(path.join(leaf, "Annotation_M528_s061.pkl"), "x", "utf8");
	assert.strictEqual(labelAudit.isAuditStale(leaf), true);
}

function testReadCache(tmp) {
	var leaf = path.join(tmp, "align2");
	var meta = path.join(leaf, branding.META_DIR);
	fs.mkdirSync(meta, { recursive: true });
	var audit = {
		summary: { any_issues: true, slices_with_issues: 1, issue_counts: { mixed_st_levels: 1 } },
	};
	fs.writeFileSync(path.join(meta, "annotation_label_audit.json"), JSON.stringify(audit), "utf8");
	var read = labelAudit.readAuditCache(leaf);
	assert.strictEqual(read.summary.any_issues, true);
	var html = labelAudit.formatIntensityAuditBanner(read, null, { staleIntensity: false });
	assert.ok(html.indexOf("mix multiple CCF levels") >= 0);
}

function run() {
	var tmp = helpers.tmpDir("mj-label-audit-");
	testStaleWhenNoCache(tmp);
	testReadCache(tmp);
	helpers.rmDir(tmp);
	console.log("test-annotation-label-audit: PASS");
}

run();
