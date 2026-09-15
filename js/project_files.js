"use strict";

var fs = require("fs");
var ipc = require("electron").ipcRenderer;
var perfLog = require("./perf_log");
var project = require("./project");
var fileIndex = require("./file_index");
var pipelineRuns = require("./pipeline_runs");
var activeRunControls = require("./active_run_controls");
var dialogs = require("./dialogs");
var importHandoff = require("./import_handoff");
var geometryState = require("./geometry_state");

var ROLE_DISPLAY_LABELS = {
	max: "Max projection",
	slices: "Atlas alignment",
	predictions: "Cell detection",
	pkls: "Isolate regions",
	quantification: "Quantification",
	dual: "Dual-channel export",
};

function sectionNumberFromSliceId(sliceId) {
	var m = String(sliceId).match(/_s(\d+)/i);
	return m ? m[1] : sliceId;
}

function populateSubsetList(container, selectedIds) {
	if (!container) {
		return;
	}
	container.innerHTML = "";
	var index = project.readProjectFileIndex();
	if (!index) {
		return;
	}
	var report = fileIndex.computeMatchReport(index, fileIndex.INPUT_MATCH_ROLES);
	var ids = report.matchedSliceIds || [];
	selectedIds = selectedIds || [];
	var selected = {};
	for (var i = 0; i < selectedIds.length; i++) {
		selected[selectedIds[i]] = true;
	}
	for (var s = 0; s < ids.length; s++) {
		var sid = ids[s];
		var div = document.createElement("div");
		div.className = "form-check";
		div.innerHTML =
			'<input class="form-check-input subset-slice" type="checkbox" value="' +
			sid +
			'" id="subset_' +
			sid +
			'"' +
			(selected[sid] ? " checked" : "") +
			"/>" +
			'<label class="form-check-label" for="subset_' +
			sid +
			'">' +
			sid +
			" (s" +
			sectionNumberFromSliceId(sid) +
			")</label>";
		container.appendChild(div);
	}
}

function truncateFailureMessage(message, maxLen) {
	maxLen = maxLen || 120;
	var text = String(message || "").replace(/\s+/g, " ").trim();
	if (text.length <= maxLen) {
		return text;
	}
	return text.slice(0, maxLen - 1) + "…";
}

function renderStepFailures() {
	var section = document.getElementById("projectStepFailuresSection");
	var list = document.getElementById("projectStepFailuresList");
	if (!section || !list) {
		return;
	}
	if (!project.isActive()) {
		section.classList.add("d-none");
		list.innerHTML = "";
		return;
	}
	var failures = project.getFailedSliceIds("align");
	if (!failures.length) {
		section.classList.add("d-none");
		list.innerHTML = "";
		return;
	}
	var proc = project.getProject().processing || project.defaultProcessing();
	var alignMap =
		(proc.step_failures && proc.step_failures.align) || {};
	failures.sort();
	list.innerHTML = "";
	for (var i = 0; i < failures.length; i++) {
		var sliceId = failures[i];
		var detail = alignMap[sliceId] || {};
		var line = document.createElement("div");
		line.className = "mb-1";
		line.textContent =
			sliceId +
			" — " +
			truncateFailureMessage(detail.message || "Alignment warp failed") +
			(detail.at ? " — " + detail.at : "");
		list.appendChild(line);
	}
	section.classList.remove("d-none");
}

function appendReadOnlyImportRow(container, label, detail) {
	var row = document.createElement("div");
	row.className = "row align-items-center mb-2";
	var labelCol = document.createElement("div");
	labelCol.className = "col-4 col-sm-3 text-start small";
	labelCol.textContent = label;
	var detailCol = document.createElement("div");
	detailCol.className = "col text-start small text-muted";
	detailCol.textContent = detail;
	row.appendChild(labelCol);
	row.appendChild(detailCol);
	container.appendChild(row);
}

