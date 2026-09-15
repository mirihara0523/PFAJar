"use strict";

var fs = require("fs");
var path = require("path");
var ipc = require("electron").ipcRenderer;
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRuns = require("./pipeline_runs");
var structureCatalog = require("./structure_catalog");
var atlasStyle = require("./atlas_region_style");
var wizardBusy = require("./wizard_busy");
var regionDualList = require("./region_dual_list");
var projectIndexBusy = require("./project_index_busy");

var PLAN_KEY = "masonjar.parcellationPlan";
var CONFIG_FILENAME = "parcellation_run_config.json";
var LOG_MAX = 1500;

var catalog = null;
var wizardStep = 1;
var sliceIds = [];
var selectedSliceIds = {};
var includedRegionIds = [];
var regionList = null;
var ccfAdvanced = false;
var tierId = "areas";
var stLevel = 6;
var running = false;
var summary = { ok: 0, failed: 0, total: 0 };
var runHeartbeatTimer = null;
var lastProgressAt = 0;

function qs(id) {
	return document.getElementById(id);
}

function getAppRoot() {
	var p = decodeURIComponent(window.location.pathname || "");
	p = p.replace(/\\/g, "/");
	if (/^\/[A-Za-z]:\//.test(p)) {
		p = p.slice(1);
	}
	return path.dirname(path.dirname(p));
}

function escapeHtml(s) {
	return String(s)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}

function setStep(step) {
	wizardStep = step;
	var panels = document.querySelectorAll(".wizard-panel");
	for (var i = 0; i < panels.length; i++) {
		panels[i].classList.add("d-none");
	}
	var active = qs("step" + step);
	if (active) active.classList.remove("d-none");
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	for (var p = 0; p < pills.length; p++) {
		var pillStep = Number(pills[p].getAttribute("data-step"));
		pills[p].classList.remove("active", "disabled");
		if (pillStep === step) pills[p].classList.add("active");
		else pills[p].classList.add("disabled");
	}
}

function savePlan() {
	var plan = {
		tierId: tierId,
		stLevel: stLevel,
		ccfAdvanced: ccfAdvanced,
		includedRegionIds: includedRegionIds.slice(),
		selectedSliceIds: Object.keys(selectedSliceIds).filter(function (k) {
			return selectedSliceIds[k];
		}),
	};
	sessionStorage.setItem(PLAN_KEY, JSON.stringify(plan));
}

function loadPlan() {
	try {
		var raw = sessionStorage.getItem(PLAN_KEY);
		if (!raw) return;
		var plan = JSON.parse(raw);
		tierId = plan.tierId || "areas";
		stLevel = plan.stLevel != null ? plan.stLevel : 6;
		ccfAdvanced = !!plan.ccfAdvanced;
		// Legacy `excludedRegionIds` is the inverse set of included regions; do
		// not load it as included (that inverts the selection). Re-pick instead.
		includedRegionIds = (plan.includedRegionIds || []).slice();
		if (plan.selectedSliceIds) {
			for (var i = 0; i < plan.selectedSliceIds.length; i++) {
				selectedSliceIds[plan.selectedSliceIds[i]] = true;
			}
		}
	} catch (_e) {
		/* ignore */
	}
}

function annotationDirAbs() {
	if (!project.isActive()) return "";
	var bundleRoot = project.getBundleRoot();
	var proj = project.readProjectJson(bundleRoot);
	var roles = (proj && proj.roles) || project.CANONICAL_ROLES;
	var processing = proj ? proj.processing : null;
	return pipelineRuns.resolveActiveRunLeafAbsForBundle(
		bundleRoot,
		roles,
		processing,
		"slices",
	);
}

function listAnnotationSliceIds(annodir) {
	if (!annodir || !fs.existsSync(annodir)) return [];
	var out = [];
	try {
		var entries = fs.readdirSync(annodir);
		for (var i = 0; i < entries.length; i++) {
			var m = /^Annotation_(.+)\.pkl$/i.exec(entries[i]);
			if (m) out.push(m[1]);
		}
	} catch (_e) {
		return [];
	}
	return out.sort();
}

function readSliceParcellationMeta(annodir, sliceId) {
	var metaPath = path.join(annodir, ".masonjar", "annotation_parcellation.json");
	if (!fs.existsSync(metaPath)) return null;
	try {
		var meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
		return meta[sliceId] || null;
	} catch (_e) {
		return null;
	}
}

