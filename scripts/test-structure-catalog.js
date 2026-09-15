"use strict";

var assert = require("assert");
var path = require("path");
var structureCatalog = require("../js/structure_catalog");

var appRoot = path.join(__dirname, "..");

function testFlattenAndLevels() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	assert.ok(catalog.nodes.length > 1000, "expected large flattened catalog");
	var levels = structureCatalog.listLevels(catalog);
	assert.ok(levels.length >= 5, "expected multiple CCF levels");
	var l6 = structureCatalog.listRegionsAtLevel(6, "", catalog);
	assert.ok(l6.length > 10, "level 6 should list areas");
	var visSearch = structureCatalog.listRegionsAtLevel(8, "visp", catalog);
	assert.ok(
		visSearch.some(function (n) {
			return n.acronym === "VISp";
		}),
		"search should find VISp",
	);
}

function testVisSiblingsShareGroupParent() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var visp = catalog.byAcronym["VISp"];
	var visl = catalog.byAcronym["VISl"];
	assert.ok(visp && visl, "VISp and VISl should exist");
	assert.strictEqual(
		visp.groupParentId,
		visl.groupParentId,
		"VIS siblings should share group parent",
	);
	var ssp = catalog.byAcronym["SSp"];
	if (ssp) {
		assert.notStrictEqual(
			visp.groupParentId,
			ssp.groupParentId,
			"VIS and SS families should differ",
		);
	}
}

function testPresetVisRsp() {
	structureCatalog.resetCatalogForTests();
	var ids = structureCatalog.presetVisRspIds(structureCatalog.loadCatalog(appRoot));
	assert.ok(ids.length >= 10, "preset should include legacy VIS/RSP set");
}

function testListTiers() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var tiers = structureCatalog.listTiers(catalog);
	var ids = tiers.map(function (t) {
		return t.id;
	});
	assert.deepStrictEqual(
		ids,
		["major", "regions", "areas", "subareas", "parts", "layers"],
		"tier ids and order must match plan",
	);
	for (var i = 0; i < tiers.length; i++) {
		assert.ok(tiers[i].label, "tier label required");
		assert.ok(tiers[i].description, "tier description required");
		assert.ok(
			Array.isArray(tiers[i].region_ids) && tiers[i].region_ids.length > 0,
			"tier " + tiers[i].id + " must have at least one region id",
		);
	}
}

function testListRegionsForTierAreas() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var areas = structureCatalog.listRegionsForTier("areas", "", catalog);
	var acros = {};
	for (var i = 0; i < areas.length; i++) {
		acros[areas[i].acronym] = true;
	}
	assert.ok(areas.length >= 30 && areas.length <= 40, "areas tier ~34");
	assert.ok(acros["VIS"], "areas tier must include VIS");
	assert.ok(acros["AUD"], "areas tier must include AUD");
	// SS parent (somatosensory areas) lives at st_level 6; SSp/SSs are deeper
	assert.ok(acros["SS"], "areas tier must include SS");
}

function testSubareasIncludeVispSspBfd() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var sub = structureCatalog.listRegionsForTier("subareas", "", catalog);
	var acros = {};
	for (var i = 0; i < sub.length; i++) {
		acros[sub[i].acronym] = true;
	}
	assert.ok(acros["VISp"], "subareas tier must include VISp");
	assert.ok(
		acros["SSp-bfd"] || acros["SSp"],
		"subareas tier must include SSp-bfd (or SSp)",
	);
	// ACB / AAA are common subcortical nuclei at st_level 8
	assert.ok(acros["ACB"] || acros["AAA"], "subareas tier must include subcortical nuclei (ACB/AAA)");
}

function testSubareasExcludeLayers() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var sub = structureCatalog.listRegionsForTier("subareas", "", catalog);
	for (var i = 0; i < sub.length; i++) {
		assert.ok(
			String(sub[i].name || "").toLowerCase().indexOf("layer") < 0,
			"subarea " + sub[i].acronym + " must not be layer-named",
		);
	}
}

