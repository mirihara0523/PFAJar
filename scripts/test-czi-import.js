"use strict";

var assert = require("assert");
var fs = require("fs");
var os = require("os");
var path = require("path");
var url = require("url");
var { execFileSync } = require("child_process");
var cziImport = require("../js/czi_import");

function testDefaultSliceId() {
	assert.strictEqual(cziImport.defaultSliceId("M528.czi", 0, 1), "M528");
	assert.strictEqual(cziImport.defaultSliceId("M528.czi", 1, 2), "M528_s001");
}

function testSuggestRole() {
	assert.strictEqual(cziImport.suggestRoleFromLabel("DAPI-405"), cziImport.ROLE_DAPI);
	assert.strictEqual(
		cziImport.suggestRoleFromLabel("Rabies"),
		cziImport.ROLE_SIGNAL_SOMATA,
	);
}

function testMergeProbe() {
	var imp = cziImport.buildDefaultCziImport("/scans");
	var probe = {
		files: [
			{
				path: "/scans/M528.czi",
				basename: "M528.czi",
				scenes: [{ index: 0, sliceId: "M528" }],
				channels: [
					{ index: 0, label: "DAPI", suggested_role: "dapi" },
					{ index: 1, label: "Unknown", suggested_role: "unused" },
				],
			},
		],
	};
	cziImport.mergeProbeIntoImport(imp, probe);
	assert.strictEqual(imp.channels.length, 2);
	assert.strictEqual(imp.channels[0].role, cziImport.ROLE_DAPI);
	assert.strictEqual(imp.channels[0].keep, true);
	assert.strictEqual(imp.channels[1].keep, true);
	assert.strictEqual(imp.channel_defaults["0"].role, cziImport.ROLE_DAPI);
	assert.strictEqual(imp.channel_defaults["1"].role, cziImport.ROLE_UNUSED);
}

function testSanitizeOtherName() {
	assert.strictEqual(cziImport.sanitizeOtherName(" rabies red "), "rabies_red");
	assert.strictEqual(cziImport.sanitizeOtherName("bad/name"), "badname");
	assert.strictEqual(cziImport.sanitizeOtherName(""), null);
	assert.strictEqual(cziImport.sanitizeOtherName("!!!"), null);
}

function testBranchForChannelOther() {
	var ch = { role: cziImport.ROLE_OTHER, other_name: "rabies_red" };
	assert.strictEqual(cziImport.branchForChannel(ch), "rabies_red");
	assert.strictEqual(cziImport.roleKeyForChannel(ch), "other:rabies_red");
}

function testApplyChannelDefaults() {
	var imp = {
		channel_defaults: {},
		channels: [
			{ file: "A.czi", index: 0, role: cziImport.ROLE_UNUSED, keep: true },
			{ file: "B.czi", index: 0, role: cziImport.ROLE_UNUSED, keep: true },
			{ file: "A.czi", index: 1, role: cziImport.ROLE_DAPI, keep: true },
		],
	};
	cziImport.applyChannelDefaults(imp, 0, {
		role: cziImport.ROLE_OTHER,
		other_name: "custom_sig",
	});
	assert.strictEqual(imp.channels[0].role, cziImport.ROLE_OTHER);
	assert.strictEqual(imp.channels[0].other_name, "custom_sig");
	assert.strictEqual(imp.channels[1].role, cziImport.ROLE_OTHER);
	assert.strictEqual(imp.channels[2].role, cziImport.ROLE_DAPI);
	assert.strictEqual(imp.channel_defaults["0"].other_name, "custom_sig");
}

function testCollectChannelIndices() {
	var imp = {
		channels: [{ index: 2 }, { index: 0 }, { index: 1 }, { index: 0 }],
	};
	assert.deepStrictEqual(cziImport.collectChannelIndices(imp), [0, 1, 2]);
}

function testMaxRunRel() {
	assert.strictEqual(
		cziImport.maxRunRelForRole(cziImport.ROLE_SIGNAL_SOMATA, "M528-M529"),
		"somata/max/M528-M529",
	);
	assert.strictEqual(
		cziImport.maxRunRelForRole("other:rabies_red", "M528"),
		"rabies_red/max/M528",
	);
}

function testCollectSliceIds() {
	var imp = {
		slice_order: [
			{ ordinal: 1, sliceId: "M528_s061" },
			{ ordinal: 2, sliceId: "M528_s062" },
		],
		files: [{ scenes: [{ sliceId: "M528_s062" }, { sliceId: "M528_s061" }] }],
	};
	assert.deepStrictEqual(cziImport.collectSliceIds(imp), ["M528_s061", "M528_s062"]);
}

function testNaturalCompareSectionOrder() {
	var ids = ["M528_s100", "M528_s20", "M528_s9", "M528_s112"];
	ids.sort(function (a, b) {
		return cziImport.naturalCompare({ sliceId: a }, { sliceId: b });
	});
	assert.deepStrictEqual(ids, ["M528_s9", "M528_s20", "M528_s100", "M528_s112"]);
}

function testNaturalCompareParenSuffix() {
	var ids = ["M467(100)", "M467(101)", "M467(57)", "M467(58)", "M467(99)", "M467(108)"];
	ids.sort(function (a, b) {
		return cziImport.naturalCompare({ sliceId: a }, { sliceId: b });
	});
	assert.deepStrictEqual(ids, [
		"M467(57)",
		"M467(58)",
		"M467(99)",
		"M467(100)",
		"M467(101)",
		"M467(108)",
	]);
}

function testNaturalCompareMixedWidths() {
	var ids = ["M528_100", "M528_10", "M528_1", "M528_2"];
	ids.sort(function (a, b) {
		return cziImport.naturalCompare({ sliceId: a }, { sliceId: b });
	});
	assert.deepStrictEqual(ids, ["M528_1", "M528_2", "M528_10", "M528_100"]);
}

