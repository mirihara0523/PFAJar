"use strict";

var fs = require("fs");
var path = require("path");
var workspace = require("./workspace");
var project = require("./project");
var branding = require("./branding");

var wizardState = {
	mode: "new",
	step: 1,
	parentDir: "",
	bundleRoot: "",
	projectFilename: "",
	sources: {},
};

function updateBundlePathPreview() {
	var parentDir = wizardState.parentDir;
	var nameEl = qs("projectName");
	var pathEl = qs("bundlePath");
	if (!pathEl) {
		return;
	}
	if (!parentDir || !nameEl || !nameEl.value.trim()) {
		pathEl.value = "";
		wizardState.bundleRoot = "";
		wizardState.projectFilename = "";
		return;
	}
	var resolved = project.resolveNewBundlePath(parentDir, nameEl.value);
	pathEl.value = resolved.bundleRoot;
	wizardState.bundleRoot = resolved.bundleRoot;
	wizardState.projectFilename = resolved.projectFilename;
}

var ROLE_LABELS = [
	{ role: "original_scans", label: "Original scans", logical: "originalScans" },
	{ role: "dapi", label: "00 DAPI", logical: "dapi" },
	{ role: "slices", label: "01 Slices", logical: "slices" },
	{ role: "max", label: "03 Max", logical: "max" },
	{ role: "predictions", label: "05 Predictions", logical: "predictions" },
	{ role: "quantification", label: "06 Quantification", logical: "quantification" },
	{ role: "pkls", label: "07 PKLs", logical: "pkls" },
	{ role: "dual", label: "08 Dual", logical: "dual" },
];

function qs(id) {
	return document.getElementById(id);
}

function getQueryMode() {
	var params = new URLSearchParams(window.location.search);
	return params.get("mode") || "new";
}

function setStep(step) {
	wizardState.step = step;
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
		} else if (pillStep < step) {
			pills[p].classList.add("active");
		} else {
			pills[p].classList.add("disabled");
		}
	}
}

function buildSourceFields() {
	var container = qs("sourceFields");
	if (!container) {
		return;
	}
	container.innerHTML = "";
	for (var i = 0; i < ROLE_LABELS.length; i++) {
		var def = ROLE_LABELS[i];
		var col = document.createElement("div");
		col.className = "col-md-6";
		col.innerHTML =
			'<label class="form-label">' +
			def.label +
			"</label>" +
			'<input type="text" class="form-control source-input" data-role="' +
			def.role +
			'" data-logical="' +
			def.logical +
			'" readonly />';
		container.appendChild(col);
		var input = col.querySelector("input");
		input.addEventListener("click", function (ev) {
			pickSourceForInput(ev.target);
		});
		if (wizardState.sources[def.role]) {
			input.value = wizardState.sources[def.role];
		}
	}
}

function pickSourceForInput(inputEl) {
	var ipc = require("electron").ipcRenderer;
	var tag = "wizardSource_" + inputEl.getAttribute("data-role");
	ipc.once("returnPath", function (event, response) {
		var responseTag = response[1];
		if (typeof responseTag === "object" && responseTag !== null && responseTag.tag) {
			responseTag = responseTag.tag;
		}
		if (responseTag === tag) {
			inputEl.value = response[0] || "";
			wizardState.sources[inputEl.getAttribute("data-role")] = inputEl.value;
		}
	});
	ipc.send("openDialog", {
		tag: tag,
		defaultPath: inputEl.value || undefined,
	});
}

function fillSourcesFromWorkspace() {
	workspace.loadWorkspace();
	var ws = workspace.getWorkspace();
	wizardState.sources = {};
	for (var i = 0; i < ROLE_LABELS.length; i++) {
		var def = ROLE_LABELS[i];
		var resolved = workspace.resolveLogicalPath(def.logical);
		if (resolved) {
			wizardState.sources[def.role] = resolved;
		}
	}
	if (ws.brainRoot) {
		wizardState.legacyRoot = ws.brainRoot;
	}
	buildSourceFields();
}

function getImportMode() {
	var selected = document.querySelector('input[name="importMode"]:checked');
	return selected ? selected.value : "copy";
}

function updateImportWarning() {
	var warn = qs("importWarning");
	if (!warn) {
		return;
	}
	var mode = getImportMode();
	if (mode === "symlink") {
		warn.textContent =
			"Symlinks point at your original folders. Moving or deleting those folders will break this project. On Windows, symlinks may require Developer Mode or administrator rights.";
		warn.classList.remove("d-none");
	} else if (mode === "reference") {
		warn.textContent =
			"Reference-only mode stores absolute paths in project.masonjar. The bundle layout is created but files are not copied.";
		warn.classList.remove("d-none");
	} else {
		warn.classList.add("d-none");
	}
}