function testListCcfLevelsEnriched() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var levels = structureCatalog.listCcfLevels(catalog);
	assert.ok(levels.length >= 5, "expected multiple CCF depths");
	var prev = -1;
	for (var i = 0; i < levels.length; i++) {
		var info = levels[i];
		assert.ok(info.level > prev, "levels must be sorted");
		prev = info.level;
		assert.ok(info.count > 0, "count must be positive");
		assert.ok(typeof info.kind === "string" && info.kind, "kind required");
		assert.ok(Array.isArray(info.sampleAcronyms), "sampleAcronyms array");
		assert.ok(info.sampleAcronyms.length <= 5, "≤ 5 sample acronyms");
		assert.strictEqual(typeof info.hasMore, "boolean", "hasMore is boolean");
	}
	var byLevel = {};
	for (var k = 0; k < levels.length; k++) {
		byLevel[levels[k].level] = levels[k];
	}
	assert.ok(byLevel[6], "level 6 should exist");
	assert.ok(byLevel[6].count >= 30 && byLevel[6].count <= 40, "level 6 ~34 acronyms");
	var label6 = structureCatalog.formatCcfLevelLabel(byLevel[6]);
	assert.ok(/^Level 6 — /.test(label6), "formatCcfLevelLabel must start 'Level 6 — '");
	assert.ok(label6.indexOf(String(byLevel[6].count)) >= 0, "label includes count");
	assert.ok(label6.indexOf(byLevel[6].kind) >= 0, "label includes kind");
}

function testCcfLevel4SingleStructureCtxpl() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var info4 = null;
	var levels = structureCatalog.listCcfLevels(catalog);
	for (var i = 0; i < levels.length; i++) {
		if (levels[i].level === 4) {
			info4 = levels[i];
			break;
		}
	}
	assert.ok(info4, "level 4 should be present");
	var label = structureCatalog.formatCcfLevelLabel(info4);
	assert.ok(label.indexOf("1 single structure") >= 0, "level 4 kind = single structure");
	assert.ok(label.indexOf("CTXpl") >= 0, "level 4 sample = CTXpl");
}

function testCcfAdvancedHelpExport() {
	assert.ok(
		structureCatalog.CCF_ADVANCED_HELP.indexOf("CCFv3") >= 0,
		"CCF_ADVANCED_HELP must mention CCFv3",
	);
	assert.ok(
		structureCatalog.CCF_ADVANCED_HELP.indexOf("st_level") >= 0,
		"CCF_ADVANCED_HELP must mention st_level",
	);
}

function testPartsParentOfLayers() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var parts = structureCatalog.listRegionsForTier("parts", "", catalog);
	var acros = {};
	for (var i = 0; i < parts.length; i++) {
		acros[parts[i].acronym] = true;
	}
	assert.ok(acros["RSPagl"], "parts must include RSPagl");
	assert.ok(acros["RSPd"], "parts must include RSPd");
	assert.ok(acros["RSPv"], "parts must include RSPv");
	assert.ok(acros["VISp"], "parts must include VISp");
	assert.ok(acros["AUDp"] || acros["SSp-bfd"], "parts must include AUD/SS parent-of-layers");
	assert.ok(!acros["RSP"], "parts must not include RSP parent");
	assert.ok(!acros["RSPv2/3"], "parts must not include laminar IDs");
	assert.ok(!acros["VISp4"], "parts must not include laminar IDs");
}

function main() {
	testFlattenAndLevels();
	testVisSiblingsShareGroupParent();
	testPresetVisRsp();
	testListTiers();
	testListRegionsForTierAreas();
	testSubareasExcludeLayers();
	testSubareasIncludeVispSspBfd();
	testPartsParentOfLayers();
	testListCcfLevelsEnriched();
	testCcfLevel4SingleStructureCtxpl();
	testCcfAdvancedHelpExport();
	console.log("test-structure-catalog: ok");
}

main();