function testNaturalCompareNoTrailingDigit() {
	var ids = ["M467(57)", "M467(58)", "Brain (1)", "Brain (10)", "Brain (2)"];
	ids.sort(function (a, b) {
		return cziImport.naturalCompare({ sliceId: a }, { sliceId: b });
	});
	assert.deepStrictEqual(ids, ["Brain (1)", "Brain (2)", "Brain (10)", "M467(57)", "M467(58)"]);
}

function testDetectIdentifierM467() {
	var files = [
		{ basename: "M467(57).czi" },
		{ basename: "M467(100).czi" },
		{ basename: "M467(9).czi" },
	];
	var candidates = cziImport.detectSectionIdentifierCandidates(files);
	var prefixed = candidates.filter(function (c) {
		return c.prefix;
	});
	assert.ok(prefixed.length >= 1);
	assert.strictEqual(prefixed[0].prefix, "M467(");
	assert.strictEqual(prefixed[0].matchCount, 3);
}

function testDetectIdentifierScanFile() {
	var files = [{ basename: "scan1.file.001.czi" }, { basename: "scan2.file.010.czi" }];
	var candidates = cziImport.detectSectionIdentifierCandidates(files);
	var prefixes = candidates.map(function (c) {
		return c.prefix;
	});
	assert.ok(prefixes.indexOf("file.") >= 0);
	var fileDot = candidates.find(function (c) {
		return c.prefix === "file.";
	});
	assert.ok(fileDot);
	assert.strictEqual(fileDot.matchCount, 2);
}

function testSortWithIdentifier() {
	var imp = cziImport.buildDefaultCziImport("");
	imp.section_identifier = "M467(";
	imp.files = [
		{
			path: "/a/M467(57).czi",
			basename: "M467(57).czi",
			scene_count: 1,
			scenes: [{ index: 0, sliceId: "M467(57)", originalSliceId: "M467(57)" }],
		},
		{
			path: "/b/M467(100).czi",
			basename: "M467(100).czi",
			scene_count: 1,
			scenes: [{ index: 0, sliceId: "M467(100)", originalSliceId: "M467(100)" }],
		},
		{
			path: "/c/M467(9).czi",
			basename: "M467(9).czi",
			scene_count: 1,
			scenes: [{ index: 0, sliceId: "M467(9)", originalSliceId: "M467(9)" }],
		},
	];
	cziImport.buildSliceOrder(imp, "M467");
	var order = imp.slice_order.map(function (e) {
		return e.originalSliceId;
	});
	assert.deepStrictEqual(order, ["M467(9)", "M467(57)", "M467(100)"]);
}

function testBuildSliceOrderSingleSceneMultiZ() {
	var imp = cziImport.buildDefaultCziImport("");
	imp.slice_numbering = cziImport.SLICE_NUMBERING_RENAME;
	imp.files = [
		{
			path: "/a/M467(57).czi",
			basename: "M467(57).czi",
			scene_count: 1,
			z_count: 57,
			channel_count: 4,
			scenes: [{ index: 0, sliceId: "M467(57)", originalSliceId: "M467(57)" }],
		},
	];
	cziImport.buildSliceOrder(imp, "M467");
	assert.strictEqual(imp.slice_order.length, 1);
	assert.strictEqual(imp.slice_order[0].sliceId, "M467_s001");
}

function testBuildSliceOrderRenameMultiDir() {
	var imp = cziImport.buildDefaultCziImport("");
	var makeFile = function (dirPath, basename, scanIndex) {
		return {
			path: dirPath + "/" + basename,
			basename: basename,
			source_dir: dirPath,
			scan_index: scanIndex,
			scene_count: 1,
			channels: [{ index: 0, label: "DAPI" }],
			scenes: [{ index: 0, sliceId: basename.replace(/\.czi$/i, ""), originalSliceId: basename.replace(/\.czi$/i, "") }],
		};
	};
	cziImport.mergeProbeDirIntoImport(
		imp,
		{
			files: [
				makeFile("/scan1", "A.czi", 0),
				makeFile("/scan1", "B.czi", 0),
			],
		},
		"/scan1",
		0,
	);
	cziImport.mergeProbeDirIntoImport(
		imp,
		{
			files: [
				makeFile("/scan2", "C.czi", 1),
				makeFile("/scan2", "D.czi", 1),
			],
		},
		"/scan2",
		1,
	);
	imp.slice_numbering = cziImport.SLICE_NUMBERING_RENAME;
	cziImport.buildSliceOrder(imp, "M528");
	assert.strictEqual(imp.slice_order.length, 4);
	assert.strictEqual(imp.slice_order[0].sliceId, "M528_s001");
	assert.strictEqual(imp.slice_order[0].scan_index, 0);
	assert.strictEqual(imp.slice_order[2].scan_index, 1);
	assert.strictEqual(imp.slice_order[3].sliceId, "M528_s004");
}