function renderImportInputRows(container, bundleRoot, proj) {
	var czi = proj.settings && proj.settings.czi_import;
	if (!czi) {
		return;
	}
	var handoff = importHandoff.getImportHandoffState(bundleRoot, proj);
	if (!handoff.fromCziImport || handoff.dapiCount === 0) {
		return;
	}
	var sub = document.createElement("h3");
	sub.className = "h6 text-muted mb-2 mt-3";
	sub.textContent = "From CZI import";
	container.appendChild(sub);
	appendReadOnlyImportRow(
		container,
		"Counterstain (DAPI)",
		handoff.dapiCount + " PNG preview(s) in 00_dapi",
	);
	if (handoff.previewCount > 0) {
		appendReadOnlyImportRow(
			container,
			"Orient previews",
			handoff.previewCount + " PNG preview(s) in _previews",
		);
	}
	if (handoff.geometryAppliedAt) {
		appendReadOnlyImportRow(
			container,
			"Orient applied",
			handoff.geometryAppliedAt,
		);
	}
}

function renderGeometryStateBanner() {
	var banner = document.getElementById("geometryStateBanner");
	if (!banner) {
		return;
	}
	if (!project.isActive()) {
		banner.classList.add("d-none");
		banner.innerHTML = "";
		return;
	}
	var workspaceBanner = project.getGeometryWorkspaceBanner();
	if (!geometryState.shouldShowGeometryWorkspaceBanner(workspaceBanner)) {
		var bundleRoot = project.getBundleRoot();
		var proj = project.getProject();
		var czi = proj.settings && proj.settings.czi_import;
		if (czi) {
			var geoState = geometryState.assessGeometryApplyState(bundleRoot, czi);
			if (
				geoState.policyState === "interrupted" ||
				geoState.policyState === "finalize_pending"
			) {
				workspaceBanner = {
					policyState: geoState.policyState,
					message: geometryState.geometryStateBannerText(geoState),
				};
			}
		}
	}
	if (!geometryState.shouldShowGeometryWorkspaceBanner(workspaceBanner)) {
		banner.classList.add("d-none");
		banner.innerHTML = "";
		return;
	}
	banner.classList.remove("d-none");
	banner.className =
		"menu-pipeline-section mb-4 workspace-block text-start alert alert-warning";
	banner.innerHTML =
		'<h2 class="h6 mb-2">Orientation apply needs attention</h2>' +
		'<p class="small mb-3">' +
		workspaceBanner.message +
		"</p>" +
		'<div class="d-flex flex-wrap gap-2">' +
		'<a class="btn btn-warning btn-sm" href="./geometry_repair_wizard.html">Check Orientation Consistency</a>' +
		'<a class="btn btn-outline-secondary btn-sm" href="./orient.html">Orient slices</a>' +
		"</div>";
}

function renderImportNextStepsBanner() {
	var banner = document.getElementById("importNextStepsBanner");
	if (!banner) {
		return;
	}
	if (!project.isActive()) {
		banner.classList.add("d-none");
		banner.innerHTML = "";
		return;
	}
	var bundleRoot = project.getBundleRoot();
	var proj = project.getProject();
	if (!importHandoff.shouldShowImportNextSteps(proj, null, bundleRoot)) {
		banner.classList.add("d-none");
		banner.innerHTML = "";
		return;
	}
	var handoff = importHandoff.getImportHandoffState(bundleRoot, proj);
	banner.classList.remove("d-none");
	banner.className =
		"menu-pipeline-section mb-4 workspace-block text-start alert alert-info";
	banner.innerHTML =
		'<h2 class="h6 mb-2">Next step: atlas alignment</h2>' +
		'<p class="small mb-2">CZI import already produced max projections, DAPI previews (' +
		handoff.dapiCount +
		"), and orient previews (" +
		handoff.previewCount +
		'). You do <strong>not</strong> need to run Max Projection again unless you add new z-stacks.</p>' +
		'<p class="small mb-2">Continue with <strong>Align Sections</strong> using the counterstain channel in <code>00_dapi</code>.</p>' +
		'<p class="small text-muted mb-3">If alignment is difficult, revisit counterstain cleanup under Image preprocessing: DAPI cleanup, semi-manual tissue edge cleanup, or Orient slices.</p>' +
		'<div class="d-flex flex-wrap gap-2">' +
		'<a class="btn btn-primary btn-sm" href="./menu_category.html?cat=alignment">Start atlas alignment</a>' +
		'<a class="btn btn-outline-secondary btn-sm" href="./menu_category.html?cat=preprocess">Preprocess tools</a>' +
		'<button type="button" class="btn btn-link btn-sm" id="dismissImportHandoff">Dismiss</button>' +
		"</div>";
	var dismissBtn = document.getElementById("dismissImportHandoff");
	if (dismissBtn) {
		dismissBtn.addEventListener("click", function () {
			importHandoff.dismissHandoff(bundleRoot);
			renderImportNextStepsBanner();
		});
	}
}

