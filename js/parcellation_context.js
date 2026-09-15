"use strict";

var fs = require("fs");
var path = require("path");
var branding = require("./branding");

var META_FILENAME = "annotation_parcellation.json";

function metaPath(annodir) {
	if (!annodir) return "";
	return path.join(annodir, branding.META_DIR, META_FILENAME);
}

function readParcellationMeta(annodir) {
	var p = metaPath(annodir);
	if (!p || !fs.existsSync(p)) {
		return {};
	}
	try {
		var raw = fs.readFileSync(p, "utf8");
		var data = JSON.parse(raw);
		return data && typeof data === "object" ? data : {};
	} catch (_e) {
		return {};
	}
}

function includeLayersAllowed(summary) {
	if (!summary || !summary.hasParcellation) {
		return true;
	}
	if (summary.isFullDetail) {
		return true;
	}
	if (summary.tierId === "layers") {
		return true;
	}
	if (summary.stLevel != null && Number(summary.stLevel) >= 11) {
		return true;
	}
	return false;
}

function formatParcellationLabel(entry, catalog) {
	if (!entry) {
		return "Full detail";
	}
	var tierId = entry.tier_id;
	var stLevel = entry.st_level;
	if (tierId === "full" || (!tierId && stLevel == null)) {
		return "Full detail";
	}
	if (tierId && catalog && catalog.byId) {
		// tier label from structure catalog tiers is done by caller if needed
	}
	if (stLevel != null && !tierId) {
		return "CCFv3 level " + stLevel;
	}
	if (tierId) {
		var labels = {
			major: "Major divisions",
			regions: "Classic regions",
			areas: "Functional areas",
			subareas: "Sub-areas",
			layers: "Cortical layers",
		};
		return labels[tierId] || tierId;
	}
	return "Parcellated";
}

function summarizeParcellationForLeaf(annodir, sliceIds) {
	var meta = readParcellationMeta(annodir);
	var keys = sliceIds && sliceIds.length ? sliceIds : Object.keys(meta);
	if (!keys.length) {
		return {
			hasParcellation: false,
			isFullDetail: true,
			tierId: null,
			stLevel: null,
			mixedTiers: false,
			sliceCount: 0,
			parcelledSliceCount: 0,
		};
	}
	var tiers = {};
	var parcelled = 0;
	for (var i = 0; i < keys.length; i++) {
		var sid = keys[i];
		var entry = meta[sid];
		if (!entry || typeof entry !== "object") {
			continue;
		}
		parcelled += 1;
		var key =
			String(entry.tier_id || "") +
			"|" +
			String(entry.st_level != null ? entry.st_level : "");
		tiers[key] = (tiers[key] || 0) + 1;
	}
	var tierKeys = Object.keys(tiers);
	var mixedTiers = tierKeys.length > 1;
	var dominant = null;
	if (tierKeys.length === 1) {
		dominant = tierKeys[0].split("|");
	} else if (tierKeys.length > 1) {
		tierKeys.sort(function (a, b) {
			return tiers[b] - tiers[a];
		});
		dominant = tierKeys[0].split("|");
	}
	var tierId = dominant && dominant[0] ? dominant[0] : null;
	var stLevel =
		dominant && dominant[1] !== "" && dominant[1] != null
			? Number(dominant[1])
			: null;
	var isFullDetail = parcelled === 0 || (!tierId && stLevel == null);
	return {
		hasParcellation: parcelled > 0,
		isFullDetail: isFullDetail,
		tierId: tierId,
		stLevel: stLevel,
		mixedTiers: mixedTiers,
		sliceCount: keys.length,
		parcelledSliceCount: parcelled,
		meta: meta,
	};
}

module.exports = {
	META_FILENAME: META_FILENAME,
	metaPath: metaPath,
	readParcellationMeta: readParcellationMeta,
	includeLayersAllowed: includeLayersAllowed,
	formatParcellationLabel: formatParcellationLabel,
	summarizeParcellationForLeaf: summarizeParcellationForLeaf,
};