var MODE_LABELS = { copy: "Copy", symlink: "Symlink", reference: "Reference" };

function yieldToUi() {
	return new Promise(function (resolve) {
		requestAnimationFrame(function () {
			setTimeout(resolve, 0);
		});
	});
}

function verboseLog(msg) {
	console.log("[ProjectWizard]", msg);
	var el = qs("wizardLog");
	if (el) {
		el.textContent = (el.textContent ? el.textContent + "\n" : "") + msg;
		el.scrollTop = el.scrollHeight;
	}
}

function setActivity(msg, pct) {
	var bar = qs("wizardProgress");
	var status = qs("finishStatus");
	if (status && msg) {
		status.textContent = msg;
	}
	if (bar && typeof pct === "number") {
		bar.style.width = String(pct) + "%";
		bar.setAttribute("aria-valuenow", String(pct));
		if (pct >= 100) {
			bar.classList.remove("progress-bar-striped", "progress-bar-animated");
		} else {
			bar.classList.add("progress-bar-striped", "progress-bar-animated");
		}
	}
}

function setBuildNavDisabled(disabled) {
	var back = qs("step4Back");
	var build = qs("step4Next");
	if (back) {
		back.disabled = disabled;
	}
	if (build) {
		build.disabled = disabled;
	}
}

function importProgressPct(roleIndex, roleCount, fileIndex, fileTotal) {
	var roleSpan = 65 / (roleCount || 1);
	var roleBase = 10 + roleIndex * roleSpan;
	if (!fileTotal) {
		return Math.round(roleBase + roleSpan);
	}
	return Math.round(roleBase + (fileIndex / fileTotal) * roleSpan);
}

function roleCheckmark(bySlice, sid, role) {
	return bySlice[sid] && bySlice[sid].roles[role] ? "✓" : "—";
}

function renderReviewStep(report) {
	var tbody = qs("reviewTableBody");
	var status = qs("reviewStatus");
	var orphansEl = qs("reviewOrphans");
	if (!tbody) {
		return;
	}
	tbody.innerHTML = "";
	var matched = report.matchedSliceIds || [];
	var issuesBySlice = {};
	var q = report.qualityIssues || [];
	for (var i = 0; i < q.length; i++) {
		var iss = q[i];
		if (!issuesBySlice[iss.sliceId]) {
			issuesBySlice[iss.sliceId] = [];
		}
		issuesBySlice[iss.sliceId].push(iss.message);
	}
	for (var m = 0; m < matched.length; m++) {
		var sid = matched[m];
		var tr = document.createElement("tr");
		var quality = (issuesBySlice[sid] || []).join(" ") || "OK";
		tr.innerHTML =
			"<td>" +
			sid +
			"</td><td>" +
			roleCheckmark(report.bySlice, sid, "dapi") +
			"</td><td>" +
			roleCheckmark(report.bySlice, sid, "max") +
			"</td><td>" +
			roleCheckmark(report.bySlice, sid, "slices") +
			'</td><td class="small text-warning">' +
			quality +
			"</td>";
		tbody.appendChild(tr);
	}
	if (status) {
		status.textContent =
			matched.length +
			" matched slice(s); " +
			(report.qualityIssues || []).length +
			" quality note(s).";
	}
	if (orphansEl && report.orphansByRole) {
		var parts = [];
		var roles = Object.keys(report.orphansByRole);
		for (var r = 0; r < roles.length; r++) {
			var orphans = report.orphansByRole[roles[r]];
			if (orphans && orphans.length) {
				parts.push(roles[r] + ": " + orphans.length + " unmatched");
			}
		}
		orphansEl.textContent = parts.length
			? "Unmatched by role — " + parts.join("; ")
			: "";
	}
}

async function loadReviewStep() {
	var status = qs("reviewStatus");
	var confirm = qs("reviewConfirm");
	var nextBtn = qs("step3Next");
	if (confirm) {
		confirm.checked = false;
	}
	if (nextBtn) {
		nextBtn.disabled = true;
	}
	if (status) {
		status.textContent = "Scanning sources for matches…";
	}
	await yieldToUi();
	try {
		var index = await project.buildPreviewIndexFromSources(wizardState.sources, {
			appRoot: path.join(__dirname, ".."),
		});
		var report = project.computeMatchReport(index, ["dapi", "max", "slices"]);
		wizardState.previewReport = report;
		renderReviewStep(report);
		var fs = require("fs");
		var predSrc = wizardState.sources.predictions;
		if (predSrc && fs.existsSync(predSrc)) {
			var fileIndex = require("./file_index");
			var scan = fileIndex.resolvePredictionsScan(predSrc, 2);
			if (scan.warning && status) {
				status.textContent = (status.textContent || "") + " " + scan.warning;
			}
		}
	} catch (err) {
		if (status) {
			status.textContent = "Review scan failed: " + (err.message || err);
		}
	}
}

