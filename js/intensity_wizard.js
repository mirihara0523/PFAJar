"use strict";

var fs = require("fs");
var path = require("path");
var os = require("os");
var ipc = require("electron").ipcRenderer;
var project = require("./project");
var pipelineGate = require("./pipeline_gate");
var pipelineRuns = require("./pipeline_runs");
var branding = require("./branding");
var structureCatalog = require("./structure_catalog");
var atlasStyle = require("./atlas_region_style");
var parcelCtx = require("./parcellation_context");
var labelAudit = require("./annotation_label_audit");
var wizardBusy = require("./wizard_busy");

var SETUP_KEY = "masonjar.intensity.setup";
var CONFIG_FILENAME = "intensity_run_config.json";
var LOG_MAX = 1500;
var PICKER_MODE_KEY = "masonjar.ccfPickerMode";
var DEFAULT_TIER_ID = "areas";
var DEFAULT_CCF_LEVEL = 6;

var setup = null;
var catalog = null;
var wizardStep = 2;
var selectedIds = [];
var availableHighlight = null;
var selectedHighlight = null;
var extractRunning = false;
var lastRunRel = "";
var lastResult = { slices: 0, pkls: 0, outputDir: "" };
var pickerMode = "tiers"; // "tiers" | "advanced"
var parcelSummary = null;
var labelAuditData = null;

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

function loadSetup() {
	try {
		var raw = sessionStorage.getItem(SETUP_KEY);
		if (!raw) {
			return null;
		}
		return JSON.parse(raw);
	} catch (_e) {
		return null;
	}
}

function configPathForSetup() {
	if (project.isActive() && setup && setup.bundleRoot) {
		return path.join(project.metaDirPath(setup.bundleRoot), CONFIG_FILENAME);
	}
	return path.join(os.tmpdir(), "masonjar_" + CONFIG_FILENAME);
}

function setStep(step) {
	wizardStep = step;
	var panels = document.querySelectorAll(".wizard-panel");
	for (var i = 0; i < panels.length; i++) {
		panels[i].classList.add("d-none");
	}
	var active = qs("step" + step);
	if (active) {
		active.classList.remove("d-none");
	}
	var pills = document.querySelectorAll("#wizardSteps .nav-link");
	for (var p = 0; p < pills.length; p++) {
		var pillStep = Number(pills[p].getAttribute("data-step"));
		pills[p].classList.remove("active", "disabled");
		if (pillStep === step) {
			pills[p].classList.add("active");
		} else {
			pills[p].classList.add("disabled");
		}
	}
}

function regionLabel(node) {
	return node.acronym + " — " + node.name;
}

function applyRowStyle(el, node) {
	var style = atlasStyle.rowStyleForRegion(node, catalog.byId);
	el.className = atlasStyle.rowClasses() + (el.classList.contains("selected-row") ? " selected-row" : "");
	el.style.borderLeftColor = style.borderLeftColor;
	el.style.backgroundColor = style.backgroundColor;
	var sw = document.createElement("span");
	sw.className = "region-swatch";
	sw.style.backgroundColor = style.swatchColor;
	el.appendChild(sw);
	el.appendChild(document.createTextNode(regionLabel(node)));
	el.setAttribute("data-id", String(node.id));
}

function currentRegions(search) {
	if (pickerMode === "advanced") {
		var level = Number(qs("levelSelect").value);
		return structureCatalog.listRegionsAtLevel(level, search, catalog);
	}
	var tierId = qs("tierSelect") ? qs("tierSelect").value : DEFAULT_TIER_ID;
	return structureCatalog.listRegionsForTier(tierId, search, catalog);
}

function visibleRegionsForLegend() {
	var search = qs("regionSearch") ? qs("regionSearch").value : "";
	var available = currentRegions(search);
	var seen = {};
	var out = [];
	function addNode(n) {
		var gid = n.groupParentId;
		if (seen[gid]) {
			return;
		}
		seen[gid] = true;
		var g = catalog.byId[gid];
		if (g) {
			out.push(g);
		}
	}
	for (var i = 0; i < available.length; i++) {
		addNode(available[i]);
	}
	for (var j = 0; j < selectedIds.length; j++) {
		var sel = catalog.byId[selectedIds[j]];
		if (sel) {
			addNode(sel);
		}
	}
	out.sort(function (a, b) {
		return a.acronym.localeCompare(b.acronym);
	});
	return out;
}