function formatMetaStatus(entry) {
	if (!entry) return "Full detail";
	var tid = entry.tier_id;
	var sl = entry.st_level;
	if (tid) return tid;
	if (sl != null) return "Level " + sl;
	return "Applied";
}

function initStep1() {
	var bundleRoot = project.getBundleRoot();
	var projInfo = qs("projectInfo");
	if (projInfo) {
		projInfo.textContent = "Project: " + bundleRoot;
	}
	var annodir = annotationDirAbs();
	var leafInfo = qs("slicesLeafInfo");
	if (leafInfo) {
		leafInfo.textContent = annodir
			? "Active slices leaf: " + annodir
			: "No active slices leaf — run Align first.";
	}
	sliceIds = listAnnotationSliceIds(annodir);
	if (!Object.keys(selectedSliceIds).length) {
		for (var i = 0; i < sliceIds.length; i++) {
			selectedSliceIds[sliceIds[i]] = true;
		}
	}
	var tbody = qs("sliceTableBody");
	if (!tbody) return;
	tbody.innerHTML = "";
	for (var s = 0; s < sliceIds.length; s++) {
		var sid = sliceIds[s];
		var entry = readSliceParcellationMeta(annodir, sid);
		var tr = document.createElement("tr");
		tr.innerHTML =
			'<td><input type="checkbox" class="slice-check" data-slice="' +
			escapeHtml(sid) +
			'" ' +
			(selectedSliceIds[sid] ? "checked" : "") +
			" /></td>" +
			"<td>" +
			escapeHtml(sid) +
			"</td>" +
			'<td class="small text-muted">' +
			escapeHtml(formatMetaStatus(entry)) +
			"</td>";
		tbody.appendChild(tr);
	}
	var checks = tbody.querySelectorAll(".slice-check");
	for (var c = 0; c < checks.length; c++) {
		checks[c].addEventListener("change", function (ev) {
			var el = ev.target;
			var id = el.getAttribute("data-slice");
			selectedSliceIds[id] = el.checked;
			savePlan();
		});
	}
}

function initTierPicker() {
	if (!catalog) {
		catalog = structureCatalog.loadCatalog(getAppRoot());
	}
	var tierSel = qs("tierSelect");
	var levelSel = qs("levelSelect");
	if (tierSel && !tierSel.dataset.populated) {
		var tiers = structureCatalog.listTiers(catalog);
		for (var t = 0; t < tiers.length; t++) {
			var opt = document.createElement("option");
			opt.value = tiers[t].id;
			opt.textContent = tiers[t].label;
			tierSel.appendChild(opt);
		}
		tierSel.value = tierId;
		tierSel.dataset.populated = "1";
		tierSel.addEventListener("change", function () {
			tierId = tierSel.value;
			if (regionList) regionList.render();
		});
	}
	if (levelSel && !levelSel.dataset.populated) {
		var infos = structureCatalog.listCcfLevels(catalog);
		for (var i = 0; i < infos.length; i++) {
			var lopt = document.createElement("option");
			lopt.value = String(infos[i].level);
			lopt.textContent = structureCatalog.formatCcfLevelLabel(infos[i]);
			levelSel.appendChild(lopt);
		}
		levelSel.value = String(stLevel);
		levelSel.dataset.populated = "1";
		levelSel.addEventListener("change", function () {
			stLevel = parseInt(levelSel.value, 10);
			if (regionList) regionList.render();
		});
	}
	var adv = qs("ccfAdvancedToggle");
	if (adv && !adv.dataset.bound) {
		adv.dataset.bound = "1";
		adv.checked = ccfAdvanced;
		adv.addEventListener("change", function () {
			ccfAdvanced = adv.checked;
			tierSel.classList.toggle("d-none", ccfAdvanced);
			levelSel.classList.toggle("d-none", !ccfAdvanced);
			if (regionList) regionList.render();
		});
		tierSel.classList.toggle("d-none", ccfAdvanced);
		levelSel.classList.toggle("d-none", !ccfAdvanced);
	}
	var search = qs("regionSearch");
	if (search && !search.dataset.bound) {
		search.dataset.bound = "1";
		search.addEventListener("input", function () {
			if (regionList) {
				regionList.setSearchQuery(search.value);
			}
		});
	}
	if (!regionList) {
		regionList = regionDualList.createRegionDualList({
			availablePanel: qs("availableList"),
			includedPanel: qs("includedList"),
			addBtn: qs("addInclude"),
			removeBtn: qs("removeInclude"),
			addAllBtn: qs("addAllInclude"),
			removeAllBtn: qs("removeAllInclude"),
			listAvailableRegions: regionsForPicker,
			getRegionById: function (id) {
				return catalog && catalog.byId ? catalog.byId[id] : null;
			},
			rowStyle: function (node) {
				return atlasStyle.rowStyleForRegion(node, catalog.byId);
			},
			onIncludedChange: function (ids) {
				includedRegionIds = ids.slice();
				savePlan();
			},
			initialIncludedIds: includedRegionIds,
		});
	} else {
		regionList.setIncludedIds(includedRegionIds);
		if (search) {
			regionList.setSearchQuery(search.value);
		}
	}
}