function bindActiveRunControls(containerId) {
	var container = document.getElementById(containerId);
	if (!container || !project.isActive()) {
		if (container) {
			container.classList.add("d-none");
		}
		return;
	}
	container.classList.remove("d-none");
	container.innerHTML = "";
	var heading = document.createElement("h2");
	heading.className = "h6 text-muted mb-2";
	heading.textContent = "Completed tasks (project)";
	container.appendChild(heading);

	var bundleRoot = project.getBundleRoot();
	var proj = project.getProject();
	var hasRows = false;

	for (var i = 0; i < pipelineRuns.OUTPUT_ROLES.length; i++) {
		(function (role) {
			project.ensureDefaultActiveRunForRole(role);
			var choices = project.listRunChoicesForRole(role);
			if (!choices.length) {
				return;
			}
			hasRows = true;
			var row = document.createElement("div");
			row.className = "row align-items-center mb-2";
			var labelCol = document.createElement("div");
			labelCol.className = "col-4 col-sm-3 text-start small";
			labelCol.textContent = ROLE_DISPLAY_LABELS[role] || role;
			var selectCol = document.createElement("div");
			selectCol.className = "col d-flex align-items-center";
			var select = document.createElement("select");
			select.className = "form-select form-select-sm active-run-select flex-grow-1";
			select.dataset.role = role;
			var active = pipelineRuns.getActiveRunRelForRole(role);
			var hasActive = false;
			for (var c = 0; c < choices.length; c++) {
				if (choices[c].rel === active) {
					hasActive = true;
					break;
				}
			}
			if (!hasActive) {
				var placeholder = document.createElement("option");
				placeholder.value = "";
				placeholder.textContent = "— select run —";
				placeholder.selected = true;
				placeholder.disabled = true;
				select.appendChild(placeholder);
			}
			for (var c = 0; c < choices.length; c++) {
				var opt = document.createElement("option");
				opt.value = choices[c].rel;
				opt.textContent = importHandoff.formatChoiceLabel(
					role,
					choices[c].rel,
					proj,
					bundleRoot,
				) || choices[c].label || choices[c].rel || "(flat)";
				if (choices[c].rel === active && hasActive) {
					opt.selected = true;
				}
				select.appendChild(opt);
			}
			select.addEventListener("change", function () {
				project.setActiveRunForRole(role, select.value);
				project.refreshProjectIndex().catch(function () {});
			});
			selectCol.appendChild(select);
			if (role === "predictions" && choices.length > 1) {
				selectCol.appendChild(activeRunControls.attachRunBrowseButton(role));
			} else {
				selectCol.appendChild(
					activeRunControls.attachRunDeleteButton(select, role, {
						onDeleted: function () {
							bindActiveRunControls(containerId);
							renderImportNextStepsBanner();
						},
					}),
				);
			}
			row.appendChild(labelCol);
			row.appendChild(selectCol);
			container.appendChild(row);
		})(pipelineRuns.OUTPUT_ROLES[i]);
	}

	renderImportInputRows(container, bundleRoot, proj);
	renderBasicCompletedRows(container, bundleRoot, proj);
	renderDetectQcScoutRow(container, bundleRoot, proj);
	if (!hasRows && !(proj.settings && proj.settings.czi_import)) {
		var hasQc =
			proj.processing &&
			proj.processing.detect_qc &&
			proj.processing.detect_qc.output_rel;
		var hasBasic =
			(proj.processing && proj.processing.basic) ||
			fs.existsSync(
				require("path").join(bundleRoot, "data", "counting", "00_dapi_basic"),
			);
		if (!hasQc && !hasBasic) {
			container.classList.add("d-none");
		}
	}
}

