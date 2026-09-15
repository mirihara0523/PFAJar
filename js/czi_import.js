"use strict";

var crypto = require("crypto");
var fs = require("fs");
var path = require("path");
var url = require("url");
var perfLog = require("./perf_log");
var orientGeometry = require("./orient_geometry");

var ROLE_DAPI = "dapi";
var ROLE_SIGNAL_SOMATA = "signal_somata";
var ROLE_SIGNAL_NUCLEI = "signal_nuclei";
var ROLE_SIGNAL_AXONS = "signal_axons";
var ROLE_OTHER = "other";
var ROLE_UNUSED = "unused";

var ROLE_TO_BRANCH = {
	signal_somata: "somata",
	signal_nuclei: "nuclei",
	signal_axons: "axons",
};

var CHANNEL_ROLE_OPTIONS = [
	{ value: ROLE_DAPI, label: "DAPI (counterstain)" },
	{ value: ROLE_SIGNAL_SOMATA, label: "Signal — somata / rabies" },
	{ value: ROLE_SIGNAL_NUCLEI, label: "Signal — nuclei" },
	{ value: ROLE_SIGNAL_AXONS, label: "Signal — axons" },
	{ value: ROLE_OTHER, label: "Other (custom signal)" },
	{ value: ROLE_UNUSED, label: "Unused (skip)" },
];

var DEFAULT_PREVIEW_SCALE = 0.05;
var PREVIEWS_REL = "data/counting/_previews";
var PREVIEW_FORMAT_VERSION = 4;
var ORIENT_DISPLAY_DAPI = "dapi";

function branchForRole(role) {
	return ROLE_TO_BRANCH[role] || "";
}

function sanitizeOtherName(raw) {
	var s = String(raw || "").trim();
	if (!s) {
		return null;
	}
	s = s.replace(/\s+/g, "_");
	s = s.replace(/[^a-zA-Z0-9_-]/g, "");
	s = s.replace(/_+/g, "_").replace(/^_|_$/g, "");
	if (!s || s.length > 32) {
		return null;
	}
	return s;
}

function branchForChannel(ch) {
	if (!ch) {
		return "";
	}
	var role = ch.role;
	if (role === ROLE_DAPI || role === ROLE_UNUSED) {
		return "";
	}
	if (role === ROLE_OTHER) {
		return sanitizeOtherName(ch.other_name) || "";
	}
	return ROLE_TO_BRANCH[role] || "";
}

function roleKeyForChannel(ch) {
	if (!ch) {
		return ROLE_UNUSED;
	}
	if (ch.role === ROLE_OTHER) {
		var name = sanitizeOtherName(ch.other_name);
		return name ? "other:" + name : ROLE_OTHER;
	}
	return ch.role;
}

function isSignalChannel(ch) {
	if (!ch || !ch.keep) {
		return false;
	}
	if (
		ch.role === ROLE_SIGNAL_SOMATA ||
		ch.role === ROLE_SIGNAL_NUCLEI ||
		ch.role === ROLE_SIGNAL_AXONS
	) {
		return true;
	}
	return ch.role === ROLE_OTHER && !!sanitizeOtherName(ch.other_name);
}

function collectChannelIndices(cziImport) {
	var seen = {};
	var channels = (cziImport && cziImport.channels) || [];
	for (var i = 0; i < channels.length; i++) {
		seen[String(channels[i].index)] = true;
	}
	return Object.keys(seen)
		.map(function (k) {
			return Number(k);
		})
		.sort(function (a, b) {
			return a - b;
		});
}

function applyChannelDefaults(cziImport, channelIndex, defaults) {
	if (!cziImport.channel_defaults) {
		cziImport.channel_defaults = {};
	}
	cziImport.channel_defaults[String(channelIndex)] = {
		role: defaults.role,
		other_name: defaults.other_name || "",
	};
	var channels = cziImport.channels || [];
	for (var i = 0; i < channels.length; i++) {
		if (channels[i].index !== channelIndex) {
			continue;
		}
		channels[i].role = defaults.role;
		if (defaults.role === ROLE_OTHER) {
			channels[i].other_name = defaults.other_name || "";
		} else {
			delete channels[i].other_name;
		}
	}
	return cziImport;
}

var SLICE_NUMBERING_PRESERVE = "preserve";
var SLICE_NUMBERING_RENAME = "rename";