async function runBuildAsync() {
	setBuildNavDisabled(true);
	setStep(5);
	var logEl = qs("wizardLog");
	if (logEl) {
		logEl.textContent = "";
	}
	var openMenu = qs("openMenu");
	var openOrient = qs("openOrient");
	if (openMenu) {
		openMenu.classList.add("d-none");
	}
	if (openOrient) {
		openOrient.classList.add("d-none");
	}

	try {
		await yieldToUi();
		setActivity("Creating project bundle…", 0);
		verboseLog("Starting build…");

		var bundleRoot = wizardState.bundleRoot;
		var name =
			(qs("projectName") && qs("projectName").value) ||
			path.basename(bundleRoot);
		var mode = getImportMode();
		var modeLabel = MODE_LABELS[mode] || mode;
		var referenceOnly = mode === "reference";
		var roles = Object.assign({}, project.CANONICAL_ROLES);

		if (referenceOnly) {
			var absRoles = Object.assign({}, project.CANONICAL_ROLES);
			var roleKeys = Object.keys(wizardState.sources);
			for (var r = 0; r < roleKeys.length; r++) {
				var rk = roleKeys[r];
				if (wizardState.sources[rk]) {
					absRoles[rk] = wizardState.sources[rk];
				}
			}
			roles = absRoles;
		}

		verboseLog("Creating bundle at " + bundleRoot);
		project.createProject({
			bundleRoot: bundleRoot,
			name: name,
			projectFilename: wizardState.projectFilename,
			referenceOnly: referenceOnly,
			roles: roles,
			sources: Object.assign({}, wizardState.sources),
		});
		setActivity("Project bundle created", 10);
		verboseLog(
			"Wrote " + (wizardState.projectFilename || branding.PROJECT_FILENAME),
		);
		await yieldToUi();

		var entries = [];
		var importRoles = Object.keys(wizardState.sources).filter(function (role) {
			return wizardState.sources[role];
		});
		var roleCount = importRoles.length || 1;

		if (mode !== "reference") {
			verboseLog("Import mode: " + modeLabel);
			for (var i = 0; i < importRoles.length; i++) {
				var role = importRoles[i];
				var src = wizardState.sources[role];
				var lastFileLog = 0;
				setActivity(modeLabel + " " + role + "…", importProgressPct(i, roleCount, 0, 0));
				verboseLog("Import " + role + " from " + src);
				await yieldToUi();

				var entry = project.importSourceToRoleWithLayout(src, role, mode, bundleRoot, roles, {
					yieldFn: yieldToUi,
					yieldEvery: 10,
					onProgress: function (ev) {
						var pct = importProgressPct(i, roleCount, ev.index, ev.total);
						var activity =
							modeLabel +
							" " +
							role +
							" (" +
							ev.index +
							"/" +
							ev.total +
							" files)…";
						setActivity(activity, pct);
						if (
							ev.type === "file" &&
							(ev.index === 1 ||
								ev.index === ev.total ||
								ev.index - lastFileLog >= 25)
						) {
							lastFileLog = ev.index;
							verboseLog(role + ": file " + ev.index + "/" + ev.total);
						}
					},
				});
				if (entry && typeof entry.then === "function") {
					entry = await entry;
				}
				entries.push(entry);
				var statusLine = role + ": " + (entry.error || "ok");
				verboseLog(statusLine);
				setActivity(
					modeLabel + " " + role + " complete",
					importProgressPct(i + 1, roleCount, 0, 0),
				);
				await yieldToUi();
			}
		} else {
			verboseLog("Reference-only: skipping file import");
			await yieldToUi();
		}

		project.writeImportLog(bundleRoot, mode, entries);
		stateSaveRoles(bundleRoot, roles, referenceOnly);
		verboseLog("Import log written");
		await yieldToUi();

		project.ensureBundleLayout(bundleRoot);
		setActivity("Building file index…", 75);
		verboseLog("Scanning roles and outputs…");
		await yieldToUi();
		await project.refreshProjectIndex(bundleRoot, {
			onProgress: async function (pct, msg) {
				setActivity(msg, 75 + Math.round(pct * 0.2));
				verboseLog(msg);
				await yieldToUi();
			},
		});

		setActivity("Project ready: " + name, 100);
		verboseLog("Done — opening project");
		if (openMenu) {
			openMenu.classList.remove("d-none");
		}
		if (openOrient) {
			openOrient.classList.remove("d-none");
		}
		project.openProject(bundleRoot);
	} catch (err) {
		var errMsg = err && (err.message || String(err));
		setActivity("Build failed: " + errMsg, 0);
		verboseLog("ERROR: " + errMsg);
		if (err && err.stack) {
			verboseLog(err.stack);
			console.error("[ProjectWizard]", err);
		}
		setBuildNavDisabled(false);
	}
}