function renderBasicCompletedRows(container, bundleRoot, proj) {
	if (!container || !bundleRoot || !proj) {
		return;
	}
	var basic = (proj.processing && proj.processing.basic) || null;
	var dapiBasic = pathJoin(bundleRoot, "data", "counting", "00_dapi_basic");
	var nDapi = 0;
	try {
		if (fs.existsSync(dapiBasic)) {
			nDapi = fs
				.readdirSync(dapiBasic)
				.filter(function (n) {
					return /\.png$/i.test(n);
				}).length;
		}
	} catch (_e) {}
	if (nDapi > 0) {
		appendReadOnlyImportRow(
			container,
			"DAPI (BaSiC shading)",
			nDapi + " corrected PNG(s) in 00_dapi_basic (display only — Align warp still uses 00_dapi)",
		);
	}
	if (basic && Array.isArray(basic.channels)) {
		var done = basic.channels.filter(function (c) {
			return c && c.status === "done";
		});
		if (done.length) {
			appendReadOnlyImportRow(
				container,
				"Shading correction (BaSiC)",
				done.length +
					" channel(s) last finished " +
					(basic.last_run_at || ""),
			);
		}
	}
}

function pathJoin() {
	var path = require("path");
	return path.join.apply(path, arguments);
}

function renderDetectQcScoutRow(container, bundleRoot, proj) {
	var detectQcScout = require("./detect_qc_scout");
	var qc = detectQcScout.readDetectQc(proj);
	if (!qc || !qc.output_rel) {
		return;
	}
	var abs = detectQcScout.resolveScoutOutputAbs(
		bundleRoot,
		(proj && proj.roles) || null,
		qc,
	);
	if (!abs || !fs.existsSync(abs)) {
		return;
	}
	var row = document.createElement("div");
	row.className = "row align-items-center mb-2";
	var labelCol = document.createElement("div");
	labelCol.className = "col-4 col-sm-3 text-start small";
	labelCol.textContent = "Detect QC scout";
	var selectCol = document.createElement("div");
	selectCol.className = "col d-flex align-items-center flex-wrap gap-1";
	var status = document.createElement("span");
	status.className = "small text-success me-2";
	var sug = detectQcScout.suggestionIntensityMin(qc);
	status.textContent =
		"QC available" +
		(sug != null ? " · suggested intensity " + String(sug) : "");
	selectCol.appendChild(status);
	var browse = document.createElement("button");
	browse.type = "button";
	browse.className = "btn btn-sm btn-outline-secondary";
	browse.textContent = "Browse";
	browse.title = "Open Detect QC scout folder";
	browse.addEventListener("click", function () {
		ipc.send("openPathInShell", abs);
	});
	selectCol.appendChild(browse);
	row.appendChild(labelCol);
	row.appendChild(selectCol);
	container.appendChild(row);
}

