"use strict";

/**
 * Load key renderer pages in a headless Electron window and fail on load alerts.
 * Run: ./node_modules/.bin/electron scripts/smoke-pages.js
 */
const fs = require("fs");
const path = require("path");
const { app, BrowserWindow } = require("electron");
const branding = require("../js/branding");
const project = require("../js/project");
const helpers = require("./test-helpers");

const PAGES = [
	"loading.html",
	"menu.html",
	"project_start.html",
	"project_wizard.html",
	"czi_wizard.html",
	"workspace_menu.html",
	"menu_category.html",
	"dapi_cleanup.html",
	"credits.html",
	"settings.html",
	"settings_network.html",
	"settings_dialogs.html",
	"settings_updates.html",
	"batch_wizard.html",
	"geometry_repair_wizard.html",
	"orient.html",
	"parcellation_wizard.html",
	"tophat_wizard.html",
	"sharpen_wizard.html",
	"basic_wizard.html",
	"tissue_cleanup_wizard.html",
	"align.html",
	"adjust.html",
];

/** page → required element ids (executeJavaScript in renderer). */
const DOM_ASSERTIONS = {
	"czi_wizard.html": [
		"step2",
		"step3",
		"wizard-review-scroll",
		"renamingTableBody",
		"channelTableBody",
		"step3Next",
		"step4Continue",
		"reextractSliceList",
		"reextractChannelList",
		"reextractConfirmTable",
	],
	"project_wizard.html": [
		"step3",
		"wizard-review-scroll",
		"reviewTable",
		"reviewTableBody",
		"reviewConfirm",
	],
	"workspace_menu.html": [
		"projectSubsetSection",
		"subsetEnabled",
		"subsetSliceList",
	],
	"dapi_cleanup.html": [
		"indir",
		"outputInPlace",
		"outputSeparate",
		"isolate",
		"saturation",
		"run",
	],
	"align.html": ["indir", "outdir", "run", "spacing", "whole", "half"],
	"adjust.html": ["imdir", "annodir", "run"],
	"settings_network.html": [
		"ioFairsharePanel",
		"ioFairshareNasList",
		"ioFairsharePickNas",
	],
	"settings_dialogs.html": [
		"dialogPrefsPanel",
		"dialogSuppressedList",
		"dialogClearSuppressBtn",
	],
	"settings_updates.html": [
		"updateStatusPanel",
		"checkAgainBtn",
		"updateNowBtn",
		"allowPrerelease",
		"keepVersionBackups",
		"deleteVersionBackupsBtn",
	],
	"parcellation_wizard.html": [
		"step1",
		"sliceTableBody",
		"tierSelect",
		"step3Start",
	],
	"tophat_wizard.html": [
		"step1",
		"signalBranchSelect",
		"sourceDatasetSelect",
		"tophatRadius",
		"preprocessPreviewViewport",
		"step1Next",
	],
	"sharpen_wizard.html": [
		"step1",
		"signalBranchSelect",
		"sharpenRadius",
		"sharpenEqualize",
		"preprocessPreviewImg",
	],
	"basic_wizard.html": [
		"step0",
		"attrProceed",
		"step1",
		"channelSelect",
		"previewFilterBtn",
		"processStart",
	],
	"tissue_cleanup_wizard.html": [
		"step1",
		"tissueCanvasViewport",
		"attemptAutoBtn",
		"step1Next",
		"confirmTableBody",
	],
};

const repoRoot = path.join(__dirname, "..");

function setupSmokeProjectBundle() {
	var bundle = helpers.tmpDir("mj-smoke-");
	var now = new Date().toISOString();
	var roles = project.CANONICAL_ROLES;
	var roleKeys = Object.keys(roles);
	for (var i = 0; i < roleKeys.length; i++) {
		fs.mkdirSync(path.join(bundle, roles[roleKeys[i]]), { recursive: true });
	}
	fs.mkdirSync(path.join(bundle, branding.META_DIR), { recursive: true });
	var projectData = {
		version: "1.0",
		name: "SmokeTest",
		layout: branding.LAYOUT_ID,
		created: now,
		modified: now,
		roles: Object.assign({}, roles),
		sources: {},
		settings: {},
		pipeline: {},
		reference_only: false,
		alignments: {},
		processing: project.defaultProcessing(),
	};
	var projectFile = path.join(bundle, "M528.masonjar");
	fs.writeFileSync(projectFile, JSON.stringify(projectData, null, 2), "utf8");
	helpers.writeFileIndex(bundle, [
		{
			sliceId: "M528_s061",
			role: "dapi",
			relPath: "data/counting/00_dapi/M528_s061.tif",
			metadata: { width: 512, height: 512 },
		},
		{
			sliceId: "M528_s061",
			role: "max",
			relPath: "data/counting/03_max/M528_s061.tif",
			metadata: { width: 512, height: 512 },
		},
	]);
	return bundle;
}