function renderLegend() {
	var leg = qs("regionLegend");
	if (!leg) {
		return;
	}
	leg.innerHTML = "";
	var groups = visibleRegionsForLegend();
	for (var i = 0; i < groups.length; i++) {
		var g = groups[i];
		var item = document.createElement("span");
		item.className = "region-legend-item";
		var sw = document.createElement("span");
		sw.className = "region-swatch";
		sw.style.backgroundColor = atlasStyle.colorHexForGroup(g);
		item.appendChild(sw);
		item.appendChild(document.createTextNode(g.acronym + " — " + g.name));
		leg.appendChild(item);
	}
}

function renderAvailableList() {
	var box = qs("availableList");
	if (!box) {
		return;
	}
	box.innerHTML = "";
	var search = qs("regionSearch") ? qs("regionSearch").value : "";
	var rows = currentRegions(search);
	var selectedSet = {};
	for (var s = 0; s < selectedIds.length; s++) {
		selectedSet[selectedIds[s]] = true;
	}
	for (var i = 0; i < rows.length; i++) {
		var node = rows[i];
		if (selectedSet[node.id]) {
			continue;
		}
		var row = document.createElement("div");
		row.setAttribute("role", "option");
		applyRowStyle(row, node);
		if (availableHighlight === node.id) {
			row.classList.add("selected-row");
		}
		row.addEventListener("click", function (id) {
			return function () {
				availableHighlight = id;
				selectedHighlight = null;
				renderAvailableList();
				renderSelectedList();
			};
		}(node.id));
		row.addEventListener("dblclick", function (id) {
			return function () {
				addSelectedId(id);
			};
		}(node.id));
		box.appendChild(row);
	}
	renderLegend();
	updateRegionHint();
}

function renderSelectedList() {
	var box = qs("selectedList");
	if (!box) {
		return;
	}
	box.innerHTML = "";
	for (var i = 0; i < selectedIds.length; i++) {
		var id = selectedIds[i];
		var node = catalog.byId[id];
		if (!node) {
			continue;
		}
		var row = document.createElement("div");
		row.setAttribute("role", "option");
		applyRowStyle(row, node);
		if (selectedHighlight === id) {
			row.classList.add("selected-row");
		}
		row.addEventListener("click", function (rid) {
			return function () {
				selectedHighlight = rid;
				availableHighlight = null;
				renderAvailableList();
				renderSelectedList();
			};
		}(id));
		box.appendChild(row);
	}
	renderLegend();
	updateRegionHint();
}

function updateRegionHint() {
	var hint = qs("regionHint");
	if (!hint) {
		return;
	}
	hint.textContent =
		selectedIds.length +
		" region(s) selected. Change Hierarchy or toggle Advanced to add regions from other tiers/depths without clearing the selection.";
}

function addSelectedId(id) {
	if (selectedIds.indexOf(id) >= 0) {
		return;
	}
	selectedIds.push(id);
	renderAvailableList();
	renderSelectedList();
}

function removeSelectedId(id) {
	selectedIds = selectedIds.filter(function (x) {
		return x !== id;
	});
	renderAvailableList();
	renderSelectedList();
}

function fillTierSelect() {
	var sel = qs("tierSelect");
	if (!sel) {
		return;
	}
	var tiers = structureCatalog.listTiers(catalog);
	sel.innerHTML = "";
	for (var i = 0; i < tiers.length; i++) {
		var tier = tiers[i];
		var opt = document.createElement("option");
		opt.value = tier.id;
		opt.textContent = tier.label;
		opt.title = tier.description || "";
		sel.appendChild(opt);
	}
	sel.value = DEFAULT_TIER_ID;
}

function fillLevelSelect() {
	var sel = qs("levelSelect");
	if (!sel) {
		return;
	}
	var infos = structureCatalog.listCcfLevels(catalog);
	sel.innerHTML = "";
	for (var i = 0; i < infos.length; i++) {
		var info = infos[i];
		var opt = document.createElement("option");
		opt.value = String(info.level);
		opt.textContent = structureCatalog.formatCcfLevelLabel(info);
		sel.appendChild(opt);
	}
	var has = infos.some(function (info) {
		return info.level === DEFAULT_CCF_LEVEL;
	});
	sel.value = String(has && infos.length ? DEFAULT_CCF_LEVEL : (infos[0] && infos[0].level) || 0);
}