function sanitizeSliceStem(stem) {
	var s = String(stem || "")
		.trim()
		.replace(/[/\\:*?"<>|]+/g, "_")
		.replace(/\s+/g, "_")
		.replace(/_+/g, "_")
		.replace(/^_|_$/g, "");
	return s || "slice";
}

function naturalSortTokens(input) {
	var s = String(input || "");
	var out = [];
	var i = 0;
	while (i < s.length) {
		var c = s.charCodeAt(i);
		if (c >= 48 && c <= 57) {
			var j = i;
			while (j < s.length && s.charCodeAt(j) >= 48 && s.charCodeAt(j) <= 57) {
				j++;
			}
			out.push([0, Number(s.slice(i, j))]);
			i = j;
		} else {
			var k = i;
			while (k < s.length) {
				var cc = s.charCodeAt(k);
				if (cc >= 48 && cc <= 57) {
					break;
				}
				k++;
			}
			out.push([1, s.slice(i, k).toLowerCase()]);
			i = k;
		}
	}
	return out;
}

function compareTokens(a, b) {
	var n = Math.min(a.length, b.length);
	for (var i = 0; i < n; i++) {
		var ta = a[i];
		var tb = b[i];
		if (ta[0] !== tb[0]) {
			return ta[0] - tb[0];
		}
		if (ta[0] === 0) {
			if (ta[1] !== tb[1]) {
				return ta[1] - tb[1];
			}
		} else {
			if (ta[1] < tb[1]) {
				return -1;
			}
			if (ta[1] > tb[1]) {
				return 1;
			}
		}
	}
	return a.length - b.length;
}

function stemFromBasename(basename) {
	return String(basename || "")
		.replace(/\.czi$/i, "")
		.trim();
}

function parseSectionWithIdentifier(stem, prefix) {
	if (!prefix) {
		return null;
	}
	var s = String(stem || "");
	var p = String(prefix || "");
	var idx = s.indexOf(p);
	if (idx < 0) {
		return null;
	}
	var rest = s.slice(idx + p.length);
	var m = rest.match(/^(\d+)/);
	return m ? Number(m[1]) : null;
}

function remainderAfterSectionIdentifier(stem, prefix) {
	if (!prefix) {
		return String(stem || "");
	}
	var s = String(stem || "");
	var p = String(prefix || "");
	var idx = s.indexOf(p);
	if (idx < 0) {
		return s;
	}
	var rest = s.slice(idx + p.length);
	var m = rest.match(/^(\d+)/);
	if (!m) {
		return rest;
	}
	return rest.slice(m[1].length);
}

var SECTION_ID_DELIMITERS = [".", "(", "_", "-", " "];

function delimiterTerminatedSuffixes(greedyPrefix) {
	var candidates = [];
	var seen = {};
	function add(suffix) {
		if (suffix && !seen[suffix]) {
			seen[suffix] = true;
			candidates.push(suffix);
		}
	}
	add(greedyPrefix);
	for (var d = 0; d < SECTION_ID_DELIMITERS.length; d++) {
		var delim = SECTION_ID_DELIMITERS[d];
		var idx = greedyPrefix.indexOf(delim);
		while (idx >= 0) {
			add(greedyPrefix.slice(idx));
			if (idx + 1 < greedyPrefix.length) {
				add(greedyPrefix.slice(idx + 1));
			}
			idx = greedyPrefix.indexOf(delim, idx + 1);
		}
	}
	return candidates;
}

function greedySectionAnchor(stem) {
	var s = String(stem || "");
	var tail = s.match(/(\d+)(\D*)$/);
	if (!tail) {
		return null;
	}
	return {
		prefix: s.slice(0, s.length - tail[0].length),
		section: Number(tail[1]),
	};
}

function detectSectionIdentifierCandidates(files) {
	var fileList = files || [];
	var totalFiles = fileList.length;
	var autoOption = {
		id: "",
		label: "Automatic (natural sort)",
		prefix: null,
		matchCount: totalFiles,
		totalFiles: totalFiles,
		score: 0,
	};
	if (!totalFiles) {
		return [autoOption];
	}
	var prefixMap = {};
	for (var f = 0; f < fileList.length; f++) {
		var file = fileList[f];
		if (file.error) {
			continue;
		}
		var stem = stemFromBasename(file.basename || path.basename(file.path || ""));
		if (!stem) {
			continue;
		}
		var greedy = greedySectionAnchor(stem);
		if (!greedy) {
			continue;
		}
		var anchors = delimiterTerminatedSuffixes(greedy.prefix);
		for (var a = 0; a < anchors.length; a++) {
			var prefix = anchors[a];
			if (!prefixMap[prefix]) {
				prefixMap[prefix] = { prefix: prefix, matchCount: 0, sections: [] };
			}
			var section = parseSectionWithIdentifier(stem, prefix);
			if (section != null) {
				prefixMap[prefix].matchCount++;
				prefixMap[prefix].sections.push(section);
			}
		}
	}
	var scored = [];
	var keys = Object.keys(prefixMap);
	for (var k = 0; k < keys.length; k++) {
		var entry = prefixMap[keys[k]];
		if (!entry.matchCount) {
			continue;
		}
		var score = entry.matchCount / totalFiles;
		if (entry.matchCount === totalFiles) {
			score += 0.5;
		}
		var pfx = entry.prefix;
		if (/[(. _-]$/.test(pfx)) {
			score += 0.1;
		}
		var uniqueSections = {};
		for (var u = 0; u < entry.sections.length; u++) {
			uniqueSections[entry.sections[u]] = true;
		}
		if (Object.keys(uniqueSections).length >= Math.min(entry.matchCount, 2)) {
			score += 0.1;
		}
		var exampleStem = stemFromBasename(
			(fileList[0] && (fileList[0].basename || path.basename(fileList[0].path || ""))) || "",
		);
		scored.push({
			id: pfx,
			label: pfx + " — " + entry.matchCount + " file(s)",
			prefix: pfx,
			matchCount: entry.matchCount,
			totalFiles: totalFiles,
			example: exampleStem,
			score: score,
		});
	}
	scored.sort(function (a, b) {
		if (b.score !== a.score) {
			return b.score - a.score;
		}
		if (b.prefix.length !== a.prefix.length) {
			return b.prefix.length - a.prefix.length;
		}
		return a.prefix.localeCompare(b.prefix);
	});
	var top = scored.slice(0, 5);
	top.push(autoOption);
	return top;
}

function defaultSectionIdentifier(candidates, totalFiles) {
	var best = null;
	var list = candidates || [];
	for (var i = 0; i < list.length; i++) {
		var c = list[i];
		if (!c.prefix) {
			continue;
		}
		var ratio = totalFiles ? c.matchCount / totalFiles : 0;
		if (c.matchCount === totalFiles || ratio >= 0.95) {
			if (
				!best ||
				c.score > best.score ||
				(c.score === best.score && c.prefix.length > best.prefix.length)
			) {
				best = c;
			}
		}
	}
	return best ? best.prefix : "";
}

function parseSectionSuffix(sliceIdOrStem) {
	var tokens = naturalSortTokens(sliceIdOrStem);
	for (var i = 0; i < tokens.length; i++) {
		if (tokens[i][0] === 0) {
			return tokens[i][1];
		}
	}
	return null;
}

function entrySortStem(entry) {
	if (entry.basename) {
		return stemFromBasename(entry.basename);
	}
	var key = entry.originalSliceId || entry.sliceId || entry.path || "";
	return String(key).replace(/\.czi$/i, "").trim();
}

function entrySceneIndex(entry) {
	return entry.scene_index != null
		? Number(entry.scene_index)
		: entry.index != null
			? Number(entry.index)
			: 0;
}

function entryScanIndex(entry) {
	return entry.scan_index != null ? Number(entry.scan_index) : 0;
}

function buildFilesLookup(files) {
	var byPath = {};
	var byBasename = {};
	var basenameCounts = {};
	var list = files || [];
	for (var f = 0; f < list.length; f++) {
		var entry = list[f];
		if (entry.path) {
			byPath[String(entry.path)] = entry;
		}
		var bn = entry.basename || path.basename(entry.path || "");
		if (bn) {
			basenameCounts[bn] = (basenameCounts[bn] || 0) + 1;
			byBasename[bn] = entry;
		}
	}
	return { byPath: byPath, byBasename: byBasename, basenameCounts: basenameCounts };
}

function resolveFileEntry(fileKey, lookup) {
	if (!fileKey || !lookup) {
		return null;
	}
	var key = String(fileKey);
	if (lookup.byPath[key]) {
		return lookup.byPath[key];
	}
	var bn = path.basename(key);
	if (lookup.basenameCounts[bn] === 1) {
		return lookup.byBasename[bn] || null;
	}
	return null;
}

function naturalSortKey(entry, cziImport) {
	var sliceId = entry.sliceId || entry.originalSliceId || "";
	var basename = entry.basename || "";
	var sceneIndex = entrySceneIndex(entry);
	var scanIndex = entryScanIndex(entry);
	var sectionId =
		cziImport && cziImport.section_identifier ? String(cziImport.section_identifier) : "";
	if (sectionId) {
		var stem = entrySortStem(entry);
		var section = parseSectionWithIdentifier(stem, sectionId);
		var sectionKey = section != null ? section : 1e9;
		var remainder = remainderAfterSectionIdentifier(stem, sectionId);
		return [
			scanIndex,
			sectionKey,
			naturalSortTokens(remainder),
			sceneIndex,
			String(entry.path || "").toLowerCase(),
		];
	}
	var primary = sliceId || basename || entry.path || "";
	return [scanIndex, naturalSortTokens(primary), sceneIndex, String(entry.path || "").toLowerCase()];
}

function naturalCompare(a, b, cziImport) {
	var scanA = entryScanIndex(a);
	var scanB = entryScanIndex(b);
	if (scanA !== scanB) {
		return scanA - scanB;
	}
	var sectionId =
		cziImport && cziImport.section_identifier ? String(cziImport.section_identifier) : "";
	if (sectionId) {
		var stemA = entrySortStem(a);
		var stemB = entrySortStem(b);
		var secA = parseSectionWithIdentifier(stemA, sectionId);
		var secB = parseSectionWithIdentifier(stemB, sectionId);
		var keyA = secA != null ? secA : 1e9;
		var keyB = secB != null ? secB : 1e9;
		if (keyA !== keyB) {
			return keyA - keyB;
		}
		var remCmp = compareTokens(
			naturalSortTokens(remainderAfterSectionIdentifier(stemA, sectionId)),
			naturalSortTokens(remainderAfterSectionIdentifier(stemB, sectionId)),
		);
		if (remCmp !== 0) {
			return remCmp;
		}
	} else {
		var keyA = a.sliceId || a.originalSliceId || a.basename || a.path || "";
		var keyB = b.sliceId || b.originalSliceId || b.basename || b.path || "";
		var tokCmp = compareTokens(naturalSortTokens(keyA), naturalSortTokens(keyB));
		if (tokCmp !== 0) {
			return tokCmp;
		}
	}
	var sa = entrySceneIndex(a);
	var sb = entrySceneIndex(b);
	if (sa !== sb) {
		return sa - sb;
	}
	return String(a.path || "")
		.toLowerCase()
		.localeCompare(String(b.path || "").toLowerCase());
}

function formatOrdinalSuffix(ordinal) {
	var n = Number(ordinal) || 0;
	if (n < 10) {
		return "00" + String(n);
	}
	if (n < 100) {
		return "0" + String(n);
	}
	return String(n);
}

function isValidSliceId(sliceId) {
	var s = sanitizeSliceStem(sliceId);
	return !!s && s === String(sliceId || "").trim();
}

function defaultSliceId(basename, sceneIndex, sceneCount) {
	var stem = sanitizeSliceStem(String(basename || "slice").replace(/\.czi$/i, ""));
	if (sceneCount > 1) {
		return stem + "_s" + formatOrdinalSuffix(sceneIndex);
	}
	return stem;
}

function canonicalSourceDir(dir) {
	if (!dir) {
		return "";
	}
	try {
		return path.resolve(String(dir).trim());
	} catch (e) {
		return String(dir).trim();
	}
}

function normalizeProbeFileEntry(file, sourceDir, scanIndex) {
	var scenes = (file.scenes || []).map(function (sc) {
		var originalSliceId =
			sc.originalSliceId ||
			sc.sliceId ||
			defaultSliceId(file.basename, sc.index, file.scene_count || (file.scenes || []).length || 1);
		return {
			index: sc.index,
			sliceId: originalSliceId,
			originalSliceId: originalSliceId,
		};
	});
	return {
		path: file.path,
		basename: file.basename,
		source_dir: sourceDir || "",
		scan_index: scanIndex != null ? scanIndex : 0,
		dims: file.dims,
		scene_count: file.scene_count,
		channel_count: file.channel_count,
		z_count: file.z_count,
		is_mosaic: file.is_mosaic,
		has_m_dim: file.has_m_dim,
		m_tile_count: file.m_tile_count,
		likely_unstitched: file.likely_unstitched,
		mosaic_stitch_status: file.mosaic_stitch_status || "unknown",
		mosaic_warnings: file.mosaic_warnings || [],
		read_warnings: file.read_warnings || [],
		channel_pixel_probe: file.channel_pixel_probe || [],
		scenes: scenes,
		channels: file.channels || [],
		error: file.error,
	};
}

function collectMosaicWarnings(files) {
	var out = [];
	var seen = {};
	var list = files || [];
	for (var i = 0; i < list.length; i++) {
		var f = list[i];
		if (f.error) {
			continue;
		}
		if (!f.likely_unstitched && f.mosaic_stitch_status !== "suspect") {
			continue;
		}
		var warnings = f.mosaic_warnings || [];
		for (var w = 0; w < warnings.length; w++) {
			var msg = String(warnings[w] || "").trim();
			if (!msg || msg.indexOf("normal for ZEN-stitched") >= 0) {
				continue;
			}
			var key = (f.basename || f.path || "file") + "|" + msg;
			if (seen[key]) {
				continue;
			}
			seen[key] = true;
			out.push({
				basename: f.basename || "",
				message: msg,
			});
		}
		if (f.likely_unstitched && !warnings.length) {
			var fallback =
				(f.basename || "CZI") +
				": mosaic tiles may be unstitched — stitch in ZEN before import.";
			if (!seen[fallback]) {
				seen[fallback] = true;
				out.push({ basename: f.basename || "", message: fallback });
			}
		}
	}
	return out;
}

function collectChannelProbeWarnings(files) {
	var out = [];
	var seen = {};
	var list = files || [];
	for (var i = 0; i < list.length; i++) {
		var f = list[i];
		if (f.error) {
			continue;
		}
		var warnings = f.read_warnings || [];
		for (var w = 0; w < warnings.length; w++) {
			var msg = String(warnings[w] || "").trim();
			if (!msg) {
				continue;
			}
			var key = (f.basename || f.path || "file") + "|" + msg;
			if (seen[key]) {
				continue;
			}
			seen[key] = true;
			out.push({
				basename: f.basename || "",
				message: msg,
				isError: msg.indexOf("sample read failed") >= 0,
			});
		}
		var probe = f.channel_pixel_probe || [];
		for (var p = 0; p < probe.length; p++) {
			var entry = probe[p];
			if (entry.ok !== false) {
				continue;
			}
			var errMsg =
				(f.basename || "CZI") +
				" channel " +
				entry.index +
				": read check failed — " +
				(entry.error || "unknown");
			if (!seen[errMsg]) {
				seen[errMsg] = true;
				out.push({
					basename: f.basename || "",
					message: errMsg,
					isError: true,
				});
			}
		}
	}
	return out;
}

function collectMosaicInfo(files) {
	var out = [];
	var seen = {};
	var list = files || [];
	for (var i = 0; i < list.length; i++) {
		var f = list[i];
		if (f.error || f.is_mosaic !== true) {
			continue;
		}
		if (f.likely_unstitched || f.mosaic_stitch_status === "suspect") {
			continue;
		}
		var tiles = f.m_tile_count != null ? f.m_tile_count : f.has_m_dim ? "2+" : null;
		if (!tiles || tiles === 1) {
			continue;
		}
		var key = (f.basename || f.path || "file") + "|" + tiles;
		if (seen[key]) {
			continue;
		}
		seen[key] = true;
		out.push({
			basename: f.basename || "",
			message:
				"Mosaic with " +
				tiles +
				" tile index(es) in file structure (normal for ZEN-stitched exports).",
		});
	}
	return out;
}

function hasLikelyUnstitchedMosaic(files) {
	var list = files || [];
	for (var i = 0; i < list.length; i++) {
		if (list[i].likely_unstitched) {
			return true;
		}
	}
	return false;
}

function rebuildChannelsFromFiles(cziImport) {
	var files = cziImport.files || [];
	var channels = [];
	for (var i = 0; i < files.length; i++) {
		var file = files[i];
		if (file.error) {
			continue;
		}
		var chans = file.channels || [];
		for (var c = 0; c < chans.length; c++) {
			var ch = chans[c];
			channels.push({
				file: file.path || file.basename,
				index: ch.index,
				label: ch.label || "",
				role: ch.suggested_role || suggestRoleFromLabel(ch.label),
				other_name: "",
				keep: true,
			});
		}
	}
	var existingDefaults = cziImport.channel_defaults || {};
	var channelDefaults = {};
	for (var j = 0; j < channels.length; j++) {
		var key = String(channels[j].index);
		if (!existingDefaults[key] && !channelDefaults[key]) {
			channelDefaults[key] = {
				role: channels[j].role,
				other_name: "",
			};
		}
	}
	cziImport.channel_defaults = Object.assign({}, existingDefaults, channelDefaults);
	for (var k = 0; k < channels.length; k++) {
		var def = cziImport.channel_defaults[String(channels[k].index)];
		if (def) {
			channels[k].role = def.role;
			channels[k].other_name = def.other_name || "";
		}
	}
	cziImport.channels = channels;
	return cziImport;
}

function syncScenesFromSliceOrder(cziImport) {
	var order = cziImport.slice_order || [];
	if (!order.length) {
		return cziImport;
	}
	var byKey = {};
	for (var i = 0; i < order.length; i++) {
		var entry = order[i];
		byKey[String(entry.path) + "|" + String(entry.scene_index)] = entry.sliceId;
	}
	var files = cziImport.files || [];
	for (var f = 0; f < files.length; f++) {
		var file = files[f];
		var scenes = file.scenes || [];
		for (var s = 0; s < scenes.length; s++) {
			var sc = scenes[s];
			var key = String(file.path) + "|" + String(sc.index);
			if (byKey[key]) {
				sc.sliceId = byKey[key];
			}
		}
	}
	return cziImport;
}

function buildSliceOrder(cziImport, projectStem) {
	var entries = [];
	var files = cziImport.files || [];
	for (var f = 0; f < files.length; f++) {
		var file = files[f];
		if (file.error) {
			continue;
		}
		var scenes = file.scenes || [];
		for (var s = 0; s < scenes.length; s++) {
			var sc = scenes[s];
			var sceneCount = file.scene_count || scenes.length || 1;
			var originalSliceId =
				sc.originalSliceId ||
				sc.sliceId ||
				defaultSliceId(file.basename, sc.index, sceneCount);
			entries.push({
				path: file.path,
				basename: file.basename,
				scene_index: sc.index,
				source_dir: file.source_dir || cziImport.source_dir || "",
				scan_index: file.scan_index != null ? file.scan_index : 0,
				originalSliceId: originalSliceId,
				sliceId: originalSliceId,
			});
		}
	}
	entries.sort(function (a, b) {
		return naturalCompare(a, b, cziImport);
	});
	var numbering = cziImport.slice_numbering || SLICE_NUMBERING_PRESERVE;
	var stem = sanitizeSliceStem(projectStem || "project");
	for (var i = 0; i < entries.length; i++) {
		entries[i].ordinal = i + 1;
		if (numbering === SLICE_NUMBERING_RENAME) {
			entries[i].sliceId = stem + "_s" + formatOrdinalSuffix(entries[i].ordinal);
		} else {
			entries[i].sliceId = entries[i].originalSliceId;
		}
	}
	cziImport.slice_order = entries;
	syncScenesFromSliceOrder(cziImport);
	return entries;
}

function validateSliceOrder(cziImport) {
	var order = cziImport.slice_order || [];
	if (!order.length) {
		return "No scenes probed yet.";
	}
	var seen = {};
	for (var i = 0; i < order.length; i++) {
		var entry = order[i];
		var sliceId = String(entry.sliceId || "").trim();
		if (!sliceId) {
			return "Every scene needs a slice ID.";
		}
		if (!isValidSliceId(sliceId)) {
			return "Invalid slice ID: " + sliceId + " (use letters, numbers, _ and -).";
		}
		if (seen[sliceId]) {
			return "Duplicate slice ID: " + sliceId;
		}
		seen[sliceId] = true;
		entry.sliceId = sliceId;
	}
	syncScenesFromSliceOrder(cziImport);
	return "";
}

function mergeProbeDirIntoImport(cziImport, probeResult, sourceDir, scanIndex) {
	var canonicalDir = canonicalSourceDir(sourceDir);
	var incoming = (probeResult.files || []).map(function (f) {
		return normalizeProbeFileEntry(f, canonicalDir, scanIndex);
	});
	var files = (cziImport.files || []).filter(function (f) {
		return canonicalSourceDir(f.source_dir) !== canonicalDir;
	});
	cziImport.files = files.concat(incoming);
	if (!cziImport.source_dirs) {
		cziImport.source_dirs = [];
	}
	var dirs = cziImport.source_dirs;
	var foundIdx = -1;
	for (var d = 0; d < dirs.length; d++) {
		if (canonicalSourceDir(dirs[d]) === canonicalDir) {
			foundIdx = d;
			break;
		}
	}
	if (foundIdx >= 0) {
		dirs[foundIdx] = canonicalDir;
	} else {
		dirs.push(canonicalDir);
	}
	cziImport.source_dir = dirs[0] || canonicalDir || "";
	rebuildChannelsFromFiles(cziImport);
	return cziImport;
}

function collectKeptSignalRoleKeys(cziImport) {
	var channels = cziImport.channels || [];
	var seen = [];
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		if (!isSignalChannel(ch)) {
			continue;
		}
		var key = roleKeyForChannel(ch);
		if (seen.indexOf(key) < 0) {
			seen.push(key);
		}
	}
	return seen;
}

function suggestRoleFromLabel(label) {
	var text = String(label || "");
	if (/dapi/i.test(text)) {
		return ROLE_DAPI;
	}
	if (/soma|rabies/i.test(text)) {
		return ROLE_SIGNAL_SOMATA;
	}
	if (/nucle/i.test(text)) {
		return ROLE_SIGNAL_NUCLEI;
	}
	if (/axon/i.test(text)) {
		return ROLE_SIGNAL_AXONS;
	}
	return ROLE_UNUSED;
}

function buildDefaultCziImport(sourceDir) {
	return {
		version: 1,
		source_dir: sourceDir || "",
		source_dirs: sourceDir ? [sourceDir] : [],
		slice_numbering: SLICE_NUMBERING_PRESERVE,
		section_identifier: null,
		slice_order: [],
		files: [],
		channels: [],
		channel_defaults: {},
		geometry: {},
		preview_scale: DEFAULT_PREVIEW_SCALE,
		primary_signal_role: ROLE_SIGNAL_SOMATA,
		bit_depth_by_role: {},
	};
}

function mergeProbeIntoImport(cziImport, probeResult) {
	var sourceDir = cziImport.source_dir || "";
	cziImport.files = [];
	cziImport.source_dirs = sourceDir ? [sourceDir] : [];
	return mergeProbeDirIntoImport(cziImport, probeResult, sourceDir, 0);
}

function importConfigPath(bundleRoot) {
	return path.join(bundleRoot, ".masonjar", "czi_import_config.json");
}

function maxRunRelForRole(roleKey, slug) {
	var branch = "";
	if (String(roleKey).indexOf("other:") === 0) {
		branch = roleKey.slice(6);
	} else {
		branch = branchForRole(roleKey);
	}
	if (!branch) {
		return "";
	}
	return branch + "/max/" + slug;
}

function countExtractWorkItems(cfg) {
	var channels = (cfg.channels || []).filter(function (ch) {
		return ch.keep && ch.role !== ROLE_UNUSED;
	});
	var lookup = buildFilesLookup(cfg.files || []);
	var count = 0;
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		if (ch.role === ROLE_OTHER && !sanitizeOtherName(ch.other_name)) {
			continue;
		}
		var fileEntry = resolveFileEntry(ch.file || "", lookup);
		if (!fileEntry) {
			continue;
		}
		var scenes = fileEntry.scenes || [{ index: 0 }];
		count += scenes.length;
	}
	return count;
}

function collectSliceIds(cziImport) {
	var order = cziImport.slice_order || [];
	if (order.length) {
		return order.map(function (entry) {
			return entry.sliceId;
		}).filter(Boolean);
	}
	var ids = [];
	var files = cziImport.files || [];
	for (var i = 0; i < files.length; i++) {
		var scenes = files[i].scenes || [];
		for (var s = 0; s < scenes.length; s++) {
			if (scenes[s].sliceId && ids.indexOf(scenes[s].sliceId) < 0) {
				ids.push(scenes[s].sliceId);
			}
		}
	}
	ids.sort(function (a, b) {
		return naturalCompare({ sliceId: a }, { sliceId: b }, cziImport);
	});
	return ids;
}

function primaryMaxRunRel(cziImport, importResult) {
	var role =
		(importResult && importResult.primary_signal_role) ||
		cziImport.primary_signal_role ||
		ROLE_SIGNAL_SOMATA;
	var maxRuns = (importResult && importResult.max_runs) || cziImport.max_runs || {};
	return maxRuns[role] || "";
}

function originalScansPath(bundleRoot, channel, sliceId) {
	var branch = branchForChannel(channel);
	var base = path.join(bundleRoot, "data/original_scans");
	if (branch) {
		return path.join(base, branch, sliceId + ".tif");
	}
	return path.join(base, sliceId + ".tif");
}

function dapiPreviewPath(bundleRoot, sliceId) {
	return path.join(bundleRoot, "data/counting/00_dapi", sliceId + ".png");
}

function orientDapiPreviewPath(bundleRoot, sliceId) {
	return path.join(bundleRoot, PREVIEWS_REL, sliceId + "_dapi.png");
}

function signalPreviewPath(bundleRoot, sliceId, channel) {
	var branch = branchForChannel(channel);
	var suffix = branch || roleKeyForChannel(channel);
	return path.join(bundleRoot, PREVIEWS_REL, sliceId + "_" + suffix + ".png");
}

function previewPathForChannel(bundleRoot, sliceId, channel) {
	if (!channel || !channel.keep || channel.role === ROLE_UNUSED) {
		return "";
	}
	if (channel.role === ROLE_DAPI) {
		return dapiPreviewPath(bundleRoot, sliceId);
	}
	if (branchForChannel(channel)) {
		return signalPreviewPath(bundleRoot, sliceId, channel);
	}
	return "";
}

function findKeptChannelForRoleKey(cziImport, roleKey) {
	var channels = cziImport.channels || [];
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		if (!ch.keep || ch.role === ROLE_UNUSED) {
			continue;
		}
		if (roleKeyForChannel(ch) === roleKey) {
			return ch;
		}
	}
	return null;
}