async function seedActiveProject(win, bundlePath) {
	await win.webContents.executeJavaScript(
		"localStorage.setItem(" +
			JSON.stringify(branding.ACTIVE_KEY) +
			", " +
			JSON.stringify(bundlePath) +
			");",
	);
}

async function loadPage(win, pageName) {
	await new Promise(function (resolve, reject) {
		function onFail(_event, errorCode, errorDescription) {
			win.webContents.removeListener("did-fail-load", onFail);
			reject(
				new Error(
					pageName +
						" did-fail-load " +
						errorCode +
						": " +
						errorDescription,
				),
			);
		}
		win.webContents.once("did-fail-load", onFail);
		win.webContents.once("did-finish-load", function () {
			win.webContents.removeListener("did-fail-load", onFail);
			setTimeout(resolve, 200);
		});
		win.loadFile(path.join(repoRoot, "pages", pageName)).catch(reject);
	});
}

async function assertDomIds(win, pageName, ids) {
	var missing = await win.webContents.executeJavaScript(
		"(function () {\n" +
			"  var ids = " +
			JSON.stringify(ids) +
			";\n" +
			"  return ids.filter(function (id) { return !document.getElementById(id); });\n" +
			"})()",
	);
	if (missing && missing.length) {
		throw new Error(
			pageName +
				" missing required DOM ids: " +
				missing.join(", "),
		);
	}
}

async function assertCziWizardStepVisibility(win) {
	var err = await win.webContents.executeJavaScript(
		"(function () {\n" +
			"  var panels = document.querySelectorAll('.wizard-panel');\n" +
			"  for (var i = 0; i < panels.length; i++) {\n" +
			"    panels[i].classList.add('d-none');\n" +
			"    panels[i].setAttribute('hidden', '');\n" +
			"  }\n" +
			"  var step3 = document.getElementById('step3');\n" +
			"  if (step3) {\n" +
			"    step3.classList.remove('d-none');\n" +
			"    step3.removeAttribute('hidden');\n" +
			"  }\n" +
			"  var s2 = document.getElementById('step2');\n" +
			"  var s3 = document.getElementById('step3');\n" +
			"  if (!s2 || !s3) return 'missing step2 or step3';\n" +
			"  var d2 = window.getComputedStyle(s2).display;\n" +
			"  var d3 = window.getComputedStyle(s3).display;\n" +
			"  if (d2 !== 'none') return 'step2 display=' + d2;\n" +
			"  if (d3 === 'none') return 'step3 display=none';\n" +
			"  return '';\n" +
			"})()",
	);
	if (err) {
		throw new Error("czi_wizard.html step visibility: " + err);
	}
}

app.whenReady().then(async function () {
	var win = new BrowserWindow({
		show: false,
		webPreferences: { nodeIntegration: true, contextIsolation: false },
	});
	var smokeBundle = null;

	try {
		for (var i = 0; i < PAGES.length; i++) {
			var pageName = PAGES[i];
			if (pageName === "workspace_menu.html") {
				smokeBundle = setupSmokeProjectBundle();
				await seedActiveProject(win, smokeBundle);
			}
			await loadPage(win, pageName);
			var domIds = DOM_ASSERTIONS[pageName];
			if (domIds) {
				await assertDomIds(win, pageName, domIds);
			}
			if (pageName === "czi_wizard.html") {
				await assertCziWizardStepVisibility(win);
			}
			console.log("OK:", pageName);
		}
	} finally {
		if (smokeBundle) {
			helpers.rmDir(smokeBundle);
		}
		win.destroy();
		app.quit();
	}
});

app.on("window-all-closed", function () {
	app.quit();
});

process.on("unhandledRejection", function (err) {
	console.error(err);
	app.exit(1);
});
