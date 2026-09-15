"use strict";

var assert = require("assert");
var path = require("path");
var structureCatalog = require("../js/structure_catalog");
var atlasStyle = require("../js/atlas_region_style");

var appRoot = path.join(__dirname, "..");

function testStableHexAcrossLoads() {
	structureCatalog.resetCatalogForTests();
	var catalog = structureCatalog.loadCatalog(appRoot);
	var visp = catalog.byAcronym["VISp"];
	assert.ok(visp, "VISp required");
	var hex1 = atlasStyle.colorHexForGroup(
		atlasStyle.groupParentForRegion(visp, catalog.byId),
	);
	structureCatalog.resetCatalogForTests();
	var catalog2 = structureCatalog.loadCatalog(appRoot);
	var visp2 = catalog2.byAcronym["VISp"];
	var hex2 = atlasStyle.colorHexForGroup(
		atlasStyle.groupParentForRegion(visp2, catalog2.byId),
	);
	assert.strictEqual(hex1, hex2, "group color hex should be stable across reloads");
}

function testContrastBorderForLightColor() {
	var style = atlasStyle.rowStyleForRegion(
		{ id: 1, idPath: [997, 1], st_level: 6 },
		{
			1: {
				id: 1,
				st_level: 6,
				colorHex: "#FFFFFF",
				acronym: "X",
				name: "Light",
			},
		},
	);
	assert.ok(style.borderLeftColor.indexOf("#") === 0);
	assert.notStrictEqual(style.borderLeftColor, "#FFFFFF");
}

function main() {
	testStableHexAcrossLoads();
	testContrastBorderForLightColor();
	console.log("test-atlas-region-style: ok");
}

main();
