"use strict";

/** Deterministic parent-area colors from Allen CCF ontology (see docs/isolate_regions_style.md). */
var GROUP_STYLE_LEVEL = 6;

function parseIdPath(idPath) {
	if (!idPath) {
		return [];
	}
	return String(idPath)
		.split("/")
		.filter(Boolean)
		.map(function (s) {
			return Number(s);
		});
}

/**
 * @param {object} region - catalog node with id, st_level, idPath, byId
 * @param {object} byId - id -> node map
 */
function groupParentForRegion(region, byId) {
	if (!region) {
		return null;
	}
	var pathIds = region.idPath || parseIdPath(region.id_path);
	if (!pathIds.length) {
		pathIds = [region.id];
	}
	var atLevel = null;
	var nearestShallow = null;
	for (var i = 0; i < pathIds.length; i++) {
		var nid = pathIds[i];
		var node = byId[nid];
		if (!node) {
			continue;
		}
		if (node.st_level === GROUP_STYLE_LEVEL) {
			atLevel = node;
		}
		if (node.st_level < GROUP_STYLE_LEVEL) {
			nearestShallow = node;
		}
	}
	if (atLevel) {
		return atLevel;
	}
	if (nearestShallow) {
		return nearestShallow;
	}
	return region;
}

function normalizeHex(hex) {
	var h = String(hex || "808080").replace(/^#/, "").trim();
	if (h.length === 3) {
		h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
	}
	if (h.length !== 6) {
		h = "808080";
	}
	return "#" + h.toUpperCase();
}

function hexToRgb(hex) {
	var h = normalizeHex(hex).slice(1);
	return {
		r: parseInt(h.slice(0, 2), 16),
		g: parseInt(h.slice(2, 4), 16),
		b: parseInt(h.slice(4, 6), 16),
	};
}

function relativeLuminance(rgb) {
	var ch = [rgb.r, rgb.g, rgb.b].map(function (c) {
		var s = c / 255;
		return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
	});
	return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

function colorHexForGroup(groupNode) {
	if (!groupNode) {
		return "#808080";
	}
	return normalizeHex(groupNode.colorHex || groupNode.color_hex_triplet);
}

function rowStyleForRegion(region, byId) {
	var group = groupParentForRegion(region, byId);
	var color = colorHexForGroup(group);
	var lum = relativeLuminance(hexToRgb(color));
	var borderColor = lum > 0.72 ? darkenHex(color, 0.35) : color;
	return {
		borderLeftColor: borderColor,
		backgroundColor: "color-mix(in srgb, " + color + " 12%, var(--mj-surface, #fff))",
		swatchColor: color,
		groupId: group ? group.id : null,
		groupAcronym: group ? group.acronym : "",
		groupName: group ? group.name : "",
	};
}

function darkenHex(hex, amount) {
	var rgb = hexToRgb(hex);
	amount = amount == null ? 0.25 : amount;
	function dim(v) {
		return Math.round(v * (1 - amount));
	}
	var r = dim(rgb.r);
	var g = dim(rgb.g);
	var b = dim(rgb.b);
	return (
		"#" +
		[r, g, b]
			.map(function (x) {
				var s = x.toString(16);
				return s.length === 1 ? "0" + s : s;
			})
			.join("")
	);
}

function rowClasses() {
	return "region-picker-row";
}

module.exports = {
	GROUP_STYLE_LEVEL: GROUP_STYLE_LEVEL,
	groupParentForRegion: groupParentForRegion,
	colorHexForGroup: colorHexForGroup,
	rowStyleForRegion: rowStyleForRegion,
	rowClasses: rowClasses,
	normalizeHex: normalizeHex,
	parseIdPath: parseIdPath,
};