function regionsForPicker(search) {
	if (ccfAdvanced) {
		return structureCatalog.listRegionsAtLevel(stLevel, search, catalog);
	}
	return structureCatalog.listRegionsForTier(tierId, search, catalog);
}

function selectedSlices() {
	return sliceIds.filter(function (sid) {
		return selectedSliceIds[sid];
	});
}

function targetLabel() {
	if (ccfAdvanced) return "CCFv3 level " + stLevel;
	if (tierId === "full") return "Full detail";
	var tiers = structureCatalog.listTiers(catalog || { byId: {} });
	for (var i = 0; i < tiers.length; i++) {
		if (tiers[i].id === tierId) return tiers[i].label;
	}
	return tierId;
}

function isPlanValid() {
	if (ccfAdvanced) return true;
	return tierId !== "full";
}

function fillReview() {
	var selected = selectedSlices();
	if (qs("reviewTarget")) qs("reviewTarget").textContent = targetLabel();
	if (qs("reviewSlices")) {
		qs("reviewSlices").textContent =
			selected.length + " section(s): " + selected.slice(0, 5).join(", ") +
			(selected.length > 5 ? " …" : "");
	}
	if (qs("reviewIncluded")) {
		qs("reviewIncluded").textContent = includedRegionIds.length
			? includedRegionIds.length + " region(s) included"
			: "All regions (no inclusion filter)";
	}
}

function writeConfig(annodir) {
	var bundleRoot = project.getBundleRoot();
	var metaDir = project.metaDirPath(bundleRoot);
	if (!fs.existsSync(metaDir)) fs.mkdirSync(metaDir, { recursive: true });
	var cfgPath = path.join(metaDir, CONFIG_FILENAME);
	var cfg = {
		annotation_dir: annodir,
		tier_id: ccfAdvanced ? null : tierId,
		st_level: ccfAdvanced ? stLevel : null,
		included_region_ids: includedRegionIds.slice(),
		slice_ids: selectedSlices(),
	};
	fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2), "utf8");
	return cfgPath;
}

function appendLog(line) {
	var pre = qs("runLog");
	if (!pre) return;
	pre.textContent += line + "\n";
	var lines = pre.textContent.split("\n");
	if (lines.length > LOG_MAX) {
		pre.textContent = lines.slice(lines.length - LOG_MAX).join("\n");
	}
	pre.scrollTop = pre.scrollHeight;
}

function clearRunHeartbeat() {
	if (runHeartbeatTimer) {
		clearInterval(runHeartbeatTimer);
		runHeartbeatTimer = null;
	}
}

function startRunHeartbeat() {
	clearRunHeartbeat();
	lastProgressAt = Date.now();
	runHeartbeatTimer = setInterval(function () {
		if (!running || wizardStep !== 4) return;
		if (Date.now() - lastProgressAt > 2000) {
			appendLog("[ParcellationWizard] Waiting for Python…");
			lastProgressAt = Date.now();
		}
	}, 2000);
}

function startRun() {
	if (running) {
		return;
	}
	var annodir = annotationDirAbs();
	if (!annodir || !fs.existsSync(annodir)) {
		alert("No active slices leaf.");
		return;
	}
	var selected = selectedSlices();
	if (!selected.length) {
		alert("Select at least one section.");
		return;
	}
	if (!isPlanValid()) {
		alert("Choose a coarser hierarchy than full detail.");
		return;
	}
	savePlan();
	var cfgPath = writeConfig(annodir);
	running = true;
	summary = { ok: 0, failed: 0, total: selected.length };
	wizardBusy.setWizardBusy({
		busy: true,
		primarySelector: "#step3Start",
		backSelectors: ["#step3Back"],
		stepPillSelector: "#wizardSteps .nav-link",
		messageEl: "#runStatus",
		message: "Launching parcellation…",
	});
	setStep(4);
	var bar = qs("runProgress");
	if (bar) {
		bar.style.width = "0%";
		bar.setAttribute("aria-valuenow", "0");
	}
	if (qs("runStatus")) qs("runStatus").textContent = "Launching parcellation…";
	appendLog("[ParcellationWizard] Starting apply_parcellation.py");
	startRunHeartbeat();
	ipc.send("runParcellation", [annodir, cfgPath]);
}