function applyPickerMode(mode) {
	pickerMode = mode === "advanced" ? "advanced" : "tiers";
	try {
		sessionStorage.setItem(PICKER_MODE_KEY, pickerMode);
	} catch (_e) {
		// sessionStorage may be unavailable in some test contexts
	}
	var tierSel = qs("tierSelect");
	var levelSel = qs("levelSelect");
	var help = qs("ccfAdvancedHelp");
	var toggle = qs("ccfAdvancedToggle");
	if (tierSel) {
		tierSel.classList.toggle("d-none", pickerMode === "advanced");
	}
	if (levelSel) {
		levelSel.classList.toggle("d-none", pickerMode !== "advanced");
	}
	if (help) {
		help.classList.toggle("d-none", pickerMode !== "advanced");
		if (pickerMode === "advanced") {
			help.textContent = structureCatalog.CCF_ADVANCED_HELP;
		}
	}
	if (toggle) {
		toggle.checked = pickerMode === "advanced";
	}
	availableHighlight = null;
	renderAvailableList();
	renderSelectedList();
}

function restorePickerMode() {
	var stored = null;
	try {
		stored = sessionStorage.getItem(PICKER_MODE_KEY);
	} catch (_e) {
		stored = null;
	}
	applyPickerMode(stored === "advanced" ? "advanced" : "tiers");
}

function appendLog(line) {
	var pre = qs("extractLog");
	if (!pre) {
		return;
	}
	var text = pre.textContent + line + "\n";
	var lines = text.split("\n");
	if (lines.length > LOG_MAX) {
		text = lines.slice(lines.length - LOG_MAX).join("\n");
	}
	pre.textContent = text;
	pre.scrollTop = pre.scrollHeight;
}

function setProgress(pct, msg) {
	var bar = qs("extractProgress");
	if (bar) {
		bar.style.width = String(pct) + "%";
		bar.setAttribute("aria-valuenow", String(pct));
	}
	if (qs("extractStatus") && msg) {
		qs("extractStatus").textContent = msg;
	}
}

function writeRunConfig(finalOut) {
	var includeLayers = qs("includeLayers") && qs("includeLayers").checked;
	var cfg = {
		selected_region_ids: selectedIds.slice(),
		include_layers: includeLayers,
		whole: setup.whole === "True",
		use_dapi: !!setup.useDapi,
		input_dir: setup.indir,
		annotation_dir: setup.annodir,
		output_dir: finalOut,
		dapi_dir: setup.dapiPath || "",
		slice_list: setup.sliceListPath || "",
	};
	var cfgPath = configPathForSetup();
	fs.mkdirSync(path.dirname(cfgPath), { recursive: true });
	fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2), "utf8");
	return { cfgPath: cfgPath, includeLayers: includeLayers };
}

function updateLabelResolutionBannerWizard() {
	var banner = qs("labelResolutionBanner");
	if (!setup || !setup.annodir || !banner) return;
	var bundleRoot = setup.bundleRoot || "";
	var stale = labelAudit.annotationsNewerThanIntensity(setup.annodir, bundleRoot);
	var html = labelAudit.formatIntensityAuditBanner(labelAuditData, parcelSummary, {
		staleIntensity: stale,
	});
	if (!html) {
		banner.classList.add("d-none");
		return;
	}
	banner.innerHTML = html;
	banner.classList.remove("d-none");
}

function refreshLabelAuditWizard() {
	if (!setup || !setup.annodir) {
		labelAuditData = null;
		updateLabelResolutionBannerWizard();
		return;
	}
	labelAudit.ensureAudit(setup.annodir, "").then(function (payload) {
		labelAuditData = payload && payload.audit ? payload.audit : null;
		updateLabelResolutionBannerWizard();
		var layers = qs("includeLayers");
		if (
			layers &&
			labelAuditData &&
			labelAudit.auditSuggestsIncludeLayers(labelAuditData)
		) {
			if (parcelSummary && parcelCtx.includeLayersAllowed(parcelSummary)) {
				layers.checked = true;
			}
		}
	});
}

