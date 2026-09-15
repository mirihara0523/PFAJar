"use strict";

var fs = require("fs");
var path = require("path");
var atlasStyle = require("./atlas_region_style");

var VIS_RSP_PRESET_ACRONYMS = [
	"VISa",
	"VISal",
	"VISam",
	"VISp",
	"VISl",
	"VISli",
	"VISpl",
	"VISpm",
	"VISpor",
	"VISrl",
	"RSPagl",
	"RSPd",
	"RSPv",
];

// Semantic tier order shown in the default Hierarchy dropdown. Rules are
// applied data-driven (st_level + layer-name heuristic / graph parent-of-layers)
// so a future CCF update keeps working without hardcoded acronym lists.
var TIER_DEFS = [
	{
		id: "major",
		label: "Major divisions",
		description: "Cerebrum, brain stem, cerebellum",
	},
	{
		id: "regions",
		label: "Classic regions",
		description: "Isocortex, thalamus, hypothalamus, midbrain, …",
	},
	{
		id: "areas",
		label: "Functional areas",
		description: "Sensory, motor, association (VIS, AUD, SSp, MO, …)",
	},
	{
		id: "subareas",
		label: "Sub-areas",
		description: "VISp, VISal, SSp-bfd, ACAd, RSP, individual nuclei",
	},
	{
		id: "parts",
		label: "Area parts",
		description:
			"Named subdivisions without cortical layers (VISp, RSPagl, AUDp, SSp-bfd, …)",
	},
	{
		id: "layers",
		label: "Cortical layers",
		description: "VISp1, VISp2/3, ACA6a, …",
	},
];

var PARTS_TIER = "parts";

var CCF_ADVANCED_HELP =
	"Allen Institute CCFv3 ontology depths (st_level 0–11). Some depths group " +
	"structures that are not anatomically meaningful (e.g. Level 4 contains " +
	"only Cortical plate). Use the standard tiers above for everyday region " +
	"picking.";

var _catalog = null;

function graphPath(appRoot) {
	return path.join(appRoot || path.join(__dirname, ".."), "csv", "structure_graph.json");
}

function flattenGraph(graph, idPath, nodes, byId, byAcronym) {
	var currentPath = idPath.concat([graph.id]);
	var node = {
		id: graph.id,
		acronym: graph.acronym,
		name: graph.name,
		st_level: graph.st_level,
		colorHex: atlasStyle.normalizeHex(graph.color_hex_triplet),
		idPath: currentPath,
		id_path: currentPath.join("/"),
		groupParentId: graph.id,
		groupParentAcronym: graph.acronym,
		groupParentName: graph.name,
		color_hex_triplet: graph.color_hex_triplet,
	};
	nodes.push(node);
	byId[graph.id] = node;
	if (graph.acronym && !byAcronym[graph.acronym]) {
		byAcronym[graph.acronym] = node;
	}
	if (graph.children && graph.children.length) {
		for (var i = 0; i < graph.children.length; i++) {
			flattenGraph(graph.children[i], currentPath, nodes, byId, byAcronym);
		}
	}
}

function loadCatalog(appRoot) {
	if (_catalog) {
		return _catalog;
	}
	var raw = fs.readFileSync(graphPath(appRoot), "utf8");
	var root = JSON.parse(raw);
	var nodes = [];
	var byId = {};
	var byAcronym = {};
	flattenGraph(root, [], nodes, byId, byAcronym);
	// Second pass: group parents need full ancestor chain in byId
	for (var i = 0; i < nodes.length; i++) {
		var n = nodes[i];
		var groupNode = atlasStyle.groupParentForRegion(n, byId);
		n.groupParentId = groupNode ? groupNode.id : n.id;
		n.groupParentAcronym = groupNode ? groupNode.acronym : n.acronym;
		n.groupParentName = groupNode ? groupNode.name : n.name;
	}
	var levels = {};
	for (var k = 0; k < nodes.length; k++) {
		var lvl = nodes[k].st_level;
		if (!levels[lvl]) {
			levels[lvl] = nodes[k];
		}
	}
	_catalog = { nodes: nodes, byId: byId, byAcronym: byAcronym, levels: levels };
	return _catalog;
}

function resetCatalogForTests() {
	_catalog = null;
}

function isLayerName(node) {
	return String((node && node.name) || "").toLowerCase().indexOf("layer") >= 0;
}

function isLayerNode(node) {
	return !!(node && (node.st_level === 11 || isLayerName(node)));
}