function displayChannelKeyForChannel(ch) {
	if (!ch || ch.role === ROLE_DAPI) {
		return ORIENT_DISPLAY_DAPI;
	}
	var branch = branchForChannel(ch);
	if (branch) {
		return branch;
	}
	return roleKeyForChannel(ch);
}

function displayChannelLabelForChannel(ch) {
	if (!ch) {
		return "";
	}
	if (ch.role === ROLE_SIGNAL_SOMATA) {
		return "Somata";
	}
	if (ch.role === ROLE_SIGNAL_NUCLEI) {
		return "Nuclei";
	}
	if (ch.role === ROLE_SIGNAL_AXONS) {
		return "Axons";
	}
	if (ch.role === ROLE_OTHER) {
		var name = sanitizeOtherName(ch.other_name);
		if (name) {
			return name.charAt(0).toUpperCase() + name.slice(1).replace(/_/g, " ");
		}
		return "Other signal";
	}
	return branchForChannel(ch) || roleKeyForChannel(ch);
}

function findKeptChannelForDisplayKey(cziImport, displayKey) {
	if (!displayKey || displayKey === ORIENT_DISPLAY_DAPI) {
		return null;
	}
	var channels = cziImport.channels || [];
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		if (!ch.keep || ch.role === ROLE_UNUSED || ch.role === ROLE_DAPI) {
			continue;
		}
		if (displayChannelKeyForChannel(ch) === displayKey) {
			return ch;
		}
		if (roleKeyForChannel(ch) === displayKey) {
			return ch;
		}
		if (branchForChannel(ch) === displayKey) {
			return ch;
		}
	}
	return null;
}