function updateParcellationBannerWizard() {
	var banner = qs("intensityParcellationBanner");
	var layers = qs("includeLayers");
	if (!setup || !setup.annodir || !banner) return;
	parcelSummary = parcelCtx.summarizeParcellationForLeaf(setup.annodir);
	if (!parcelSummary.hasParcellation) {
		banner.classList.add("d-none");
		if (layers) layers.disabled = false;
		return;
	}
	var label = parcelCtx.formatParcellationLabel({
		tier_id: parcelSummary.tierId,
		st_level: parcelSummary.stLevel,
	});
	var msg =
		"Align run uses <strong>" +
		label +
		"</strong> parcellation — selections roll up automatically.";
	if (parcelSummary.mixedTiers) {
		msg += " Mixed tiers across slices; Python uses per-slice matching.";
	}
	banner.innerHTML = msg;
	banner.classList.remove("d-none");
	if (layers) {
		var allowed = parcelCtx.includeLayersAllowed(parcelSummary);
		layers.disabled = !allowed;
		if (!allowed) layers.checked = false;
	}
	updateLabelResolutionBannerWizard();
}

function startProcess() {
	if (!selectedIds.length) {
		alert("Select at least one region for output.");
		return;
	}
	if (parcelSummary && qs("includeLayers") && qs("includeLayers").checked) {
		if (!parcelCtx.includeLayersAllowed(parcelSummary)) {
			alert(
				"Include cortical layers is not available when annotations are parcellated above layer resolution.",
			);
			return;
		}
	}
	var bundleRoot = setup.bundleRoot || "";
	var stale = labelAudit.annotationsNewerThanIntensity(setup.annodir, bundleRoot);
	var hasAuditIssues =
		labelAuditData && labelAuditData.summary && labelAuditData.summary.any_issues;
	if (stale || hasAuditIssues) {
		var msg =
			labelAudit.formatIntensityAuditBanner(labelAuditData, parcelSummary, {
				staleIntensity: stale,
			}) || "Annotation labels may not match your last Isolate Regions run.";
		var proceed = window.confirm(
			"Label resolution warnings:\n\n" +
				msg.replace(/<[^>]+>/g, "") +
				"\n\nContinue with Isolate Regions anyway?",
		);
		if (!proceed) {
			return;
		}
	}
	wizardBusy.setWizardBusy({
		busy: true,
		primarySelector: "#step2Process",
		backSelectors: ["#step2Back"],
		extraSelectors: ["#addRegion", "#removeRegion", "#presetVisRsp"],
		messageEl: "#extractStatus",
		message: "Starting Isolate Regions…",
	});
	var sortedStems = setup.sortedStems || pipelineRuns.listImageSliceStems(setup.indir);
	var includeLayers = qs("includeLayers") && qs("includeLayers").checked;
	var slug = pipelineRuns.buildRunSlug("intensity", {
		sortedStems: sortedStems,
		whole: setup.whole,
		useDapi: setup.useDapi,
		regionCount: selectedIds.length,
		includeLayers: includeLayers,
		subsetCount: setup.subsetCount || 0,
	});
	var outBase =
		project.isActive() && pipelineRuns.resolveRoleBaseAbs("pkls")
			? pipelineRuns.resolveRoleBaseAbs("pkls")
			: setup.outdir;
	var finalOut = pipelineRuns.resolveStepOutputPath("intensity", {
		slug: slug,
		flat: !!setup.flatOutput,
		runMode: setup.runMode || "merge",
		legacyOutBase: setup.outdir || outBase,
	});
	try {
		fs.mkdirSync(finalOut, { recursive: true });
	} catch (err) {
		wizardBusy.setWizardBusy({ busy: false });
		alert("Could not create output directory: " + (err.message || err));
		return;
	}
	lastRunRel = setup.flatOutput
		? ""
		: pipelineRuns.relFromRoleBase("intensity", finalOut);
	var written = writeRunConfig(finalOut);
	lastResult.outputDir = finalOut;

	setStep(3);
	extractRunning = true;
	setProgress(0, "Starting Isolate Regions…");
	appendLog("[IntensityWizard] Output: " + finalOut);
	appendLog(
		"[IntensityWizard] Regions: " +
			selectedIds.length +
			(layersLabel(written.includeLayers)),
	);

	ipc.send("runIntensity", [
		setup.indir,
		finalOut,
		setup.annodir,
		setup.whole,
		setup.dapiPath || "",
		setup.sliceListPath || "",
		written.cfgPath,
	]);
}