function stateSaveRoles(bundleRoot, roles, referenceOnly) {
	var data = project.getProject();
	if (data) {
		data.roles = roles;
		data.reference_only = referenceOnly;
		data.sources = Object.assign({}, wizardState.sources);
		project.saveProjectJson();
	}
}

function init() {
	wizardState.mode = getQueryMode();
	var intro = qs("wizardIntro");
	if (wizardState.mode === "migrate") {
		if (intro) {
			intro.textContent =
				"Migrate a legacy brain folder into a new .masonjar bundle (non-destructive). Legacy .belljar projects still supported.";
		}
		fillSourcesFromWorkspace();
	} else if (intro) {
		intro.textContent =
			"Create a new .masonjar project bundle. Legacy .belljar projects still supported.";
	}

	buildSourceFields();
	setStep(1);

	qs("chooseBundle").addEventListener("click", function () {
		project.chooseNewBundleLocation(function (selected) {
			if (selected) {
				wizardState.parentDir = selected;
				var parentEl = qs("parentDir");
				if (parentEl) {
					parentEl.value = selected;
				}
				updateBundlePathPreview();
			}
		});
	});

	var projectNameEl = qs("projectName");
	if (projectNameEl) {
		projectNameEl.addEventListener("input", updateBundlePathPreview);
	}

	qs("step1Next").addEventListener("click", function () {
		var name = projectNameEl ? projectNameEl.value.trim() : "";
		if (!name) {
			alert("Enter a project name.");
			return;
		}
		if (!wizardState.parentDir) {
			alert("Choose a parent folder for the project.");
			return;
		}
		var resolved = project.resolveNewBundlePath(wizardState.parentDir, name);
		wizardState.bundleRoot = resolved.bundleRoot;
		wizardState.projectFilename = resolved.projectFilename;
		if (fs.existsSync(resolved.bundleRoot) && project.isBundleRoot(resolved.bundleRoot)) {
			alert("A project already exists at:\n" + resolved.bundleRoot);
			return;
		}
		try {
			fs.mkdirSync(resolved.bundleRoot, { recursive: true });
			project.ensureBundleLayout(resolved.bundleRoot);
			qs("bundlePath").value = resolved.bundleRoot;
		} catch (layoutErr) {
			alert(String(layoutErr.message || layoutErr));
			return;
		}
		setStep(2);
	});

	qs("scanLegacy").addEventListener("click", function () {
		workspace.chooseBrainFolder(function () {
			fillSourcesFromWorkspace();
		});
	});

	qs("step2Back").addEventListener("click", function () {
		setStep(1);
	});
	qs("step2Next").addEventListener("click", function () {
		var inputs = document.querySelectorAll(".source-input");
		wizardState.sources = {};
		for (var i = 0; i < inputs.length; i++) {
			var role = inputs[i].getAttribute("data-role");
			if (inputs[i].value) {
				wizardState.sources[role] = inputs[i].value;
			}
		}
		setStep(3);
		loadReviewStep();
	});

	qs("step3Back").addEventListener("click", function () {
		setStep(2);
	});
	qs("step3Next").addEventListener("click", function () {
		setStep(4);
		updateImportWarning();
	});

	var reviewConfirm = qs("reviewConfirm");
	if (reviewConfirm) {
		reviewConfirm.addEventListener("change", function () {
			var nextBtn = qs("step3Next");
			if (nextBtn) {
				nextBtn.disabled = !reviewConfirm.checked;
			}
		});
	}

	qs("step4Back").addEventListener("click", function () {
		setStep(3);
	});
	qs("step4Next").addEventListener("click", function () {
		runBuildAsync();
	});

	document.querySelectorAll('input[name="importMode"]').forEach(function (el) {
		el.addEventListener("change", updateImportWarning);
	});
}

init();