function orientDapiPngExistsInBundle(bundleRoot) {
	if (!bundleRoot) {
		return false;
	}
	var prevDir = path.join(bundleRoot, PREVIEWS_REL);
	if (!fs.existsSync(prevDir)) {
		return false;
	}
	var entries = fs.readdirSync(prevDir);
	for (var i = 0; i < entries.length; i++) {
		if (/_dapi\.png$/i.test(entries[i])) {
			return true;
		}
	}
	return false;
}

function listOrientDisplayChannels(bundleRoot, cziImport) {
	cziImport = cziImport || {};
	var out = [];
	var seenKeys = {};

	function addEntry(key, label) {
		if (!key || seenKeys[key]) {
			return;
		}
		seenKeys[key] = true;
		out.push({ key: key, label: label });
	}

	var hasDapiRole = false;
	var channels = cziImport.channels || [];
	for (var i = 0; i < channels.length; i++) {
		if (channels[i].keep && channels[i].role === ROLE_DAPI) {
			hasDapiRole = true;
			break;
		}
	}
	if (orientDapiPngExistsInBundle(bundleRoot) || hasDapiRole) {
		addEntry(ORIENT_DISPLAY_DAPI, "DAPI (_previews)");
	}

	for (var c = 0; c < channels.length; c++) {
		var ch = channels[c];
		if (!isSignalChannel(ch)) {
			continue;
		}
		addEntry(displayChannelKeyForChannel(ch), displayChannelLabelForChannel(ch));
	}

	if (out.length <= 1 && bundleRoot) {
		var sliceIds = collectSliceIds(cziImport);
		var firstSlice = sliceIds[0];
		if (firstSlice) {
			var prevDir = path.join(bundleRoot, PREVIEWS_REL);
			if (fs.existsSync(prevDir)) {
				var prevEntries = fs.readdirSync(prevDir);
				var prefix = firstSlice + "_";
				var suffixes = {};
				for (var p = 0; p < prevEntries.length; p++) {
					var name = prevEntries[p];
					if (name.indexOf(prefix) !== 0 || !name.toLowerCase().endsWith(".png")) {
						continue;
					}
					var suffix = name.slice(prefix.length, -4);
					if (suffix === "dapi" && (orientDapiPngExistsInBundle(bundleRoot) || hasDapiRole)) {
						continue;
					}
					suffixes[suffix] = true;
				}
				var suffixKeys = Object.keys(suffixes).sort();
				for (var s = 0; s < suffixKeys.length; s++) {
					var sk = suffixKeys[s];
					var skLabel = sk.charAt(0).toUpperCase() + sk.slice(1).replace(/_/g, " ");
					addEntry(sk, skLabel);
				}
			}
		}
	}

	if (!out.length) {
		addEntry(ORIENT_DISPLAY_DAPI, "DAPI (_previews)");
	}
	return out;
}

function orientPreviewPathForChannel(bundleRoot, sliceId, channel) {
	if (!channel || !channel.keep || channel.role === ROLE_UNUSED) {
		return "";
	}
	if (channel.role === ROLE_DAPI) {
		return orientDapiPreviewPath(bundleRoot, sliceId);
	}
	if (branchForChannel(channel)) {
		return signalPreviewPath(bundleRoot, sliceId, channel);
	}
	return "";
}

function resolveOrientPreviewPath(
	bundleRoot,
	cziImport,
	importResult,
	sliceId,
	displayChannelKey,
) {
	if (!bundleRoot || !sliceId) {
		return "";
	}
	if (displayChannelKey == null || displayChannelKey === "") {
		displayChannelKey = ORIENT_DISPLAY_DAPI;
	}

	if (displayChannelKey === ORIENT_DISPLAY_DAPI) {
		var orientDapi = orientDapiPreviewPath(bundleRoot, sliceId);
		if (fs.existsSync(orientDapi)) {
			return orientDapi;
		}
		return "";
	}

	var ch = findKeptChannelForDisplayKey(cziImport || {}, displayChannelKey);
	if (ch) {
		var signalPrev = signalPreviewPath(bundleRoot, sliceId, ch);
		if (fs.existsSync(signalPrev)) {
			return signalPrev;
		}
	}

	var previewFormatVersion =
		(cziImport && cziImport.preview_format_version) || PREVIEW_FORMAT_VERSION;
	if (previewFormatVersion < PREVIEW_FORMAT_VERSION) {
		var ch2 = findKeptChannelForDisplayKey(cziImport || {}, displayChannelKey);
		if (ch2) {
			var legacySuffix = branchForChannel(ch2) || roleKeyForChannel(ch2);
			var legacySignalPrev = path.join(
				bundleRoot,
				PREVIEWS_REL,
				sliceId + "_" + legacySuffix + ".tif",
			);
			if (fs.existsSync(legacySignalPrev)) {
				return legacySignalPrev;
			}
		}
		var legacyDirectTif = path.join(
			bundleRoot,
			PREVIEWS_REL,
			sliceId + "_" + displayChannelKey + ".tif",
		);
		if (fs.existsSync(legacyDirectTif)) {
			return legacyDirectTif;
		}
	}

	var directPng = path.join(
		bundleRoot,
		PREVIEWS_REL,
		sliceId + "_" + displayChannelKey + ".png",
	);
	if (fs.existsSync(directPng)) {
		return directPng;
	}
	if (previewFormatVersion < PREVIEW_FORMAT_VERSION) {
		var directTif = path.join(
			bundleRoot,
			PREVIEWS_REL,
			sliceId + "_" + displayChannelKey + ".tif",
		);
		if (fs.existsSync(directTif)) {
			return directTif;
		}
	}
	return "";
}

function normalizeSourceDirs(cziImport) {
	var dirs = (cziImport.source_dirs || []).slice();
	if (!dirs.length && cziImport.source_dir) {
		dirs = [cziImport.source_dir];
	}
	var seen = {};
	var out = [];
	for (var i = 0; i < dirs.length; i++) {
		var resolved;
		try {
			resolved = path.resolve(String(dirs[i] || ""));
		} catch (e) {
			resolved = String(dirs[i] || "");
		}
		if (!resolved || seen[resolved]) {
			continue;
		}
		seen[resolved] = true;
		out.push(resolved);
	}
	return out;
}

function fingerprintPayload(cziImport) {
	var keptChannels = (cziImport.channels || [])
		.filter(function (ch) {
			return ch.keep;
		})
		.map(function (ch) {
			return {
				file: ch.file || "",
				index: ch.index,
				role: ch.role,
				other_name: ch.other_name || "",
				keep: true,
			};
		})
		.sort(function (a, b) {
			var fa = String(a.file);
			var fb = String(b.file);
			if (fa < fb) {
				return -1;
			}
			if (fa > fb) {
				return 1;
			}
			return a.index - b.index;
		});
	var sliceOrder = (cziImport.slice_order || []).map(function (entry) {
		return {
			ordinal: entry.ordinal,
			sliceId: entry.sliceId,
			path: entry.path || "",
			scene_index: entry.scene_index,
			basename: entry.basename || "",
		};
	});
	return {
		source_dirs: normalizeSourceDirs(cziImport),
		slice_numbering: cziImport.slice_numbering || SLICE_NUMBERING_PRESERVE,
		slice_order: sliceOrder,
		channels: keptChannels,
		primary_signal_role: cziImport.primary_signal_role || ROLE_SIGNAL_SOMATA,
		preview_scale: cziImport.preview_scale != null ? cziImport.preview_scale : DEFAULT_PREVIEW_SCALE,
	};
}

function cziImportFingerprint(cziImport) {
	return crypto
		.createHash("sha256")
		.update(JSON.stringify(fingerprintPayload(cziImport)))
		.digest("hex");
}

function importStatePath(bundleRoot) {
	var mason = path.join(bundleRoot, ".masonjar", "czi_import_state.json");
	if (fs.existsSync(mason)) {
		return mason;
	}
	var legacy = path.join(bundleRoot, ".belljar", "czi_import_state.json");
	if (fs.existsSync(legacy)) {
		return legacy;
	}
	return mason;
}

function readImportState(bundleRoot) {
	var statePath = importStatePath(bundleRoot);
	if (!fs.existsSync(statePath)) {
		return null;
	}
	try {
		return JSON.parse(fs.readFileSync(statePath, "utf8"));
	} catch (e) {
		return null;
	}
}