function layersLabel(on) {
	return on ? ", layers on" : ", layers off";
}

function showSummary(ok, errMsg) {
	setStep(4);
	var alert = qs("summaryAlert");
	var dl = qs("summaryDetails");
	if (alert) {
		if (ok) {
			alert.className = "alert alert-success text-start";
			alert.textContent =
				"Isolate Regions finished. Wrote " +
				String(lastResult.pkls) +
				" PKL file(s) across " +
				String(lastResult.slices) +
				" slice(s).";
		} else {
			alert.className = "alert alert-danger text-start";
			alert.textContent = errMsg || "Isolate Regions failed. See the log on step 3 or the Application log.";
		}
	}
	if (dl) {
		dl.innerHTML =
			'<dt class="col-sm-3">Output</dt><dd class="col-sm-9"><code>' +
			(lastResult.outputDir || "") +
			"</code></dd>" +
			'<dt class="col-sm-3">Regions</dt><dd class="col-sm-9">' +
			selectedIds.length +
			layersLabel(qs("includeLayers") && qs("includeLayers").checked) +
			"</dd>";
	}
}

function countPklsInOutput(dir) {
	try {
		var names = fs.readdirSync(dir);
		var n = 0;
		for (var i = 0; i < names.length; i++) {
			if (/\.pkl$/i.test(names[i]) && names[i] !== "run_manifest.json") {
				n++;
			}
		}
		return n;
	} catch (_e) {
		return 0;
	}
}

project.tryRestoreActiveProject();
pipelineGate.assertPipelineAccess();

setup = loadSetup();
if (!setup || !setup.indir || !setup.annodir) {
	alert("Open Isolate Regions setup first (Configure outputs).");
	window.location.href = "./intensity.html";
} else {
	catalog = structureCatalog.loadCatalog(getAppRoot());
	fillTierSelect();
	fillLevelSelect();
	restorePickerMode();

	if (qs("tierSelect")) {
		qs("tierSelect").addEventListener("change", function () {
			availableHighlight = null;
			renderAvailableList();
		});
	}
	if (qs("levelSelect")) {
		qs("levelSelect").addEventListener("change", function () {
			availableHighlight = null;
			renderAvailableList();
		});
	}
	if (qs("ccfAdvancedToggle")) {
		qs("ccfAdvancedToggle").addEventListener("change", function (ev) {
			applyPickerMode(ev.target.checked ? "advanced" : "tiers");
		});
	}
	if (qs("regionSearch")) {
		qs("regionSearch").addEventListener("input", function () {
			renderAvailableList();
		});
	}
	qs("addRegion").addEventListener("click", function () {
		if (availableHighlight != null) {
			addSelectedId(availableHighlight);
		}
	});
	qs("removeRegion").addEventListener("click", function () {
		if (selectedHighlight != null) {
			removeSelectedId(selectedHighlight);
			selectedHighlight = null;
		}
	});
	qs("presetVisRsp").addEventListener("click", function () {
		selectedIds = structureCatalog.presetVisRspIds(catalog).slice();
		renderAvailableList();
		renderSelectedList();
	});
	updateParcellationBannerWizard();
	refreshLabelAuditWizard();
	qs("step2Process").addEventListener("click", startProcess);
	qs("cancelExtract").addEventListener("click", function () {
		if (extractRunning) {
			ipc.send("killIntensity", []);
		}
	});

	ipc.on("updateLoad", function (_event, response) {
		if (wizardStep !== 3) {
			return;
		}
		setProgress(response[0], response[1]);
		appendLog(response[1]);
	});

	ipc.on("intensityResult", function () {
		extractRunning = false;
		wizardBusy.setWizardBusy({ busy: false });
		lastResult.pkls = countPklsInOutput(lastResult.outputDir);
		if (project.isActive() && lastRunRel) {
			pipelineRuns.setActiveRunRel("intensity", lastRunRel);
			project.refreshProjectIndex().catch(function () {});
		}
		try {
			sessionStorage.removeItem(SETUP_KEY);
		} catch (_e) {
			// ignore
		}
		showSummary(true);
	});

	ipc.on("intensityError", function (_event, response) {
		extractRunning = false;
		wizardBusy.setWizardBusy({ busy: false });
		var msg = response && response[0] ? String(response[0]) : "";
		showSummary(false, msg);
	});
}