function childrenByParent(catalog) {
	catalog = catalog || loadCatalog();
	if (catalog.childrenByParent) {
		return catalog.childrenByParent;
	}
	var byParent = {};
	for (var i = 0; i < catalog.nodes.length; i++) {
		var n = catalog.nodes[i];
		var path = n.idPath || [];
		if (path.length < 2) {
			continue;
		}
		var parentId = path[path.length - 2];
		if (!byParent[parentId]) {
			byParent[parentId] = [];
		}
		byParent[parentId].push(n);
	}
	catalog.childrenByParent = byParent;
	return byParent;
}

function hasDirectLayerChild(nodeId, catalog) {
	var kids = childrenByParent(catalog)[nodeId] || [];
	for (var i = 0; i < kids.length; i++) {
		if (isLayerNode(kids[i])) {
			return true;
		}
	}
	return false;
}

function tierRegionIds(tierId, catalog) {
	catalog = catalog || loadCatalog();
	var nodes = catalog.nodes;
	var i;
	var out = [];
	if (tierId === "major") {
		for (i = 0; i < nodes.length; i++) {
			if (nodes[i].st_level === 2) {
				out.push(nodes[i].id);
			}
		}
		return out;
	}
	if (tierId === "regions") {
		for (i = 0; i < nodes.length; i++) {
			if (nodes[i].st_level === 5) {
				out.push(nodes[i].id);
			}
		}
		return out;
	}
	if (tierId === "areas") {
		for (i = 0; i < nodes.length; i++) {
			if (nodes[i].st_level === 6) {
				out.push(nodes[i].id);
			}
		}
		return out;
	}
	if (tierId === "subareas") {
		for (i = 0; i < nodes.length; i++) {
			if (nodes[i].st_level === 8 && !isLayerName(nodes[i])) {
				out.push(nodes[i].id);
			}
		}
		return out;
	}
	if (tierId === PARTS_TIER || tierId === "parts") {
		for (i = 0; i < nodes.length; i++) {
			if (
				!isLayerNode(nodes[i]) &&
				hasDirectLayerChild(nodes[i].id, catalog)
			) {
				out.push(nodes[i].id);
			}
		}
		return out;
	}
	if (tierId === "layers") {
		for (i = 0; i < nodes.length; i++) {
			if (nodes[i].st_level === 11 || isLayerName(nodes[i])) {
				out.push(nodes[i].id);
			}
		}
		return out;
	}
	return out;
}

function uniqueSortedNumbers(arr) {
	var seen = {};
	var out = [];
	for (var i = 0; i < arr.length; i++) {
		var v = arr[i];
		if (!seen[v]) {
			seen[v] = true;
			out.push(v);
		}
	}
	out.sort(function (a, b) {
		return a - b;
	});
	return out;
}

function listTiers(catalog) {
	catalog = catalog || loadCatalog();
	var out = [];
	for (var i = 0; i < TIER_DEFS.length; i++) {
		var tier = TIER_DEFS[i];
		var ids = uniqueSortedNumbers(tierRegionIds(tier.id, catalog));
		out.push({
			id: tier.id,
			label: tier.label,
			description: tier.description,
			region_ids: ids,
		});
	}
	return out;
}

function listRegionsForTier(tierId, searchQuery, catalog) {
	catalog = catalog || loadCatalog();
	var idsArr = tierRegionIds(tierId, catalog);
	var ids = {};
	for (var k = 0; k < idsArr.length; k++) {
		ids[idsArr[k]] = true;
	}
	var q = (searchQuery || "").trim().toLowerCase();
	var out = [];
	for (var i = 0; i < catalog.nodes.length; i++) {
		var n = catalog.nodes[i];
		if (!ids[n.id]) {
			continue;
		}
		if (q) {
			var hay =
				(n.acronym + " " + n.name + " " + n.groupParentAcronym).toLowerCase();
			if (hay.indexOf(q) < 0) {
				continue;
			}
		}
		out.push(n);
	}
	out.sort(function (a, b) {
		return a.acronym.localeCompare(b.acronym);
	});
	return out;
}

function levelKind(level, count, layerShare) {
	if (layerShare >= 0.25) {
		return "layers";
	}
	if (count === 1) {
		return "single structure";
	}
	if (level <= 3) {
		return "major divisions";
	}
	if (count <= 20) {
		return "divisions";
	}
	return "regions";
}

function ccfLevelInfo(level, catalog, maxSamples) {
	catalog = catalog || loadCatalog();
	if (typeof maxSamples !== "number") {
		maxSamples = 5;
	}
	var seen = {};
	var acronyms = [];
	var layerCount = 0;
	for (var i = 0; i < catalog.nodes.length; i++) {
		var n = catalog.nodes[i];
		if (n.st_level !== level) {
			continue;
		}
		if (!seen[n.acronym]) {
			seen[n.acronym] = true;
			acronyms.push(n.acronym);
		}
		if (isLayerName(n)) {
			layerCount++;
		}
	}
	acronyms.sort(function (a, b) {
		return a.localeCompare(b);
	});
	var count = acronyms.length;
	var share = count ? layerCount / count : 0;
	var samples = acronyms.slice(0, maxSamples);
	return {
		level: level,
		count: count,
		kind: levelKind(level, count, share),
		sampleAcronyms: samples,
		hasMore: count > samples.length,
	};
}