function finishRun(success) {
	running = false;
	clearRunHeartbeat();
	wizardBusy.setWizardBusy({ busy: false });
	setStep(5);
	var alertEl = qs("summaryAlert");
	if (alertEl) {
		alertEl.className = success
			? "alert alert-success text-start"
			: "alert alert-warning text-start";
		alertEl.textContent =
			"Parcellation complete: " +
			summary.ok +
			" ok, " +
			summary.failed +
			" failed (of " +
			summary.total +
			" selected).";
	}
	try {
		var bundleRoot = project.getBundleRoot();
		var summaryPath = path.join(
			project.metaDirPath(bundleRoot),
			"last_parcellation_summary.json",
		);
		fs.writeFileSync(
			summaryPath,
			JSON.stringify(
				{
					at: new Date().toISOString(),
					ok: summary.ok,
					failed: summary.failed,
					total: summary.total,
					target: targetLabel(),
				},
				null,
				2,
			),
			"utf8",
		);
	} catch (_e) {
		/* ignore */
	}
}

function bindUi() {
	var selectAll = qs("selectAllSlices");
	if (selectAll) {
		selectAll.addEventListener("change", function () {
			var checked = selectAll.checked;
			for (var i = 0; i < sliceIds.length; i++) {
				selectedSliceIds[sliceIds[i]] = checked;
			}
			initStep1();
			savePlan();
		});
	}
	if (qs("step1Next")) {
		qs("step1Next").addEventListener("click", function () {
			if (!selectedSlices().length) {
				alert("Select at least one section.");
				return;
			}
			setStep(2);
			initTierPicker();
		});
	}
	if (qs("step2Back")) {
		qs("step2Back").addEventListener("click", function () {
			setStep(1);
		});
	}
	if (qs("step2Next")) {
		qs("step2Next").addEventListener("click", function () {
			if (!isPlanValid()) {
				alert("Choose a coarser hierarchy than full detail.");
				return;
			}
			fillReview();
			setStep(3);
		});
	}
	if (qs("step3Back")) {
		qs("step3Back").addEventListener("click", function () {
			setStep(2);
		});
	}
	if (qs("step3Start")) {
		qs("step3Start").addEventListener("click", function () {
			if (running) return;
			startRun();
		});
	}
	if (qs("cancelRun")) {
		qs("cancelRun").addEventListener("click", function () {
			if (running) {
				ipc.send("killParcellation");
				clearRunHeartbeat();
				wizardBusy.setWizardBusy({ busy: false });
				running = false;
			}
		});
	}
}

ipc.on("updateLoad", function (_ev, data) {
	if (!running || wizardStep !== 4) return;
	lastProgressAt = Date.now();
	var pct = data[0];
	var msg = data[1] || "";
	var bar = qs("runProgress");
	if (bar) {
		bar.style.width = pct + "%";
		bar.setAttribute("aria-valuenow", String(pct));
	}
	if (qs("runStatus")) qs("runStatus").textContent = msg || "Running…";
});

ipc.on("log", function (_ev, line) {
	if (wizardStep === 4 && line && String(line).indexOf("LOG:") === 0) {
		appendLog(String(line));
		if (String(line).indexOf(" ok=") >= 0) {
			if (String(line).indexOf(" ok=True") >= 0 || String(line).indexOf(" ok=true") >= 0) {
				summary.ok++;
			} else {
				summary.failed++;
			}
		}
	}
});

ipc.on("parcellationResult", function () {
	finishRun(summary.failed === 0);
});

projectIndexBusy.populatePage(function () {
	project.tryRestoreActiveProject();
	pipelineGate.assertPipelineAccess();
	loadPlan();
	initStep1();
	bindUi();
	setStep(1);

	var annodir = annotationDirAbs();
	if (!annodir || !sliceIds.length) {
		var info = qs("slicesLeafInfo");
		if (info) {
			info.classList.add("text-danger");
			info.textContent =
				"No annotation PKLs in the active align run. Run Align first, then return here.";
		}
		if (qs("step1Next")) qs("step1Next").disabled = true;
	}
});