function scenesForFileEntry(fileEntry, cziImport) {
	if (!fileEntry) {
		return [];
	}
	var order = cziImport.slice_order || [];
	var filePath = String(fileEntry.path || "");
	var basename = fileEntry.basename || "";
	var fromOrder = [];
	for (var i = 0; i < order.length; i++) {
		var entry = order[i];
		if (filePath && entry.path && String(entry.path) !== filePath) {
			continue;
		}
		if (basename && entry.basename && entry.basename !== basename) {
			continue;
		}
		fromOrder.push({
			sliceId: entry.sliceId,
			scene_index: entry.scene_index,
		});
	}
	if (fromOrder.length) {
		return fromOrder;
	}
	var scenes = fileEntry.scenes || [];
	return scenes.map(function (sc) {
		return {
			sliceId: sc.sliceId,
			scene_index: sc.index,
		};
	});
}

function iterKeptChannelScenes(cziImport) {
	var lookup = buildFilesLookup(cziImport.files || []);
	var channels = (cziImport.channels || []).filter(function (ch) {
		return ch.keep && ch.role !== ROLE_UNUSED;
	});
	var items = [];
	for (var c = 0; c < channels.length; c++) {
		var ch = channels[c];
		if (ch.role === ROLE_OTHER && !sanitizeOtherName(ch.other_name)) {
			continue;
		}
		var fileRef = resolveFileEntry(ch.file || "", lookup);
		var scenes = scenesForFileEntry(fileRef, cziImport);
		for (var si = 0; si < scenes.length; si++) {
			var scene = scenes[si];
			if (!scene.sliceId) {
				continue;
			}
			items.push({
				slice_id: scene.sliceId,
				scene_index: scene.scene_index != null ? scene.scene_index : 0,
				channel_index: ch.index,
				channel: ch,
				role_key: roleKeyForChannel(ch),
				file: fileRef ? fileRef.basename : ch.file,
				czi_path: fileRef ? fileRef.path : "",
			});
		}
	}
	return items;
}

function isPreviewFileValid(previewPath, previewFormatVersion) {
	if (previewFormatVersion == null || previewFormatVersion < PREVIEW_FORMAT_VERSION) {
		return false;
	}
	if (!previewPath || !fs.existsSync(previewPath)) {
		return false;
	}
	if (previewFormatVersion >= PREVIEW_FORMAT_VERSION && !previewPath.toLowerCase().endsWith(".png")) {
		return false;
	}
	try {
		return fs.statSync(previewPath).size > 0;
	} catch (e) {
		return false;
	}
}

function findLowResTiffIssues(bundleRoot) {
	var issues = [];
	var dapiDir = path.join(bundleRoot, "data/counting/00_dapi");
	if (fs.existsSync(dapiDir)) {
		var dapiEntries = fs.readdirSync(dapiDir);
		for (var d = 0; d < dapiEntries.length; d++) {
			var dname = dapiEntries[d];
			var lower = dname.toLowerCase();
			if (lower.endsWith(".tif") || lower.endsWith(".tiff")) {
				issues.push({
					kind: "dapi_tif",
					path: path.join(dapiDir, dname),
					slice_id: path.basename(dname, path.extname(dname)),
				});
			}
		}
	}
	var prevDir = path.join(bundleRoot, PREVIEWS_REL);
	if (fs.existsSync(prevDir)) {
		var prevEntries = fs.readdirSync(prevDir);
		for (var p = 0; p < prevEntries.length; p++) {
			var pname = prevEntries[p];
			var plower = pname.toLowerCase();
			if (plower.endsWith(".tif") || plower.endsWith(".tiff")) {
				issues.push({
					kind: "preview_tif",
					path: path.join(prevDir, pname),
				});
			}
		}
	}
	return issues;
}

function findMissingOrientDapiPreviews(bundleRoot) {
	var missing = [];
	if (!bundleRoot) {
		return missing;
	}
	var dapiDir = path.join(bundleRoot, "data/counting/00_dapi");
	if (!fs.existsSync(dapiDir)) {
		return missing;
	}
	var entries = fs.readdirSync(dapiDir);
	for (var i = 0; i < entries.length; i++) {
		var name = entries[i];
		if (!name.toLowerCase().endsWith(".png")) {
			continue;
		}
		var sliceId = path.basename(name, path.extname(name));
		var orientPath = orientDapiPreviewPath(bundleRoot, sliceId);
		if (!fs.existsSync(orientPath)) {
			missing.push({ slice_id: sliceId, path: orientPath });
		}
	}
	return missing;
}

function ensureOrientDapiPreviewsFromPipeline(bundleRoot) {
	var synced = 0;
	var missing = findMissingOrientDapiPreviews(bundleRoot);
	for (var i = 0; i < missing.length; i++) {
		var sliceId = missing[i].slice_id;
		var src = dapiPreviewPath(bundleRoot, sliceId);
		var dest = orientDapiPreviewPath(bundleRoot, sliceId);
		try {
			fs.mkdirSync(path.dirname(dest), { recursive: true });
			fs.copyFileSync(src, dest);
			synced += 1;
		} catch (e) {
			console.warn("[czi_import] sync orient DAPI preview failed", sliceId, e);
		}
	}
	return synced;
}

// Lightweight audit for callers (e.g. the workspace hub banner) that only need
// missingMaxRuns / completeness — NOT the full integrity audit. Skips the
// per-slice zstack + preview scans (workItemsLoop) and per-preview blank
// detection (findBlankPreviews), which together cost minutes on NAS. Uses one
// readdirSync per max dir + in-memory membership instead of per-slice existsSync.
function auditMaxRunsOnly(bundleRoot, cziImport, options) {
	options = options || {};
	var importResult = options.importResult || null;
	var maxRuns = (importResult && importResult.max_runs) || cziImport.max_runs || {};
	var signalKeys = collectKeptSignalRoleKeys(cziImport);
	var sliceIds = collectSliceIds(cziImport);
	var missingMaxRuns = [];
	for (var r = 0; r < signalKeys.length; r++) {
		var roleKey = signalKeys[r];
		var rel = maxRuns[roleKey] || maxRunRelForRole(roleKey, sliceIds[0] || "run");
		if (!rel) {
			missingMaxRuns.push({ role_key: roleKey, reason: "no max run registered" });
			continue;
		}
		var maxDir = path.join(bundleRoot, "data/counting/03_max", rel);
		if (!fs.existsSync(maxDir)) {
			missingMaxRuns.push({ role_key: roleKey, path: maxDir });
			continue;
		}
		var present = {};
		try {
			var names = fs.readdirSync(maxDir);
			for (var n = 0; n < names.length; n++) {
				present[names[n]] = true;
			}
		} catch (_e) {}
		var missingSlice = false;
		for (var s = 0; s < sliceIds.length; s++) {
			if (!present[sliceIds[s] + ".tif"]) {
				missingSlice = true;
				break;
			}
		}
		if (missingSlice) {
			missingMaxRuns.push({
				role_key: roleKey,
				path: maxDir,
				reason: "missing slice tifs",
			});
		}
	}
	return { missingMaxRuns: missingMaxRuns };
}

function auditCziImportCompletion(bundleRoot, cziImport, options) {
	options = options || {};
	var importResult = options.importResult || null;
	var state = readImportState(bundleRoot);
	var previewFormatVersion =
		(state && state.preview_format_version) ||
		(cziImport && cziImport.preview_format_version) ||
		0;
	var extractComplete =
		!!state &&
		state.phase === "complete" &&
		Number(state.done) === Number(state.total) &&
		state.total > 0;
	var missingZstacks = [];
	var invalidPreviews = [];
	var missingMaxRuns = [];
	var lowResTiffIssues = perfLog.perfSection("audit.findLowResTiffIssues", function () {
		return findLowResTiffIssues(bundleRoot);
	});
	var missingOrientDapiPreviews = perfLog.perfSection(
		"audit.findMissingOrientDapiPreviews",
		function () {
			return findMissingOrientDapiPreviews(bundleRoot);
		},
	);
	var workItems = iterKeptChannelScenes(cziImport);

	var _auditWorkT0 = perfLog.now();
	for (var i = 0; i < workItems.length; i++) {
		var item = workItems[i];
		var zPath = originalScansPath(bundleRoot, item.channel, item.slice_id);
		if (!fs.existsSync(zPath)) {
			missingZstacks.push({
				slice_id: item.slice_id,
				path: zPath,
				role_key: item.role_key,
			});
		}
		var prevPath = orientPreviewPathForChannel(bundleRoot, item.slice_id, item.channel);
		if (prevPath && !isPreviewFileValid(prevPath, previewFormatVersion)) {
			invalidPreviews.push({
				slice_id: item.slice_id,
				channel_index: item.channel_index,
				role_key: item.role_key,
				file: item.file,
				scene_index: item.scene_index,
				czi_path: item.czi_path,
				preview_path: prevPath,
			});
		}
	}

	perfLog.perfLog("audit.workItemsLoop", perfLog.now() - _auditWorkT0);

	var maxRuns = (importResult && importResult.max_runs) || cziImport.max_runs || {};
	var signalKeys = collectKeptSignalRoleKeys(cziImport);
	var sliceIds = collectSliceIds(cziImport);
	var _auditMaxT0 = perfLog.now();
	for (var r = 0; r < signalKeys.length; r++) {
		var roleKey = signalKeys[r];
		var rel = maxRuns[roleKey] || maxRunRelForRole(roleKey, sliceIds[0] || "run");
		if (!rel) {
			missingMaxRuns.push({ role_key: roleKey, reason: "no max run registered" });
			continue;
		}
		var maxDir = path.join(bundleRoot, "data/counting/03_max", rel);
		if (!fs.existsSync(maxDir)) {
			missingMaxRuns.push({ role_key: roleKey, path: maxDir });
			continue;
		}
		var missingSlice = false;
		for (var s = 0; s < sliceIds.length; s++) {
			var maxTif = path.join(maxDir, sliceIds[s] + ".tif");
			if (!fs.existsSync(maxTif)) {
				missingSlice = true;
				break;
			}
		}
		if (missingSlice) {
			missingMaxRuns.push({ role_key: roleKey, path: maxDir, reason: "missing slice tifs" });
		}
	}
	perfLog.perfLog("audit.maxDirLoop", perfLog.now() - _auditMaxT0);

	var _tBlank = perfLog.now();
	var blankPreviews =
		options.blankPreviews != null
			? options.blankPreviews
			: findBlankPreviews(bundleRoot, cziImport, {});
	perfLog.perfLog("audit.findBlankPreviews", perfLog.now() - _tBlank);
	var blankPreviewCount = (blankPreviews && blankPreviews.length) || 0;

	var canSkipToOrient =
		extractComplete &&
		missingZstacks.length === 0 &&
		invalidPreviews.length === 0 &&
		missingMaxRuns.length === 0 &&
		lowResTiffIssues.length === 0 &&
		missingOrientDapiPreviews.length === 0 &&
		blankPreviewCount === 0;
	var needsPreviewRepair =
		(extractComplete && missingZstacks.length === 0 && invalidPreviews.length > 0) ||
		(extractComplete && lowResTiffIssues.length > 0) ||
		(extractComplete && missingOrientDapiPreviews.length > 0) ||
		(extractComplete && blankPreviewCount > 0);

	return {
		extractComplete: extractComplete,
		missingZstacks: missingZstacks,
		invalidPreviews: invalidPreviews,
		missingMaxRuns: missingMaxRuns,
		lowResTiffIssues: lowResTiffIssues,
		missingOrientDapiPreviews: missingOrientDapiPreviews,
		blankPreviews: blankPreviews,
		canSkipToOrient: canSkipToOrient,
		needsPreviewRepair: needsPreviewRepair,
		previewFormatVersion: previewFormatVersion,
		importState: state,
	};
}