function bindProjectFileControls(options) {
	options = options || {};
	var subsetSection = document.getElementById("projectSubsetSection");
	var subsetToggle = document.getElementById("subsetEnabled");
	var subsetList = document.getElementById("subsetSliceList");
	var rescanBtn = document.getElementById("rescanProject");
	var addFilesBtn = document.getElementById("addFilesToRole");
	var reimportCziBtn = document.getElementById("reimportCziSections");

	if (!project.isActive()) {
		if (subsetSection) {
			subsetSection.classList.add("d-none");
		}
		if (rescanBtn) {
			rescanBtn.classList.add("d-none");
		}
		if (addFilesBtn) {
			addFilesBtn.classList.add("d-none");
		}
		if (reimportCziBtn) {
			reimportCziBtn.classList.add("d-none");
		}
		return;
	}

	var proj = project.getProject();

	if (subsetSection) {
		subsetSection.classList.remove("d-none");
	}
	if (rescanBtn) {
		rescanBtn.classList.remove("d-none");
	}
	if (addFilesBtn) {
		addFilesBtn.classList.remove("d-none");
	}
	if (reimportCziBtn) {
		var hasCziImport =
			proj.settings && proj.settings.czi_import && proj.settings.czi_import.files;
		reimportCziBtn.classList.toggle("d-none", !hasCziImport);
	}

	perfLog.perfSection("hub.renderGeometryStateBanner", function () {
		renderGeometryStateBanner();
	});
	perfLog.perfSection("hub.renderImportNextStepsBanner", function () {
		renderImportNextStepsBanner();
	});
	perfLog.perfSection("hub.bindActiveRunControls", function () {
		bindActiveRunControls("projectActiveRunsSection");
	});
	perfLog.perfSection("hub.renderStepFailures", function () {
		renderStepFailures();
	});

	var proc = proj.processing || project.defaultProcessing();
	if (subsetToggle) {
		subsetToggle.checked = !!proc.subset_enabled;
		if (subsetList) {
			subsetList.classList.toggle("d-none", !proc.subset_enabled);
		}
		perfLog.perfSection("hub.populateSubsetList", function () {
			populateSubsetList(subsetList, proc.slice_ids || []);
		});
		subsetToggle.addEventListener("change", function () {
			if (!proj.processing) {
				proj.processing = project.defaultProcessing();
			}
			proj.processing.subset_enabled = subsetToggle.checked;
			if (subsetList) {
				subsetList.classList.toggle("d-none", !subsetToggle.checked);
			}
			project.saveProjectJson();
		});
	}

	function saveSubsetIds() {
		if (!subsetList) {
			return;
		}
		var boxes = subsetList.querySelectorAll(".subset-slice:checked");
		var ids = [];
		for (var i = 0; i < boxes.length; i++) {
			ids.push(boxes[i].value);
		}
		if (!proj.processing) {
			proj.processing = project.defaultProcessing();
		}
		proj.processing.slice_ids = ids;
		project.saveProjectJson();
	}

	if (subsetList) {
		subsetList.addEventListener("change", function (ev) {
			if (ev.target && ev.target.classList.contains("subset-slice")) {
				saveSubsetIds();
			}
		});
	}

	if (rescanBtn) {
		rescanBtn.addEventListener("click", function () {
			rescanBtn.disabled = true;
			var projectIndexBusy = require("./project_index_busy");
			projectIndexBusy.show();
			project
				.refreshProjectIndex()
				.then(function () {
					populateSubsetList(
						subsetList,
						(proj.processing && proj.processing.slice_ids) || [],
					);
					if (typeof options.onRescan === "function") {
						options.onRescan();
					}
					alert("Project file index refreshed.");
				})
				.catch(function (err) {
					alert(String(err.message || err));
				})
				.finally(function () {
					projectIndexBusy.hide();
					rescanBtn.disabled = false;
				});
		});
	}

	if (addFilesBtn) {
		addFilesBtn.addEventListener("click", function () {
			var chosenRole = "";
			dialogs
				.pickProjectRole(project.CANONICAL_ROLES, "dapi")
				.then(function (role) {
					if (!role) {
						return null;
					}
					if (!project.CANONICAL_ROLES[role]) {
						alert("Unknown role: " + role);
						return null;
					}
					chosenRole = role;
					return dialogs.pickDirectory({ tag: "addFiles_" + role });
				})
				.then(function (selected) {
					if (!selected || !chosenRole) {
						return;
					}
					var bundleRoot = project.getBundleRoot();
					var roles = proj.roles || project.CANONICAL_ROLES;
					var destDir = project.resolveRolePath(chosenRole);
					if (!destDir) {
						alert("Could not resolve role path.");
						return;
					}
					project.importSourceToRoleWithLayout(
						selected,
						chosenRole,
						"copy",
						bundleRoot,
						roles,
					);
					return project.refreshProjectIndex();
				})
				.then(function (refreshed) {
					if (!refreshed) {
						return;
					}
					populateSubsetList(
						subsetList,
						(proj.processing && proj.processing.slice_ids) || [],
					);
					alert("Files imported into " + chosenRole + "; index refreshed.");
				})
				.catch(function (err) {
					alert(String(err.message || err));
				});
		});
	}
}

module.exports = {
	bindProjectFileControls: bindProjectFileControls,
	bindActiveRunControls: bindActiveRunControls,
	populateSubsetList: populateSubsetList,
	renderStepFailures: renderStepFailures,
	renderGeometryStateBanner: renderGeometryStateBanner,
	renderImportNextStepsBanner: renderImportNextStepsBanner,
};