function testBuildSliceOrderTwoDirsDuplicateNames() {
	var imp = cziImport.buildDefaultCziImport("");
	imp.section_identifier = "M514(";
	var makeFile = function (dirPath, n, scanIndex) {
		var basename = "M514(" + n + ").czi";
		var stem = "M514(" + n + ")";
		return {
			path: dirPath + "/" + basename,
			basename: basename,
			source_dir: dirPath,
			scan_index: scanIndex,
			scene_count: 1,
			scenes: [{ index: 0, sliceId: stem, originalSliceId: stem }],
		};
	};
	var makeStemFile = function (dirPath, scanIndex) {
		return {
			path: dirPath + "/M514.czi",
			basename: "M514.czi",
			source_dir: dirPath,
			scan_index: scanIndex,
			scene_count: 1,
			scenes: [{ index: 0, sliceId: "M514", originalSliceId: "M514" }],
		};
	};
	var dir0Files = [];
	var dir1Files = [];
	for (var n = 1; n <= 10; n++) {
		dir0Files.push(makeFile("/day1", n, 0));
		dir1Files.push(makeFile("/day2", n, 1));
	}
	dir0Files.push(makeStemFile("/day1", 0));
	dir1Files.push(makeStemFile("/day2", 1));
	cziImport.mergeProbeDirIntoImport(imp, { files: dir0Files }, "/day1", 0);
	cziImport.mergeProbeDirIntoImport(imp, { files: dir1Files }, "/day2", 1);
	imp.slice_numbering = cziImport.SLICE_NUMBERING_RENAME;
	cziImport.buildSliceOrder(imp, "M514");
	assert.strictEqual(imp.slice_order.length, 22);
	for (var i = 0; i < imp.slice_order.length; i++) {
		assert.strictEqual(imp.slice_order[i].sliceId, "M514_s" + String(i + 1).padStart(3, "0"));
	}
	for (var j = 0; j < 11; j++) {
		assert.strictEqual(imp.slice_order[j].scan_index, 0);
	}
	for (var k = 11; k < 22; k++) {
		assert.strictEqual(imp.slice_order[k].scan_index, 1);
	}
	var firstBatchSections = imp.slice_order.slice(0, 11).map(function (e) {
		return e.originalSliceId;
	});
	assert.deepStrictEqual(
		firstBatchSections.slice(0, 10),
		["M514(1)", "M514(2)", "M514(3)", "M514(4)", "M514(5)", "M514(6)", "M514(7)", "M514(8)", "M514(9)", "M514(10)"],
	);
	assert.strictEqual(firstBatchSections[10], "M514");
	var secondBatchFirst = imp.slice_order[11].originalSliceId;
	assert.strictEqual(secondBatchFirst, "M514(1)");
	assert.notStrictEqual(imp.slice_order[0].path, imp.slice_order[11].path);
}

function testNormalizeSourceDirsPreservesOrder() {
	var imp = { source_dirs: ["/z/second", "/a/first"] };
	assert.deepStrictEqual(cziImport.normalizeSourceDirs(imp), [
		path.resolve("/z/second"),
		path.resolve("/a/first"),
	]);
}

function testCanonicalSourceDir() {
	assert.strictEqual(
		cziImport.canonicalSourceDir("/foo/bar"),
		path.resolve("/foo/bar"),
	);
	assert.strictEqual(
		cziImport.canonicalSourceDir("/foo/bar/"),
		path.resolve("/foo/bar"),
	);
}

function testMergeProbeDirCanonicalReplace() {
	var imp = cziImport.buildDefaultCziImport("");
	var day2Root = path.join("/tmp", "day2");
	var makeFile = function (filePath) {
		return {
			path: filePath,
			basename: path.basename(filePath),
			scene_count: 1,
			scenes: [{ index: 0, sliceId: "s1", originalSliceId: "s1" }],
		};
	};
	var day2a = path.join(day2Root, "A.czi");
	var day2b = path.join(day2Root, "B.czi");
	cziImport.mergeProbeDirIntoImport(
		imp,
		{ files: [makeFile(day2a)] },
		day2Root + path.sep,
		1,
	);
	assert.strictEqual(imp.files.length, 1);
	assert.strictEqual(imp.files[0].path, day2a);
	cziImport.mergeProbeDirIntoImport(
		imp,
		{ files: [makeFile(day2b)] },
		day2Root,
		1,
	);
	assert.strictEqual(imp.files.length, 1);
	assert.strictEqual(imp.files[0].path, day2b);
	assert.strictEqual(cziImport.canonicalSourceDir(imp.files[0].source_dir), path.resolve(day2Root));
}

function testResyncScanIndicesCanonicalPaths() {
	var dirs = [path.resolve("/day1"), path.resolve("/day2")];
	var files = [
		{
			path: "/day1/M514(1).czi",
			basename: "M514(1).czi",
			source_dir: "/day1",
			scan_index: 0,
		},
		{
			path: path.join("/day2", "M514(1).czi"),
			basename: "M514(1).czi",
			source_dir: "/day2/",
			scan_index: 0,
		},
	];
	for (var i = 0; i < files.length; i++) {
		var canon = cziImport.canonicalSourceDir(files[i].source_dir);
		files[i].source_dir = canon;
		var idx = dirs.indexOf(canon);
		files[i].scan_index = idx >= 0 ? idx : 0;
	}
	assert.strictEqual(files[0].scan_index, 0);
	assert.strictEqual(files[1].scan_index, 1);
	assert.strictEqual(files[1].source_dir, path.resolve("/day2"));
}

function testChannelPathKeysDuplicateBasenames() {
	var imp = cziImport.buildDefaultCziImport("");
	cziImport.mergeProbeDirIntoImport(
		imp,
		{
			files: [
				{
					path: "/day1/M514(1).czi",
					basename: "M514(1).czi",
					channels: [{ index: 0, label: "DAPI" }],
					scenes: [{ index: 0 }],
				},
			],
		},
		"/day1",
		0,
	);
	cziImport.mergeProbeDirIntoImport(
		imp,
		{
			files: [
				{
					path: "/day2/M514(1).czi",
					basename: "M514(1).czi",
					channels: [{ index: 0, label: "DAPI" }],
					scenes: [{ index: 0 }],
				},
			],
		},
		"/day2",
		1,
	);
	assert.strictEqual(imp.channels[0].file, "/day1/M514(1).czi");
	assert.strictEqual(imp.channels[1].file, "/day2/M514(1).czi");
	var lookup = cziImport.buildFilesLookup(imp.files);
	assert.strictEqual(
		cziImport.resolveFileEntry("/day1/M514(1).czi", lookup).path,
		"/day1/M514(1).czi",
	);
	assert.strictEqual(
		cziImport.resolveFileEntry("/day2/M514(1).czi", lookup).path,
		"/day2/M514(1).czi",
	);
	assert.strictEqual(cziImport.resolveFileEntry("M514(1).czi", lookup), null);
}