// Short-lived memo: the Orient page calls assessOrientPreviewHealth several
// times in one render burst (preview banner + apply-control snapshot + grid),
// each running the full NAS audit (findBlankPreviews / per-slice checks). Cache
// the result for the current synchronous burst and clear it on the next
// microtask, so a burst runs the audit once without ever returning stale health
// beyond that burst.
var _orientHealthMemoRoot = null;
var _orientHealthMemo = null;

function assessOrientPreviewHealth(bundleRoot, cziImport) {
	if (bundleRoot && _orientHealthMemoRoot === bundleRoot && _orientHealthMemo) {
		return _orientHealthMemo;
	}
	cziImport = cziImport || {};
	var audit = auditCziImportCompletion(bundleRoot, cziImport, {});
	var missingOrient = findMissingOrientDapiPreviews(bundleRoot);
	var tiffIn00 = (audit.lowResTiffIssues || []).filter(function (i) {
		return i.kind === "dapi_tif";
	});
	var invalidOrient = (audit.invalidPreviews || []).filter(function (item) {
		return item.role_key === ROLE_DAPI || !item.role_key;
	});
	var blankDapi = (audit.blankPreviews || []).filter(function (item) {
		return item.role_key === ROLE_DAPI;
	});
	var needsRepair =
		tiffIn00.length > 0 ||
		invalidOrient.length > 0 ||
		missingOrient.length > 0 ||
		blankDapi.length > 0;
	var result = {
		audit: audit,
		synced: 0,
		missingOrientDapiPreviews: missingOrient,
		tiffIn00: tiffIn00,
		invalidOrient: invalidOrient,
		missingOrient: missingOrient,
		blankPreviews: audit.blankPreviews || [],
		blankDapi: blankDapi,
		needsRepair: needsRepair,
		canApply: !needsRepair,
	};
	_orientHealthMemoRoot = bundleRoot;
	_orientHealthMemo = result;
	try {
		Promise.resolve().then(function () {
			_orientHealthMemoRoot = null;
			_orientHealthMemo = null;
		});
	} catch (_e) {
		setTimeout(function () {
			_orientHealthMemoRoot = null;
			_orientHealthMemo = null;
		}, 0);
	}
	return result;
}

function findGeometryKeysWithoutPreviewFiles(bundleRoot, geometryMap, sliceIds) {
	var fs = require("fs");
	var orphans = [];
	var keys = {};
	for (var i = 0; i < (sliceIds || []).length; i++) {
		keys[sliceIds[i]] = true;
	}
	for (var key in geometryMap || {}) {
		if (Object.prototype.hasOwnProperty.call(geometryMap, key)) {
			keys[key] = true;
		}
	}
	for (var sliceId in keys) {
		if (!Object.prototype.hasOwnProperty.call(keys, sliceId)) {
			continue;
		}
		var geom = geometryMap && geometryMap[sliceId];
		if (!geom) {
			continue;
		}
		if (!orientGeometry.geometryHasPending(geom)) {
			continue;
		}
		var dapiPng = dapiPreviewPath(bundleRoot, sliceId);
		var orientPng = orientDapiPreviewPath(bundleRoot, sliceId);
		if (!fs.existsSync(dapiPng) && !fs.existsSync(orientPng)) {
			orphans.push(sliceId);
		}
	}
	return orphans;
}

function orientPreviewBannerText(health) {
	if (!health || !health.needsRepair) {
		return "";
	}
	var parts = [];
	if (health.tiffIn00.length) {
		parts.push(
			health.tiffIn00.length +
				" invalid TIFF file(s) in 00_dapi (pipeline folder must be PNG only).",
		);
	}
	if (health.missingOrient.length) {
		parts.push(
			health.missingOrient.length +
				" missing orient DAPI preview(s) under _previews (*_dapi.png).",
		);
	}
	if (health.invalidOrient.length) {
		parts.push(health.invalidOrient.length + " invalid or missing orient preview(s).");
	}
	if (health.blankDapi && health.blankDapi.length) {
		parts.push(
			health.blankDapi.length +
				" blank DAPI preview(s) detected. Use Re-import sections from CZI to re-read from source.",
		);
	}
	if (health.tiffIn00.length || health.missingOrient.length || health.invalidOrient.length) {
		parts.push("Run Repair previews to convert TIFFs, rebuild PNGs, and sync _previews.");
	}
	return parts.join(" ");
}

function sliceOrderEntryForSlice(cziImport, sliceId) {
	var order = (cziImport && cziImport.slice_order) || [];
	for (var i = 0; i < order.length; i++) {
		if (order[i].sliceId === sliceId) {
			return order[i];
		}
	}
	return null;
}

function resolveChannelForSlice(cziImport, sliceId, role) {
	if (!cziImport || !sliceId) {
		return null;
	}
	var orderEntry = sliceOrderEntryForSlice(cziImport, sliceId);
	if (!orderEntry || !orderEntry.path) {
		return null;
	}
	var lookup = buildFilesLookup(cziImport.files || []);
	var channels = cziImport.channels || [];
	var orderPath = String(orderEntry.path);
	for (var c = 0; c < channels.length; c++) {
		var ch = channels[c];
		if (!ch.keep || ch.role !== role) {
			continue;
		}
		var fileRef = resolveFileEntry(ch.file || "", lookup);
		if (fileRef && String(fileRef.path) === orderPath) {
			return {
				channel: ch,
				fileRef: fileRef,
				orderEntry: orderEntry,
			};
		}
	}
	return null;
}

function buildRepairTargetsFromAudit(audit, cziImport) {
	var targets = (audit.invalidPreviews || []).map(function (item) {
		return {
			slice_id: item.slice_id,
			channel_index: item.channel_index,
			role_key: item.role_key,
			file: item.file,
			scene_index: item.scene_index,
			czi_path: item.czi_path,
		};
	});
	var seen = {};
	for (var t = 0; t < targets.length; t++) {
		seen[targets[t].slice_id + ":" + targets[t].channel_index] = true;
	}
	function addDapiTarget(sliceId) {
		var resolved = resolveChannelForSlice(cziImport, sliceId, ROLE_DAPI);
		if (!resolved) {
			return;
		}
		var ch = resolved.channel;
		var key = sliceId + ":" + ch.index;
		if (seen[key]) {
			return;
		}
		seen[key] = true;
		var sceneIndex =
			resolved.orderEntry.scene_index != null ? resolved.orderEntry.scene_index : 0;
		targets.push({
			slice_id: sliceId,
			channel_index: ch.index,
			role_key: ROLE_DAPI,
			file: ch.file,
			scene_index: sceneIndex,
			czi_path: resolved.fileRef.path || "",
		});
	}
	for (var i = 0; i < (audit.lowResTiffIssues || []).length; i++) {
		var issue = audit.lowResTiffIssues[i];
		if (issue.kind === "dapi_tif" && issue.slice_id) {
			addDapiTarget(issue.slice_id);
		}
	}
	for (var m = 0; m < (audit.missingOrientDapiPreviews || []).length; m++) {
		addDapiTarget(audit.missingOrientDapiPreviews[m].slice_id);
	}
	return targets;
}

var BLANK_PREVIEW_MEAN_THRESHOLD = 8;

function computeMeanLumaFromImageData(imageData) {
	if (!imageData || !imageData.data || !imageData.data.length) {
		return 0;
	}
	var data = imageData.data;
	var sum = 0;
	var n = 0;
	for (var i = 0; i < data.length; i += 4) {
		var r = data[i];
		var g = data[i + 1];
		var b = data[i + 2];
		sum += 0.299 * r + 0.587 * g + 0.114 * b;
		n += 1;
	}
	return n ? sum / n : 0;
}

function pngMeanLumaSync(absPath) {
	if (!absPath || !fs.existsSync(absPath)) {
		return null;
	}
	try {
		var zlib = require("zlib");
		var buf = fs.readFileSync(absPath);
		if (buf.length < 24 || buf.readUInt32BE(0) !== 0x89504e47) {
			return null;
		}
		var pos = 8;
		var width = 0;
		var height = 0;
		var colorType = 0;
		var idatParts = [];
		while (pos + 8 <= buf.length) {
			var len = buf.readUInt32BE(pos);
			var type = buf.toString("ascii", pos + 4, pos + 8);
			var dataStart = pos + 8;
			var dataEnd = dataStart + len;
			if (type === "IHDR" && len >= 13) {
				width = buf.readUInt32BE(dataStart);
				height = buf.readUInt32BE(dataStart + 4);
				colorType = buf[dataStart + 9];
			} else if (type === "IDAT") {
				idatParts.push(buf.slice(dataStart, dataEnd));
			} else if (type === "IEND") {
				break;
			}
			pos = dataEnd + 4;
		}
		if (!width || !height || !idatParts.length) {
			return null;
		}
		var inflated = zlib.inflateSync(Buffer.concat(idatParts));
		var bpp = colorType === 6 ? 4 : colorType === 2 ? 3 : colorType === 0 ? 1 : 0;
		if (!bpp) {
			return null;
		}
		var stride = width * bpp;
		var sum = 0;
		var count = 0;
		var off = 0;
		for (var y = 0; y < height; y++) {
			off += 1;
			for (var x = 0; x < width; x++) {
				var r;
				if (bpp === 1) {
					r = inflated[off];
					off += 1;
				} else if (bpp === 3) {
					r = inflated[off];
					off += 3;
				} else {
					r = inflated[off];
					off += 4;
				}
				sum += r;
				count += 1;
			}
		}
		return count ? sum / count : 0;
	} catch (e) {
		return null;
	}
}