function listCcfLevels(catalog) {
	catalog = catalog || loadCatalog();
	var levels = {};
	for (var i = 0; i < catalog.nodes.length; i++) {
		levels[catalog.nodes[i].st_level] = true;
	}
	var ordered = Object.keys(levels)
		.map(Number)
		.sort(function (a, b) {
			return a - b;
		});
	var out = [];
	for (var k = 0; k < ordered.length; k++) {
		out.push(ccfLevelInfo(ordered[k], catalog));
	}
	return out;
}

function formatCcfLevelLabel(info) {
	var samples = (info && info.sampleAcronyms) || [];
	var suffix = "";
	if (samples.length) {
		var joined = samples.join(", ");
		if (info.hasMore) {
			joined += ", …";
		}
		suffix = " (" + joined + ")";
	}
	return "Level " + info.level + " — " + info.count + " " + info.kind + suffix;
}

function listLevels(catalog) {
	catalog = catalog || loadCatalog();
	var seen = {};
	var out = [];
	for (var i = 0; i < catalog.nodes.length; i++) {
		var lvl = catalog.nodes[i].st_level;
		if (seen[lvl]) {
			continue;
		}
		seen[lvl] = true;
		var example = catalog.levels[lvl];
		out.push({
			level: lvl,
			exampleAcronym: example.acronym,
			exampleName: example.name,
		});
	}
	out.sort(function (a, b) {
		return a.level - b.level;
	});
	return out;
}

function listRegionsAtLevel(level, searchQuery, catalog) {
	catalog = catalog || loadCatalog();
	var q = (searchQuery || "").trim().toLowerCase();
	var out = [];
	for (var i = 0; i < catalog.nodes.length; i++) {
		var n = catalog.nodes[i];
		if (n.st_level !== level) {
			continue;
		}
		if (q) {
			var hay =
				(n.acronym + " " + n.name + " " + n.groupParentAcronym).toLowerCase();
			if (hay.indexOf(q) < 0) {
				continue;
			}
		}
		out.push(n);
	}
	out.sort(function (a, b) {
		return a.acronym.localeCompare(b.acronym);
	});
	return out;
}

function isLayerStructure(node) {
	if (!node) {
		return false;
	}
	return String(node.name || "").toLowerCase().indexOf("layer") >= 0;
}

function presetVisRspIds(catalog) {
	catalog = catalog || loadCatalog();
	var ids = [];
	for (var i = 0; i < VIS_RSP_PRESET_ACRONYMS.length; i++) {
		var ac = VIS_RSP_PRESET_ACRONYMS[i];
		var node = catalog.byAcronym[ac];
		if (node) {
			ids.push(node.id);
		}
	}
	return ids;
}

function groupParentFor(id, catalog) {
	catalog = catalog || loadCatalog();
	var node = catalog.byId[id];
	if (!node) {
		return null;
	}
	return catalog.byId[node.groupParentId] || null;
}

function colorForRegion(id, catalog) {
	catalog = catalog || loadCatalog();
	var group = groupParentFor(id, catalog);
	if (group) {
		return atlasStyle.colorHexForGroup(group);
	}
	var node = catalog.byId[id];
	return node ? atlasStyle.colorHexForGroup(node) : "#808080";
}

function getRegion(id, catalog) {
	catalog = catalog || loadCatalog();
	return catalog.byId[id] || null;
}

module.exports = {
	loadCatalog: loadCatalog,
	resetCatalogForTests: resetCatalogForTests,
	listLevels: listLevels,
	listCcfLevels: listCcfLevels,
	formatCcfLevelLabel: formatCcfLevelLabel,
	listTiers: listTiers,
	listRegionsForTier: listRegionsForTier,
	listRegionsAtLevel: listRegionsAtLevel,
	isLayerStructure: isLayerStructure,
	presetVisRspIds: presetVisRspIds,
	groupParentFor: groupParentFor,
	colorForRegion: colorForRegion,
	getRegion: getRegion,
	VIS_RSP_PRESET_ACRONYMS: VIS_RSP_PRESET_ACRONYMS,
	TIER_DEFS: TIER_DEFS,
	PARTS_TIER: PARTS_TIER,
	CCF_ADVANCED_HELP: CCF_ADVANCED_HELP,
	graphPath: graphPath,
};