function testValidateSliceOrderDuplicate() {
	var imp = {
		slice_order: [
			{ ordinal: 1, sliceId: "M528_s001", path: "/a.czi", scene_index: 0 },
			{ ordinal: 2, sliceId: "M528_s001", path: "/b.czi", scene_index: 0 },
		],
		files: [],
	};
	assert.match(cziImport.validateSliceOrder(imp), /Duplicate slice ID/);
}

function testCollectKeptSignalRoleKeys() {
	var imp = {
		channels: [
			{ role: cziImport.ROLE_DAPI, keep: true },
			{ role: cziImport.ROLE_SIGNAL_SOMATA, keep: true },
			{ role: cziImport.ROLE_SIGNAL_NUCLEI, keep: false },
			{
				role: cziImport.ROLE_OTHER,
				other_name: "rabies_red",
				keep: true,
			},
		],
	};
	assert.deepStrictEqual(cziImport.collectKeptSignalRoleKeys(imp), [
		cziImport.ROLE_SIGNAL_SOMATA,
		"other:rabies_red",
	]);
}

function testImportConfigPath() {
	var p = cziImport.importConfigPath("/tmp/Brain_masonjar");
	assert.ok(p.endsWith(path.join(".masonjar", "czi_import_config.json")));
}

function testCollectMosaicWarnings() {
	var files = [
		{
			basename: "tile.czi",
			likely_unstitched: true,
			mosaic_stitch_status: "suspect",
			mosaic_warnings: ["Stitch in ZEN first."],
		},
		{
			basename: "tile.czi",
			likely_unstitched: true,
			mosaic_stitch_status: "suspect",
			mosaic_warnings: ["Stitch in ZEN first."],
		},
		{
			basename: "flat.czi",
			likely_unstitched: false,
			mosaic_stitch_status: "ok",
			mosaic_warnings: [],
		},
		{
			basename: "zen.czi",
			is_mosaic: true,
			m_tile_count: 30,
			likely_unstitched: false,
			mosaic_stitch_status: "ok",
			mosaic_warnings: ["Mosaic structure with 30 tile index(es) (normal for ZEN-stitched exports)."],
		},
	];
	var warnings = cziImport.collectMosaicWarnings(files);
	assert.strictEqual(warnings.length, 1);
	assert.strictEqual(warnings[0].basename, "tile.czi");
	assert.match(warnings[0].message, /ZEN/);
}

function testCollectChannelProbeWarnings() {
	var files = [
		{
			basename: "sparse.czi",
			read_warnings: ["Channel 0: sparse Z stack (1 plane with data)."],
		},
		{
			basename: "bad.czi",
			channel_pixel_probe: [{ index: 1, ok: false, error: "timeout" }],
		},
	];
	var warnings = cziImport.collectChannelProbeWarnings(files);
	assert.strictEqual(warnings.length, 2);
	assert.strictEqual(warnings[0].basename, "sparse.czi");
	assert.strictEqual(warnings[0].isError, false);
	assert.strictEqual(warnings[1].basename, "bad.czi");
	assert.strictEqual(warnings[1].isError, true);
	assert.match(warnings[1].message, /channel 1/);
}

function testCollectMosaicInfo() {
	var files = [
		{
			basename: "zen.czi",
			is_mosaic: true,
			m_tile_count: 30,
			likely_unstitched: false,
			mosaic_stitch_status: "ok",
		},
		{
			basename: "bad.czi",
			is_mosaic: true,
			m_tile_count: 4,
			likely_unstitched: true,
			mosaic_stitch_status: "suspect",
		},
	];
	var infos = cziImport.collectMosaicInfo(files);
	assert.strictEqual(infos.length, 1);
	assert.strictEqual(infos[0].basename, "zen.czi");
	assert.match(infos[0].message, /30 tile/);
}

function testHasLikelyUnstitchedMosaic() {
	assert.strictEqual(
		cziImport.hasLikelyUnstitchedMosaic([{ likely_unstitched: false }]),
		false,
	);
	assert.strictEqual(
		cziImport.hasLikelyUnstitchedMosaic([{ likely_unstitched: true }]),
		true,
	);
}