function measurePreviewBrightness(absPath) {
	return new Promise(function (resolve) {
		if (!absPath || !fs.existsSync(absPath)) {
			resolve(null);
			return;
		}
		if (typeof Image === "undefined") {
			resolve(pngMeanLumaSync(absPath));
			return;
		}
		var img = new Image();
		img.onload = function () {
			try {
				var canvas = document.createElement("canvas");
				canvas.width = img.width;
				canvas.height = img.height;
				var ctx = canvas.getContext("2d");
				ctx.drawImage(img, 0, 0);
				var data = ctx.getImageData(0, 0, canvas.width, canvas.height);
				resolve(computeMeanLumaFromImageData(data));
			} catch (e) {
				resolve(pngMeanLumaSync(absPath));
			}
		};
		img.onerror = function () {
			resolve(pngMeanLumaSync(absPath));
		};
		img.src = url.pathToFileURL(absPath).href;
	});
}

function collectSliceIdsFromImport(cziImport) {
	var ids = [];
	var seen = {};
	var items = iterKeptChannelScenes(cziImport || {});
	for (var i = 0; i < items.length; i++) {
		var sid = items[i].slice_id;
		if (sid && !seen[sid]) {
			seen[sid] = true;
			ids.push(sid);
		}
	}
	ids.sort(function (a, b) {
		return naturalCompare({ sliceId: a }, { sliceId: b }, cziImport);
	});
	return ids;
}

function buildRepairTargetsForSelection(cziImport, sliceIds, roleKeys) {
	var sliceSet = {};
	for (var i = 0; i < (sliceIds || []).length; i++) {
		sliceSet[sliceIds[i]] = true;
	}
	var roleSet = null;
	if (roleKeys && roleKeys.length) {
		roleSet = {};
		for (var r = 0; r < roleKeys.length; r++) {
			roleSet[roleKeys[r]] = true;
		}
	}
	return iterKeptChannelScenes(cziImport)
		.filter(function (item) {
			if (!sliceSet[item.slice_id]) {
				return false;
			}
			if (roleSet && !roleSet[item.role_key]) {
				return false;
			}
			return true;
		})
		.map(function (item) {
			return {
				slice_id: item.slice_id,
				channel_index: item.channel_index,
				role_key: item.role_key,
				file: item.file,
				scene_index: item.scene_index,
				czi_path: item.czi_path,
			};
		});
}

function maxTifPathForReimport(bundleRoot, roleKey, sliceId, project) {
	// Prefer the role-specific max run; the generic active_runs.max is the
	// PRIMARY signal's run, so using it for nuclei/axons/other would resolve the
	// wrong 03_max branch. Fall back to active max only when this role has no
	// registered run (e.g. legacy single-signal imports).
	var rel = "";
	if (project && project.settings && project.settings.czi_import) {
		var maxRuns = project.settings.czi_import.max_runs || {};
		rel = maxRuns[roleKey] || "";
	}
	if (!rel && project && project.processing && project.processing.active_runs) {
		rel = project.processing.active_runs.max || "";
	}
	if (!rel) {
		return "";
	}
	return path.join(bundleRoot, "data/counting/03_max", rel, sliceId + ".tif");
}

function listReimportOutputPaths(bundleRoot, item, cziImport, project) {
	var paths = [];
	var zPath = originalScansPath(bundleRoot, item.channel, item.slice_id);
	if (zPath) {
		paths.push(zPath);
	}
	var prevPath = orientPreviewPathForChannel(bundleRoot, item.slice_id, item.channel);
	if (prevPath) {
		paths.push(prevPath);
	}
	if (item.role_key === ROLE_DAPI) {
		paths.push(dapiPreviewPath(bundleRoot, item.slice_id));
	}
	if (isSignalChannel(item.channel)) {
		var maxTif = maxTifPathForReimport(bundleRoot, item.role_key, item.slice_id, project);
		if (maxTif) {
			paths.push(maxTif);
		}
	}
	var unique = [];
	var seen = {};
	for (var i = 0; i < paths.length; i++) {
		var p = paths[i];
		if (p && !seen[p]) {
			seen[p] = true;
			unique.push(p);
		}
	}
	return unique;
}

function validateReimportSources(targets) {
	var missing = [];
	for (var i = 0; i < (targets || []).length; i++) {
		var t = targets[i];
		var cziPath = t.czi_path || "";
		if (!cziPath || !fs.existsSync(cziPath)) {
			missing.push({ slice_id: t.slice_id, czi_path: cziPath, role_key: t.role_key });
		}
	}
	return { ok: missing.length === 0, missing: missing };
}

function findBlankPreviews(bundleRoot, cziImport, opts) {
	opts = opts || {};
	var threshold = opts.threshold != null ? opts.threshold : BLANK_PREVIEW_MEAN_THRESHOLD;
	var sliceSet = null;
	if (opts.sliceIds && opts.sliceIds.length) {
		sliceSet = {};
		for (var si = 0; si < opts.sliceIds.length; si++) {
			sliceSet[opts.sliceIds[si]] = true;
		}
	}
	var roleSet = null;
	if (opts.roleKeys && opts.roleKeys.length) {
		roleSet = {};
		for (var ri = 0; ri < opts.roleKeys.length; ri++) {
			roleSet[opts.roleKeys[ri]] = true;
		}
	}
	var meansMap = opts.meansMap || null;
	var previewFormatVersion =
		(cziImport && cziImport.preview_format_version) || PREVIEW_FORMAT_VERSION;
	var blanks = [];
	var items = iterKeptChannelScenes(cziImport || {});
	for (var i = 0; i < items.length; i++) {
		var item = items[i];
		if (sliceSet && !sliceSet[item.slice_id]) {
			continue;
		}
		if (roleSet && !roleSet[item.role_key]) {
			continue;
		}
		var prevPath = orientPreviewPathForChannel(bundleRoot, item.slice_id, item.channel);
		if (!prevPath || !isPreviewFileValid(prevPath, previewFormatVersion)) {
			continue;
		}
		var mean = null;
		if (meansMap) {
			mean = meansMap[prevPath];
			if (mean == null) {
				mean = meansMap[item.slice_id + ":" + item.role_key];
			}
		} else {
			mean = pngMeanLumaSync(prevPath);
		}
		if (mean == null || mean >= threshold) {
			continue;
		}
		blanks.push({
			slice_id: item.slice_id,
			role_key: item.role_key,
			channel_index: item.channel_index,
			mean: mean,
			path: prevPath,
		});
	}
	return blanks;
}

function findBlankPreviewsAsync(bundleRoot, cziImport, opts) {
	opts = opts || {};
	var threshold = opts.threshold != null ? opts.threshold : BLANK_PREVIEW_MEAN_THRESHOLD;
	var sliceSet = null;
	if (opts.sliceIds && opts.sliceIds.length) {
		sliceSet = {};
		for (var si = 0; si < opts.sliceIds.length; si++) {
			sliceSet[opts.sliceIds[si]] = true;
		}
	}
	var roleSet = null;
	if (opts.roleKeys && opts.roleKeys.length) {
		roleSet = {};
		for (var ri = 0; ri < opts.roleKeys.length; ri++) {
			roleSet[opts.roleKeys[ri]] = true;
		}
	}
	var previewFormatVersion =
		(cziImport && cziImport.preview_format_version) || PREVIEW_FORMAT_VERSION;
	var items = iterKeptChannelScenes(cziImport || {});
	var jobs = [];
	for (var i = 0; i < items.length; i++) {
		var item = items[i];
		if (sliceSet && !sliceSet[item.slice_id]) {
			continue;
		}
		if (roleSet && !roleSet[item.role_key]) {
			continue;
		}
		var prevPath = orientPreviewPathForChannel(bundleRoot, item.slice_id, item.channel);
		if (!prevPath || !isPreviewFileValid(prevPath, previewFormatVersion)) {
			continue;
		}
		jobs.push({ item: item, prevPath: prevPath });
	}
	return Promise.all(
		jobs.map(function (job) {
			return measurePreviewBrightness(job.prevPath).then(function (mean) {
				return { job: job, mean: mean };
			});
		}),
	).then(function (results) {
		var blanks = [];
		for (var r = 0; r < results.length; r++) {
			var mean = results[r].mean;
			if (mean == null || mean >= threshold) {
				continue;
			}
			var item = results[r].job.item;
			blanks.push({
				slice_id: item.slice_id,
				role_key: item.role_key,
				channel_index: item.channel_index,
				mean: mean,
				path: results[r].job.prevPath,
			});
		}
		return blanks;
	});
}

function buildReextractGeometryScope(repairTargets) {
	var scope = {};
	for (var i = 0; i < (repairTargets || []).length; i++) {
		var t = repairTargets[i];
		var sid = t && t.slice_id;
		var roleKey = t && t.role_key;
		if (!sid || !roleKey) {
			continue;
		}
		if (!scope[sid]) {
			scope[sid] = [];
		}
		if (scope[sid].indexOf(roleKey) < 0) {
			scope[sid].push(roleKey);
		}
	}
	return scope;
}

function displayChannelKeyForRoleKey(roleKey) {
	if (!roleKey) {
		return "";
	}
	if (roleKey === ROLE_DAPI) {
		return ORIENT_DISPLAY_DAPI;
	}
	var branch = branchForRole(roleKey);
	if (branch) {
		return branch;
	}
	if (roleKey.indexOf("other:") === 0) {
		return roleKey.slice(6);
	}
	return roleKey;
}

function roleKeysMatchingDisplayChannel(displayKey, cziImport) {
	if (!displayKey || displayKey === ORIENT_DISPLAY_DAPI) {
		return [ROLE_DAPI];
	}
	var out = [];
	var roleKeys = Object.keys(ROLE_TO_BRANCH);
	for (var i = 0; i < roleKeys.length; i++) {
		if (ROLE_TO_BRANCH[roleKeys[i]] === displayKey) {
			out.push(roleKeys[i]);
		}
	}
	var ch = findKeptChannelForDisplayKey(cziImport || {}, displayKey);
	if (ch) {
		var rk = roleKeyForChannel(ch);
		if (out.indexOf(rk) < 0) {
			out.push(rk);
		}
	}
	if (out.indexOf(displayKey) < 0) {
		out.push(displayKey);
	}
	var otherKey = "other:" + displayKey;
	if (out.indexOf(otherKey) < 0) {
		out.push(otherKey);
	}
	return out;
}

function unionDisplayChannelKeysFromScope(scope) {
	var seen = {};
	var out = [];
	var sliceIds = Object.keys(scope || {});
	for (var i = 0; i < sliceIds.length; i++) {
		var roles = scope[sliceIds[i]] || [];
		for (var r = 0; r < roles.length; r++) {
			var dk = displayChannelKeyForRoleKey(roles[r]);
			if (dk && !seen[dk]) {
				seen[dk] = true;
				out.push(dk);
			}
		}
	}
	return out;
}

function sliceIdsInReextractScope(scope) {
	var out = [];
	var keys = Object.keys(scope || {});
	for (var i = 0; i < keys.length; i++) {
		if (scope[keys[i]] && scope[keys[i]].length) {
			out.push(keys[i]);
		}
	}
	return out;
}

function isDisplayChannelInReextractScope(sliceId, displayKey, scope, cziImport) {
	if (!scope || !sliceId || !displayKey) {
		return false;
	}
	var roles = scope[sliceId];
	if (!roles || !roles.length) {
		return false;
	}
	var candidates = roleKeysMatchingDisplayChannel(displayKey, cziImport);
	for (var i = 0; i < roles.length; i++) {
		if (candidates.indexOf(roles[i]) >= 0) {
			return true;
		}
		if (displayChannelKeyForRoleKey(roles[i]) === displayKey) {
			return true;
		}
	}
	return false;
}

function listOrientDisplayChannelsForReextract(bundleRoot, cziImport, scope) {
	var all = listOrientDisplayChannels(bundleRoot, cziImport);
	var enabledSet = {};
	var enabledKeys = unionDisplayChannelKeysFromScope(scope);
	for (var i = 0; i < enabledKeys.length; i++) {
		enabledSet[enabledKeys[i]] = true;
	}
	var out = [];
	for (var c = 0; c < all.length; c++) {
		out.push({
			key: all[c].key,
			label: all[c].label,
			enabled: !!enabledSet[all[c].key],
		});
	}
	return out;
}

function firstEnabledReextractDisplayChannel(bundleRoot, cziImport, scope) {
	var list = listOrientDisplayChannelsForReextract(bundleRoot, cziImport, scope);
	for (var i = 0; i < list.length; i++) {
		if (list[i].enabled) {
			return list[i].key;
		}
	}
	return null;
}

function buildReextractConfig(cziImport, targets, project) {
	var payload = JSON.parse(JSON.stringify(cziImport || {}));
	payload.repair_mode = "reextract";
	payload.repair_targets = (targets || []).slice();
	var maxRuns = Object.assign({}, payload.max_runs || {});
	if (project && project.processing && project.processing.active_runs) {
		var activeMax = project.processing.active_runs.max;
		if (activeMax) {
			var primary =
				payload.primary_signal_role ||
				cziImport.primary_signal_role ||
				ROLE_SIGNAL_SOMATA;
			if (!maxRuns[primary]) {
				maxRuns[primary] = activeMax;
			}
		}
	}
	payload.max_runs = maxRuns;
	payload.config_fingerprint = cziImportFingerprint(payload);
	return payload;
}

function listKeptChannelsForReimport(cziImport) {
	var channels = (cziImport && cziImport.channels) || [];
	var out = [];
	var seen = {};
	for (var i = 0; i < channels.length; i++) {
		var ch = channels[i];
		if (!ch.keep || ch.role === ROLE_UNUSED) {
			continue;
		}
		if (ch.role === ROLE_OTHER && !sanitizeOtherName(ch.other_name)) {
			continue;
		}
		var key = roleKeyForChannel(ch);
		if (seen[key]) {
			continue;
		}
		seen[key] = true;
		out.push({
			channel: ch,
			role_key: key,
			label: displayChannelLabelForChannel(ch),
			index: ch.index,
		});
	}
	return out;
}

module.exports = {
	ROLE_DAPI: ROLE_DAPI,
	ROLE_SIGNAL_SOMATA: ROLE_SIGNAL_SOMATA,
	ROLE_SIGNAL_NUCLEI: ROLE_SIGNAL_NUCLEI,
	ROLE_SIGNAL_AXONS: ROLE_SIGNAL_AXONS,
	ROLE_OTHER: ROLE_OTHER,
	ROLE_UNUSED: ROLE_UNUSED,
	ROLE_TO_BRANCH: ROLE_TO_BRANCH,
	CHANNEL_ROLE_OPTIONS: CHANNEL_ROLE_OPTIONS,
	DEFAULT_PREVIEW_SCALE: DEFAULT_PREVIEW_SCALE,
	SLICE_NUMBERING_PRESERVE: SLICE_NUMBERING_PRESERVE,
	SLICE_NUMBERING_RENAME: SLICE_NUMBERING_RENAME,
	branchForRole: branchForRole,
	sanitizeOtherName: sanitizeOtherName,
	sanitizeSliceStem: sanitizeSliceStem,
	parseSectionSuffix: parseSectionSuffix,
	naturalSortTokens: naturalSortTokens,
	compareTokens: compareTokens,
	entrySceneIndex: entrySceneIndex,
	entryScanIndex: entryScanIndex,
	buildFilesLookup: buildFilesLookup,
	resolveFileEntry: resolveFileEntry,
	canonicalSourceDir: canonicalSourceDir,
	normalizeSourceDirs: normalizeSourceDirs,
	naturalSortKey: naturalSortKey,
	naturalCompare: naturalCompare,
	stemFromBasename: stemFromBasename,
	parseSectionWithIdentifier: parseSectionWithIdentifier,
	detectSectionIdentifierCandidates: detectSectionIdentifierCandidates,
	defaultSectionIdentifier: defaultSectionIdentifier,
	branchForChannel: branchForChannel,
	roleKeyForChannel: roleKeyForChannel,
	isSignalChannel: isSignalChannel,
	collectChannelIndices: collectChannelIndices,
	applyChannelDefaults: applyChannelDefaults,
	defaultSliceId: defaultSliceId,
	suggestRoleFromLabel: suggestRoleFromLabel,
	buildDefaultCziImport: buildDefaultCziImport,
	mergeProbeIntoImport: mergeProbeIntoImport,
	mergeProbeDirIntoImport: mergeProbeDirIntoImport,
	buildSliceOrder: buildSliceOrder,
	syncScenesFromSliceOrder: syncScenesFromSliceOrder,
	validateSliceOrder: validateSliceOrder,
	collectKeptSignalRoleKeys: collectKeptSignalRoleKeys,
	importConfigPath: importConfigPath,
	maxRunRelForRole: maxRunRelForRole,
	collectSliceIds: collectSliceIds,
	countExtractWorkItems: countExtractWorkItems,
	primaryMaxRunRel: primaryMaxRunRel,
	collectMosaicWarnings: collectMosaicWarnings,
	collectChannelProbeWarnings: collectChannelProbeWarnings,
	collectMosaicInfo: collectMosaicInfo,
	hasLikelyUnstitchedMosaic: hasLikelyUnstitchedMosaic,
	PREVIEWS_REL: PREVIEWS_REL,
	PREVIEW_FORMAT_VERSION: PREVIEW_FORMAT_VERSION,
	ORIENT_DISPLAY_DAPI: ORIENT_DISPLAY_DAPI,
	displayChannelKeyForChannel: displayChannelKeyForChannel,
	displayChannelLabelForChannel: displayChannelLabelForChannel,
	listOrientDisplayChannels: listOrientDisplayChannels,
	originalScansPath: originalScansPath,
	dapiPreviewPath: dapiPreviewPath,
	orientDapiPreviewPath: orientDapiPreviewPath,
	signalPreviewPath: signalPreviewPath,
	previewPathForChannel: previewPathForChannel,
	orientPreviewPathForChannel: orientPreviewPathForChannel,
	resolveOrientPreviewPath: resolveOrientPreviewPath,
	cziImportFingerprint: cziImportFingerprint,
	importStatePath: importStatePath,
	readImportState: readImportState,
	findLowResTiffIssues: findLowResTiffIssues,
	findMissingOrientDapiPreviews: findMissingOrientDapiPreviews,
	ensureOrientDapiPreviewsFromPipeline: ensureOrientDapiPreviewsFromPipeline,
	assessOrientPreviewHealth: assessOrientPreviewHealth,
	findGeometryKeysWithoutPreviewFiles: findGeometryKeysWithoutPreviewFiles,
	orientPreviewBannerText: orientPreviewBannerText,
	auditCziImportCompletion: auditCziImportCompletion,
	auditMaxRunsOnly: auditMaxRunsOnly,
	sliceOrderEntryForSlice: sliceOrderEntryForSlice,
	resolveChannelForSlice: resolveChannelForSlice,
	buildRepairTargetsFromAudit: buildRepairTargetsFromAudit,
	iterKeptChannelScenes: iterKeptChannelScenes,
	BLANK_PREVIEW_MEAN_THRESHOLD: BLANK_PREVIEW_MEAN_THRESHOLD,
	computeMeanLumaFromImageData: computeMeanLumaFromImageData,
	pngMeanLumaSync: pngMeanLumaSync,
	measurePreviewBrightness: measurePreviewBrightness,
	collectSliceIdsFromImport: collectSliceIdsFromImport,
	buildRepairTargetsForSelection: buildRepairTargetsForSelection,
	buildReextractGeometryScope: buildReextractGeometryScope,
	displayChannelKeyForRoleKey: displayChannelKeyForRoleKey,
	unionDisplayChannelKeysFromScope: unionDisplayChannelKeysFromScope,
	sliceIdsInReextractScope: sliceIdsInReextractScope,
	isDisplayChannelInReextractScope: isDisplayChannelInReextractScope,
	listOrientDisplayChannelsForReextract: listOrientDisplayChannelsForReextract,
	firstEnabledReextractDisplayChannel: firstEnabledReextractDisplayChannel,
	listReimportOutputPaths: listReimportOutputPaths,
	validateReimportSources: validateReimportSources,
	findBlankPreviews: findBlankPreviews,
	findBlankPreviewsAsync: findBlankPreviewsAsync,
	buildReextractConfig: buildReextractConfig,
	listKeptChannelsForReimport: listKeptChannelsForReimport,
	isPreviewFileValid: isPreviewFileValid,
};