function testCountExtractWorkItems() {
	var cfg = {
		files: [
			{
				basename: "M528.czi",
				scenes: [{ index: 0, sliceId: "M528" }, { index: 1, sliceId: "M528_s001" }],
			},
		],
		channels: [
			{ file: "M528.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true },
			{ file: "M528.czi", index: 1, role: cziImport.ROLE_UNUSED, keep: false },
		],
	};
	assert.strictEqual(cziImport.countExtractWorkItems(cfg), 2);
}

testDefaultSliceId();
testSuggestRole();
testMergeProbe();
testSanitizeOtherName();
testBranchForChannelOther();
testApplyChannelDefaults();
testCollectChannelIndices();
testMaxRunRel();
testCollectSliceIds();
testNaturalCompareSectionOrder();
testNaturalCompareParenSuffix();
testNaturalCompareMixedWidths();
testNaturalCompareNoTrailingDigit();
testDetectIdentifierM467();
testDetectIdentifierScanFile();
testSortWithIdentifier();
testBuildSliceOrderSingleSceneMultiZ();
testBuildSliceOrderRenameMultiDir();
testBuildSliceOrderTwoDirsDuplicateNames();
testNormalizeSourceDirsPreservesOrder();
testCanonicalSourceDir();
testMergeProbeDirCanonicalReplace();
testResyncScanIndicesCanonicalPaths();
testChannelPathKeysDuplicateBasenames();
testValidateSliceOrderDuplicate();
testCollectKeptSignalRoleKeys();
testImportConfigPath();
testCollectMosaicWarnings();
testCollectChannelProbeWarnings();
testCollectMosaicInfo();
testHasLikelyUnstitchedMosaic();
testCountExtractWorkItems();

function testRepairTargetsMultidirDapi() {
	var imp = cziImport.buildDefaultCziImport("");
	var makeFile = function (dirPath, n, scanIndex) {
		var basename = "M514(" + n + ").czi";
		return {
			path: dirPath + "/" + basename,
			basename: basename,
			source_dir: dirPath,
			scan_index: scanIndex,
			scene_count: 1,
			channels: [{ index: 0, label: "DAPI" }],
			scenes: [{ index: 0, sliceId: basename.replace(/\.czi$/i, ""), originalSliceId: basename.replace(/\.czi$/i, "") }],
		};
	};
	var dir0Files = [];
	var dir1Files = [];
	for (var n = 1; n <= 3; n++) {
		dir0Files.push(makeFile("/day1", n, 0));
		dir1Files.push(makeFile("/day2", n, 1));
	}
	cziImport.mergeProbeDirIntoImport(imp, { files: dir0Files }, "/day1", 0);
	cziImport.mergeProbeDirIntoImport(imp, { files: dir1Files }, "/day2", 1);
	imp.slice_numbering = cziImport.SLICE_NUMBERING_RENAME;
	cziImport.buildSliceOrder(imp, "M514");
	imp.channels = [];
	for (var f = 0; f < imp.files.length; f++) {
		var file = imp.files[f];
		imp.channels.push({
			file: file.path,
			index: 0,
			role: cziImport.ROLE_DAPI,
			other_name: "",
			keep: true,
		});
	}
	var audit = {
		missingOrientDapiPreviews: [{ slice_id: "M514_s004" }],
		invalidPreviews: [],
		lowResTiffIssues: [],
	};
	var targets = cziImport.buildRepairTargetsFromAudit(audit, imp);
	assert.strictEqual(targets.length, 1);
	assert.strictEqual(targets[0].slice_id, "M514_s004");
	assert.ok(
		String(targets[0].czi_path).indexOf("/day2/") >= 0,
		"expected day2 CZI path, got " + targets[0].czi_path,
	);
	assert.notStrictEqual(
		String(targets[0].czi_path).indexOf("/day1/"),
		0,
		"repair target must not use day1 CZI for folder-2 slice",
	);
}

testRepairTargetsMultidirDapi();

function testLowResTiffAudit() {
	var bundle = fs.mkdtempSync(path.join(os.tmpdir(), "czi-tiff-audit-"));
	var sliceId = "M528_s001";
	var dapiTif = path.join(bundle, "data/counting/00_dapi", sliceId + ".tif");
	fs.mkdirSync(path.dirname(dapiTif), { recursive: true });
	fs.writeFileSync(dapiTif, "legacy");
	var issues = cziImport.findLowResTiffIssues(bundle);
	assert.strictEqual(issues.length, 1);
	assert.strictEqual(issues[0].kind, "dapi_tif");
}

function testResolveOrientPreviewPath() {
	var bundle = fs.mkdtempSync(path.join(os.tmpdir(), "czi-orient-"));
	var sliceId = "M528_s001";
	var dapiPath = cziImport.dapiPreviewPath(bundle, sliceId);
	var orientDapiPath = cziImport.orientDapiPreviewPath(bundle, sliceId);
	var prevDir = path.join(bundle, cziImport.PREVIEWS_REL);
	fs.mkdirSync(path.dirname(dapiPath), { recursive: true });
	fs.mkdirSync(prevDir, { recursive: true });
	fs.writeFileSync(orientDapiPath, "dapi-orient");
	fs.writeFileSync(dapiPath, "dapi-pipeline");
	var somataPrev = path.join(prevDir, sliceId + "_somata.png");
	fs.writeFileSync(somataPrev, "somata");
	var cziCfg = {
		primary_signal_role: cziImport.ROLE_SIGNAL_SOMATA,
		preview_format_version: cziImport.PREVIEW_FORMAT_VERSION,
		channels: [
			{ file: "M528.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true },
			{ file: "M528.czi", index: 1, role: cziImport.ROLE_SIGNAL_SOMATA, keep: true },
		],
	};
	assert.strictEqual(
		cziImport.resolveOrientPreviewPath(bundle, cziCfg, null, sliceId),
		orientDapiPath,
	);
	assert.notStrictEqual(
		cziImport.resolveOrientPreviewPath(bundle, cziCfg, null, sliceId),
		dapiPath,
	);
	var legacyTif00 = path.join(bundle, "data/counting/00_dapi", sliceId + ".tif");
	fs.writeFileSync(legacyTif00, "legacy");
	assert.strictEqual(
		cziImport.resolveOrientPreviewPath(bundle, cziCfg, null, sliceId),
		orientDapiPath,
	);
	fs.unlinkSync(legacyTif00);
	assert.strictEqual(
		cziImport.resolveOrientPreviewPath(bundle, cziCfg, null, sliceId, "somata"),
		somataPrev,
	);
	assert.strictEqual(
		cziImport.resolveOrientPreviewPath(
			bundle,
			cziCfg,
			null,
			sliceId,
			cziImport.ROLE_SIGNAL_SOMATA,
		),
		somataPrev,
	);
	fs.unlinkSync(somataPrev);
	assert.strictEqual(
		cziImport.resolveOrientPreviewPath(bundle, cziCfg, null, sliceId),
		orientDapiPath,
	);
}

function testListOrientDisplayChannels() {
	var bundle = fs.mkdtempSync(path.join(os.tmpdir(), "czi-orient-ch-"));
	var sliceId = "M528_s001";
	var orientDapiPath = cziImport.orientDapiPreviewPath(bundle, sliceId);
	var prevDir = path.join(bundle, cziImport.PREVIEWS_REL);
	fs.mkdirSync(prevDir, { recursive: true });
	fs.writeFileSync(orientDapiPath, "dapi");
	fs.writeFileSync(path.join(prevDir, sliceId + "_somata.png"), "somata");
	var cziCfg = {
		slice_order: [{ ordinal: 1, sliceId: sliceId, path: "/scan/M528.czi", scene_index: 0 }],
		channels: [
			{ file: "M528.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true },
			{ file: "M528.czi", index: 1, role: cziImport.ROLE_SIGNAL_SOMATA, keep: true },
		],
	};
	var channels = cziImport.listOrientDisplayChannels(bundle, cziCfg);
	assert.strictEqual(channels.length, 2);
	assert.strictEqual(channels[0].key, cziImport.ORIENT_DISPLAY_DAPI);
	assert.strictEqual(channels[0].label, "DAPI (_previews)");
	assert.strictEqual(channels[1].key, "somata");
	assert.strictEqual(channels[1].label, "Somata");
}

function testCziImportFingerprintStable() {
	var imp = cziImport.buildDefaultCziImport("/scan/a");
	imp.source_dirs = ["/scan/a", "/scan/b"];
	imp.slice_order = [{ ordinal: 1, sliceId: "M528_s001", path: "/scan/a/M528.czi", scene_index: 0 }];
	imp.channels = [
		{ file: "M528.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true },
	];
	var fp1 = cziImport.cziImportFingerprint(imp);
	var fp2 = cziImport.cziImportFingerprint(JSON.parse(JSON.stringify(imp)));
	assert.strictEqual(fp1, fp2);
	assert.notStrictEqual(fp1, cziImport.cziImportFingerprint(cziImport.buildDefaultCziImport("/other")));
}

function testAuditCziImportCompletion() {
	var bundle = fs.mkdtempSync(path.join(os.tmpdir(), "czi-audit-"));
	var meta = path.join(bundle, ".masonjar");
	fs.mkdirSync(meta, { recursive: true });
	var sliceId = "M528_s001";
	var cziCfg = {
		primary_signal_role: cziImport.ROLE_SIGNAL_SOMATA,
		preview_format_version: cziImport.PREVIEW_FORMAT_VERSION,
		slice_order: [{ ordinal: 1, sliceId: sliceId, path: "/scan/M528.czi", scene_index: 0, basename: "M528.czi" }],
		files: [
			{
				basename: "M528.czi",
				path: "/scan/M528.czi",
				scenes: [{ index: 0, sliceId: sliceId }],
			},
		],
		channels: [
			{ file: "M528.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true },
			{ file: "M528.czi", index: 1, role: cziImport.ROLE_SIGNAL_SOMATA, keep: true },
		],
		max_runs: { signal_somata: "somata/max/M528_s001" },
	};
	var zSomata = cziImport.originalScansPath(bundle, cziCfg.channels[1], sliceId);
	fs.mkdirSync(path.dirname(zSomata), { recursive: true });
	fs.writeFileSync(zSomata, "z");
	var zDapi = cziImport.originalScansPath(bundle, cziCfg.channels[0], sliceId);
	fs.mkdirSync(path.dirname(zDapi), { recursive: true });
	fs.writeFileSync(zDapi, "z");
	var dapiPrev = cziImport.dapiPreviewPath(bundle, sliceId);
	fs.mkdirSync(path.dirname(dapiPrev), { recursive: true });
	fs.writeFileSync(dapiPrev, "preview");
	var orientDapiPrev = cziImport.orientDapiPreviewPath(bundle, sliceId);
	fs.mkdirSync(path.dirname(orientDapiPrev), { recursive: true });
	fs.writeFileSync(orientDapiPrev, "orient");
	assert.notStrictEqual(orientDapiPrev, dapiPrev);
	var somataPrev = cziImport.signalPreviewPath(bundle, sliceId, cziCfg.channels[1]);
	fs.mkdirSync(path.dirname(somataPrev), { recursive: true });
	fs.writeFileSync(somataPrev, "preview");
	var maxDir = path.join(bundle, "data/counting/03_max/somata/max/M528_s001");
	fs.mkdirSync(maxDir, { recursive: true });
	fs.writeFileSync(path.join(maxDir, sliceId + ".tif"), "max");
	fs.writeFileSync(
		path.join(meta, "czi_import_state.json"),
		JSON.stringify({
			phase: "complete",
			done: 2,
			total: 2,
			preview_format_version: cziImport.PREVIEW_FORMAT_VERSION,
		}),
	);
	var audit = cziImport.auditCziImportCompletion(bundle, cziCfg, {
		importResult: { max_runs: cziCfg.max_runs },
	});
	assert.strictEqual(audit.extractComplete, true);
	assert.strictEqual(audit.canSkipToOrient, true);
	fs.writeFileSync(
		path.join(meta, "czi_import_state.json"),
		JSON.stringify({ phase: "complete", done: 2, total: 2, preview_format_version: 2 }),
	);
	var auditV2Tiff = cziImport.auditCziImportCompletion(bundle, cziCfg, {
		importResult: { max_runs: cziCfg.max_runs },
	});
	assert.strictEqual(auditV2Tiff.needsPreviewRepair, true);
	assert.strictEqual(auditV2Tiff.canSkipToOrient, false);
	fs.writeFileSync(
		path.join(meta, "czi_import_state.json"),
		JSON.stringify({ phase: "complete", done: 2, total: 2, preview_format_version: 1 }),
	);
	var auditLegacy = cziImport.auditCziImportCompletion(bundle, cziCfg, {
		importResult: { max_runs: cziCfg.max_runs },
	});
	assert.strictEqual(auditLegacy.needsPreviewRepair, true);
}

function testPathToFileURLSpaces() {
	var p = path.join("Z:", "Matt Jacobs", "Brain_masonjar", "data", "preview.tif");
	var href = url.pathToFileURL(p).href;
	assert.ok(href.startsWith("file://"));
	assert.ok(href.indexOf("Matt") >= 0);
}

function writeBase64Png(filePath, b64) {
	fs.mkdirSync(path.dirname(filePath), { recursive: true });
	fs.writeFileSync(filePath, Buffer.from(b64, "base64"));
}

function testBuildRepairTargetsForSelection() {
	var cziCfg = {
		slice_order: [
			{ ordinal: 1, sliceId: "M467_s006", path: "/scan/A.czi", scene_index: 0, basename: "A.czi" },
			{ ordinal: 2, sliceId: "M467_s007", path: "/scan/B.czi", scene_index: 0, basename: "B.czi" },
		],
		files: [
			{
				basename: "A.czi",
				path: "/scan/A.czi",
				scenes: [{ index: 0, sliceId: "M467_s006" }],
			},
			{
				basename: "B.czi",
				path: "/scan/B.czi",
				scenes: [{ index: 0, sliceId: "M467_s007" }],
			},
		],
		channels: [
			{ file: "A.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true },
			{ file: "A.czi", index: 1, role: cziImport.ROLE_SIGNAL_SOMATA, keep: true },
			{ file: "B.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true },
		],
	};
	var targets = cziImport.buildRepairTargetsForSelection(
		cziCfg,
		["M467_s006"],
		[cziImport.ROLE_DAPI],
	);
	assert.strictEqual(targets.length, 1);
	assert.strictEqual(targets[0].slice_id, "M467_s006");
	assert.strictEqual(targets[0].role_key, cziImport.ROLE_DAPI);
	assert.strictEqual(targets[0].czi_path, "/scan/A.czi");
}

function testFindBlankPreviews() {
	var bundle = fs.mkdtempSync(path.join(os.tmpdir(), "czi-blank-"));
	var sliceId = "M467_s006";
	var cziCfg = {
		preview_format_version: cziImport.PREVIEW_FORMAT_VERSION,
		slice_order: [{ ordinal: 1, sliceId: sliceId, path: "/scan/M.czi", scene_index: 0, basename: "M.czi" }],
		files: [{ basename: "M.czi", path: "/scan/M.czi", scenes: [{ index: 0, sliceId: sliceId }] }],
		channels: [{ file: "M.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true }],
	};
	var blackPath = cziImport.orientDapiPreviewPath(bundle, sliceId);
	var brightPath = cziImport.orientDapiPreviewPath(bundle, "M467_s007");
	writeBase64Png(
		blackPath,
		"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
	);
	writeBase64Png(
		brightPath,
		"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==",
	);
	var blackMean = cziImport.pngMeanLumaSync(blackPath);
	var brightMean = cziImport.pngMeanLumaSync(brightPath);
	assert.ok(blackMean != null && blackMean < cziImport.BLANK_PREVIEW_MEAN_THRESHOLD);
	assert.ok(brightMean != null && brightMean > cziImport.BLANK_PREVIEW_MEAN_THRESHOLD);
	var blanks = cziImport.findBlankPreviews(bundle, cziCfg, {});
	assert.strictEqual(blanks.length, 1);
	assert.strictEqual(blanks[0].slice_id, sliceId);
}

function testComputeMeanLumaFromImageData() {
	var data = new Uint8ClampedArray([0, 0, 0, 255, 255, 255, 255, 255]);
	var mean = cziImport.computeMeanLumaFromImageData({ data: data, width: 2, height: 1 });
	assert.ok(mean > 100);
}

function testBuildReextractConfigPreservesBitDepth() {
	var cfg = {
		files: [
			{
				basename: "A.czi",
				path: "/scan/A.czi",
				scenes: [{ index: 0, sliceId: "M467_s006" }],
			},
		],
		channels: [{ file: "A.czi", index: 0, role: cziImport.ROLE_DAPI, keep: true }],
		bit_depth_by_role: { signal_axons: 16 },
		preview_scale: 0.05,
		primary_signal_role: cziImport.ROLE_SIGNAL_SOMATA,
	};
	var payload = cziImport.buildReextractConfig(cfg, [], null);
	assert.strictEqual(payload.repair_mode, "reextract");
	assert.strictEqual(payload.bit_depth_by_role.signal_axons, 16);
	assert.strictEqual(payload.preview_scale, 0.05);
}

function testCziWizardModulesParse() {
	var repoRoot = path.join(__dirname, "..");
	var node = process.execPath;
	var files = ["js/czi_wizard.js", "js/czi_reextract_picker.js", "js/geometry_history.js"];
	for (var i = 0; i < files.length; i++) {
		execFileSync(node, ["--check", path.join(repoRoot, files[i])], { stdio: "pipe" });
	}
}

function testGeometryHistoryParseAndReconcile() {
	var geometryHistory = require("../js/geometry_history");
	var branding = require("../js/branding");

	var jsonl =
		'{"kind":"file","slice_id":"M528_s001","ops":["rot90"]}\n' +
		'{"kind":"file","slice_id":"M528_s001","ops":["rot90","flipX"]}\n' +
		'{"kind":"file","slice_id":"M528_s002","ops":[]}\n' +
		'{"kind":"batch","slice_id":"M528_s003"}\n';

	var entries = geometryHistory.parseGeometryHistoryJsonl(jsonl);
	assert.strictEqual(entries.length, 4);

	var lastOps = geometryHistory.lastOpsBySliceId(entries, ["M528_s001", "M528_s002", "M528_s003"]);
	assert.deepStrictEqual(lastOps["M528_s001"], ["rot90", "flipX"]);
	assert.strictEqual(lastOps["M528_s002"], undefined);
	assert.strictEqual(lastOps["M528_s003"], undefined);

	var bundle = fs.mkdtempSync(path.join(os.tmpdir(), "geom-reconcile-"));
	var metaDir = path.join(bundle, branding.META_DIR);
	fs.mkdirSync(metaDir, { recursive: true });
	fs.writeFileSync(path.join(metaDir, geometryHistory.HISTORY_FILENAME), jsonl, "utf8");
	fs.writeFileSync(
		path.join(metaDir, "geometry_apply_progress.json"),
		JSON.stringify({
			config_fingerprint: "fp",
			geometry_hash: "gh",
			files_total: 10,
			completed: 10,
		}),
		"utf8",
	);
	fs.writeFileSync(
		path.join(metaDir, "geometry_apply_last_result.json"),
		JSON.stringify({ ok: true, changed: 10, files_total: 10 }),
		"utf8",
	);

	var cziImport = {
		geometry: { M528_s001: { ops: [] }, M528_s099: { ops: ["flipY"] } },
		geometry_applied_at: "2026-01-01T00:00:00Z",
		geometry_applied_files_total: 42,
	};
	var result = geometryHistory.reconcileGeometryAfterReextract(bundle, cziImport, [
		"M528_s001",
		"M528_s004",
	]);
	assert.deepStrictEqual(result.restored, ["M528_s001"]);
	assert.deepStrictEqual(result.missing, ["M528_s004"]);
	assert.deepStrictEqual(cziImport.geometry["M528_s001"].ops, ["rot90", "flipX"]);
	assert.deepStrictEqual(cziImport.geometry["M528_s099"].ops, ["flipY"]);
	assert.strictEqual(cziImport.geometry_applied_at, undefined);
	assert.strictEqual(cziImport.geometry_applied_files_total, undefined);
	assert.ok(!fs.existsSync(path.join(metaDir, "geometry_apply_progress.json")));
	assert.ok(!fs.existsSync(path.join(metaDir, "geometry_apply_last_result.json")));
}

function testReextractSliceIdsLackHistory() {
	var geometryHistory = require("../js/geometry_history");
	var branding = require("../js/branding");

	var bundleEmpty = fs.mkdtempSync(path.join(os.tmpdir(), "geom-lack-"));
	assert.strictEqual(
		geometryHistory.reextractSliceIdsLackHistory(bundleEmpty, ["M528_s001"]),
		true,
	);

	var bundleWithHistory = fs.mkdtempSync(path.join(os.tmpdir(), "geom-lack-"));
	var metaDir = path.join(bundleWithHistory, branding.META_DIR);
	fs.mkdirSync(metaDir, { recursive: true });
	fs.writeFileSync(
		path.join(metaDir, geometryHistory.HISTORY_FILENAME),
		'{"kind":"file","slice_id":"M528_s001","ops":["rot90"]}\n',
		"utf8",
	);
	assert.strictEqual(
		geometryHistory.reextractSliceIdsLackHistory(bundleWithHistory, ["M528_s001"]),
		false,
	);
	assert.strictEqual(
		geometryHistory.reextractSliceIdsLackHistory(bundleWithHistory, ["M528_s002"]),
		true,
	);
	assert.strictEqual(geometryHistory.reextractSliceIdsLackHistory(bundleWithHistory, []), false);
}

function testBuildReextractGeometryScope() {
	var cziImport = require("../js/czi_import");
	var scope = cziImport.buildReextractGeometryScope([
		{ slice_id: "M528_s001", role_key: "signal_somata" },
		{ slice_id: "M528_s001", role_key: "signal_nuclei" },
		{ slice_id: "M528_s002", role_key: "signal_somata" },
	]);
	assert.deepStrictEqual(scope["M528_s001"], ["signal_somata", "signal_nuclei"]);
	assert.deepStrictEqual(scope["M528_s002"], ["signal_somata"]);
}

function testReextractOrientDisplayChannels() {
	var cziImport = require("../js/czi_import");
	assert.strictEqual(cziImport.displayChannelKeyForRoleKey("signal_somata"), "somata");
	assert.strictEqual(cziImport.displayChannelKeyForRoleKey("dapi"), "dapi");
	assert.strictEqual(cziImport.displayChannelKeyForRoleKey("other:my_branch"), "my_branch");

	var scope = {
		M528_s001: ["signal_somata"],
	};
	assert.deepStrictEqual(cziImport.unionDisplayChannelKeysFromScope(scope), ["somata"]);
	assert.deepStrictEqual(cziImport.sliceIdsInReextractScope(scope), ["M528_s001"]);

	var cfg = {
		channels: [
			{ keep: true, role: "dapi", index: 0 },
			{ keep: true, role: "signal_somata", index: 1 },
			{ keep: true, role: "signal_nuclei", index: 2 },
		],
	};
	assert.strictEqual(
		cziImport.isDisplayChannelInReextractScope("M528_s001", "somata", scope, cfg),
		true,
	);
	assert.strictEqual(
		cziImport.isDisplayChannelInReextractScope("M528_s001", "dapi", scope, cfg),
		false,
	);
	assert.strictEqual(
		cziImport.isDisplayChannelInReextractScope("M528_s001", "nuclei", scope, cfg),
		false,
	);

	var list = cziImport.listOrientDisplayChannelsForReextract(null, cfg, scope);
	var byKey = {};
	for (var i = 0; i < list.length; i++) {
		byKey[list[i].key] = list[i].enabled;
	}
	assert.strictEqual(byKey.dapi, false);
	assert.strictEqual(byKey.somata, true);
	assert.strictEqual(byKey.nuclei, false);

	assert.strictEqual(
		cziImport.firstEnabledReextractDisplayChannel(null, cfg, scope),
		"somata",
	);
}

testLowResTiffAudit();
testResolveOrientPreviewPath();
testListOrientDisplayChannels();
testCziImportFingerprintStable();
testAuditCziImportCompletion();
testPathToFileURLSpaces();
testBuildRepairTargetsForSelection();
testFindBlankPreviews();
testComputeMeanLumaFromImageData();
testBuildReextractConfigPreservesBitDepth();
testGeometryHistoryParseAndReconcile();
testReextractSliceIdsLackHistory();
testBuildReextractGeometryScope();
testReextractOrientDisplayChannels();
testCziWizardModulesParse();
console.log("test-czi-import.js: OK");
