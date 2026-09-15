"use strict";

var assert = require("assert");
var path = require("path");
var fs = require("fs");
var preprocessWizard = require("../js/preprocess_wizard");

function testViewportRoi() {
	var state = { scale: 1, panX: -100, panY: -50, viewW: 512, viewH: 512 };
	var roi = preprocessWizard.viewportRoi(state, 2000, 1500);
	assert.ok(roi.w >= 32);
	assert.ok(roi.h >= 32);
	assert.ok(roi.x >= 0);
	assert.ok(roi.y >= 0);
}

function testScaleRoiForFullRes() {
	var roi = { x: 10, y: 20, w: 100, h: 80 };
	var scaled = preprocessWizard.scaleRoiForFullRes(roi, 500, 400, 2000, 1600);
	assert.strictEqual(scaled.x, 40);
	assert.strictEqual(scaled.y, 80);
	assert.strictEqual(scaled.w, 400);
	assert.strictEqual(scaled.h, 320);
	var same = preprocessWizard.scaleRoiForFullRes(roi, 100, 80, 100, 80);
	assert.deepStrictEqual(same, roi);
}

function testResolvePreviewFilterRequest() {
	var previewAbs = "C:\\bundle\\data\\counting\\_previews\\M528_s001_rabies.png";
	var tiffAbs = "C:\\bundle\\data\\counting\\03_max\\rabies\\max\\run\\M528_s001.tif";
	var roi = { x: 10, y: 20, w: 100, h: 80 };
	var highBudget = preprocessWizard.PREVIEW_PIXEL_BUDGET * 10;

	var onPreview = preprocessWizard.resolvePreviewFilterRequest(
		{
			baseAbs: previewAbs,
			baseNaturalW: 500,
			baseNaturalH: 400,
			fullNaturalW: 10000,
			fullNaturalH: 8000,
			scale: 10,
			minPreviewScale: 1,
			maxFullResPreviewPixels: highBudget,
		},
		roi,
		tiffAbs,
	);
	assert.strictEqual(onPreview.ready, true);
	assert.strictEqual(onPreview.filterAbs, tiffAbs);
	assert.strictEqual(onPreview.roi.x, 200);
	assert.strictEqual(onPreview.roi.w, 2000);
	assert.ok(onPreview.previewRoi);
	assert.strictEqual(onPreview.previewRoi.x, 10);
	assert.strictEqual(onPreview.previewRoi.w, 100);

	var onFull = preprocessWizard.resolvePreviewFilterRequest(
		{
			baseAbs: tiffAbs,
			baseNaturalW: 10000,
			baseNaturalH: 8000,
			fullNaturalW: 10000,
			fullNaturalH: 8000,
			scale: 10,
			minPreviewScale: 1,
			maxFullResPreviewPixels: highBudget,
		},
		roi,
		tiffAbs,
	);
	assert.strictEqual(onFull.ready, true);
	assert.strictEqual(onFull.filterAbs, tiffAbs);
	assert.strictEqual(onFull.roi.x, 10);
	assert.strictEqual(onFull.roi.w, 100);

	var deferred = preprocessWizard.resolvePreviewFilterRequest(
		{
			baseAbs: tiffAbs,
			baseNaturalW: 10000,
			baseNaturalH: 8000,
			fullNaturalW: 0,
			fullNaturalH: 0,
		},
		roi,
		tiffAbs,
	);
	assert.strictEqual(deferred.ready, false);
	assert.strictEqual(deferred.reason, "waiting_for_dimensions");

	var zoomedOut = preprocessWizard.resolvePreviewFilterRequest(
		{
			baseNaturalW: 800,
			baseNaturalH: 640,
			fullNaturalW: 16129,
			fullNaturalH: 6399,
			scale: 1,
			minPreviewScale: 4.5,
		},
		roi,
		tiffAbs,
	);
	assert.strictEqual(zoomedOut.ready, false);
	assert.strictEqual(zoomedOut.reason, "zoom_too_far");
}

function testComputePreviewZoomPolicy() {
	var state = {
		baseNaturalW: 800,
		baseNaturalH: 640,
		fullNaturalW: 16129,
		fullNaturalH: 6399,
		viewW: 512,
		viewH: 512,
		scale: 1,
	};
	preprocessWizard.computePreviewZoomPolicy(state);
	assert.ok(state.minPreviewScale > 1);
	assert.ok(state.previewScaleX > 10);
}

function testCapFullResRoi() {
	var roi = { x: 0, y: 0, w: 4000, h: 4000 };
	var capped = preprocessWizard.capFullResRoi(roi, 1000000);
	assert.ok(capped.w * capped.h <= 1000000 + 8000);
	assert.ok(capped.w >= 8);
	assert.ok(capped.h >= 8);
}

function testIsPreviewZoomEligible() {
	var state = {
		fullNaturalW: 1000,
		fullNaturalH: 1000,
		minPreviewScale: 3,
		scale: 2,
	};
	assert.strictEqual(preprocessWizard.isPreviewZoomEligible(state), false);
	state.scale = 3.5;
	assert.strictEqual(preprocessWizard.isPreviewZoomEligible(state), true);
}

function testAutoStretchImageDataIfFlat() {
	var flat = {
		data: new Uint8ClampedArray([5, 5, 5, 255, 8, 8, 8, 255]),
		width: 2,
		height: 1,
	};
	var out = preprocessWizard.autoStretchImageDataIfFlat(flat);
	assert.ok(out.data[0] < out.data[4]);
	var bright = {
		data: new Uint8ClampedArray([100, 100, 100, 255, 200, 200, 200, 255]),
		width: 2,
		height: 1,
	};
	var unchanged = preprocessWizard.autoStretchImageDataIfFlat(bright);
	assert.strictEqual(unchanged.data[0], 100);
}

function testFindSignalPreviewAbs() {
	var helpers = require("./test-helpers");
	var bundle = helpers.tmpDir("mj-prev-");
	var prevDir = path.join(bundle, "data", "counting", "_previews");
	fs.mkdirSync(prevDir, { recursive: true });
	fs.writeFileSync(path.join(prevDir, "M528_s001_dapi.png"), "dapi");
	fs.writeFileSync(path.join(prevDir, "M528_s001_rabies.png"), "rabies");
	var rabies = preprocessWizard.findSignalPreviewAbs(
		bundle,
		"M528_s001.tif",
		"rabies",
	);
	assert.strictEqual(
		rabies,
		path.join(prevDir, "M528_s001_rabies.png"),
		"signal branch should pick rabies preview not dapi",
	);
	var missing = preprocessWizard.findSignalPreviewAbs(
		bundle,
		"M528_s002.tif",
		"somata",
	);
	assert.strictEqual(missing, "");
	helpers.rmDir(bundle);
}

function testIsProcessableTiffName() {
	assert.strictEqual(preprocessWizard.isProcessableTiffName("a.tif"), true);
	assert.strictEqual(preprocessWizard.isProcessableTiffName("b.ome.tiff"), true);
	assert.strictEqual(preprocessWizard.isProcessableTiffName("c.png"), false);
}

function testParsePreviewJson() {
	var line = 'PREVIEW_JSON:{"ok":true,"previewPath":"/tmp/x.png","width":100,"height":80}';
	var data = preprocessWizard.parsePreviewJsonLine(line);
	assert.strictEqual(data.ok, true);
	assert.strictEqual(data.width, 100);
}

function testNoAutoPreviewOnInteraction() {
	assert.strictEqual(preprocessWizard.shouldSchedulePreviewOnInteraction(), false);
}

function testApplyDisplayWindow() {
	var imgData = {
		data: new Uint8ClampedArray([50, 50, 50, 255, 150, 150, 150, 255, 200, 200, 200, 255]),
		width: 3,
		height: 1,
	};
	var out = preprocessWizard.applyDisplayWindow(imgData, 0, 255);
	assert.strictEqual(out.data[0], 50);
	var stretched = preprocessWizard.applyDisplayWindow(imgData, 100, 200);
	assert.ok(stretched.data[4] > 100);
}

function testBakeFilterIntoBaseImageData() {
	var base = {
		data: new Uint8ClampedArray([
			100, 100, 100, 255, 100, 100, 100, 255, 100, 100, 100, 255,
			100, 100, 100, 255, 100, 100, 100, 255, 100, 100, 100, 255,
			100, 100, 100, 255, 100, 100, 100, 255, 100, 100, 100, 255,
		]),
		width: 3,
		height: 3,
	};
	var filt = {
		data: new Uint8ClampedArray([
			20, 20, 20, 255, 30, 30, 30, 255,
			40, 40, 40, 255, 50, 50, 50, 255,
		]),
		width: 2,
		height: 2,
	};
	var roi = { x: 1, y: 1, w: 2, h: 2 };
	var out = preprocessWizard.bakeFilterIntoBaseImageData(base, filt, roi, 0, 255);
	assert.strictEqual(out.data[0], 100);
	var idx11 = (1 * 3 + 1) * 4;
	assert.strictEqual(out.data[idx11], 20);
	var idx22 = (2 * 3 + 2) * 4;
	assert.strictEqual(out.data[idx22], 50);

	var windowed = preprocessWizard.bakeFilterIntoBaseImageData(base, filt, roi, 0, 100);
	assert.strictEqual(windowed.data[idx11], 51);
	assert.strictEqual(windowed.data[0], 255);
}

function testResolvePreviewRequestFromFilterView() {
	var state = {
		showingFiltered: true,
		lastFullResFilterRoi: { x: 100, y: 200, w: 400, h: 300 },
		scale: 1,
		panX: 0,
		panY: 0,
		viewW: 400,
		viewH: 300,
		maxFullResPreviewPixels: 1e9,
	};
	var filterBmp = { width: 400, height: 300 };
	var resolved = preprocessWizard.resolvePreviewRequest(
		state,
		filterBmp,
		"C:\\slice.tif",
	);
	assert.strictEqual(resolved.ready, true);
	assert.strictEqual(resolved.roi.x, 100);
	assert.strictEqual(resolved.roi.y, 200);
	assert.strictEqual(resolved.roi.w, 400);
	assert.strictEqual(resolved.roi.h, 300);
}

function testPreviewRoiFromFullRes() {
	var state = {
		baseNaturalW: 800,
		baseNaturalH: 640,
		fullNaturalW: 16000,
		fullNaturalH: 12800,
	};
	var fullRoi = { x: 4000, y: 3200, w: 800, h: 640 };
	var preview = preprocessWizard.previewRoiFromFullRes(fullRoi, state);
	assert.strictEqual(preview.x, 200);
	assert.strictEqual(preview.y, 160);
	assert.strictEqual(preview.w, 40);
	assert.strictEqual(preview.h, 32);
}

function testApplyCursorAnchoredZoom() {
	var state = { scale: 2, panX: 100, panY: 50 };
	// Zoom in 2× at viewport center (256, 256): image point under cursor stays fixed.
	preprocessWizard.applyCursorAnchoredZoom(state, 256, 256, 2);
	assert.strictEqual(state.scale, 4);
	assert.strictEqual(state.panX, -56);
	assert.strictEqual(state.panY, -156);
	// Zoom out restores prior scale and pan.
	preprocessWizard.applyCursorAnchoredZoom(state, 256, 256, 0.5);
	assert.strictEqual(state.scale, 2);
	assert.strictEqual(state.panX, 100);
	assert.strictEqual(state.panY, 50);
}

function testFitScaleToViewport() {
	var scale = preprocessWizard.fitScaleToViewport(2000, 1000, 512, 512);
	assert.ok(scale > 0 && scale <= 1);
	assert.strictEqual(preprocessWizard.fitScaleToViewport(256, 256, 512, 512), 1);
	var pan = preprocessWizard.centerPanForFit(200, 100, 512, 512, 0.5);
	assert.ok(pan.panX > 0);
	assert.ok(pan.panY > 0);
	var state = {
		baseNaturalW: 2000,
		baseNaturalH: 1000,
		viewW: 512,
		viewH: 512,
		scale: 1,
		panX: 0,
		panY: 0,
	};
	preprocessWizard.fitViewportToImage(state);
	assert.ok(state.scale < 1);
	assert.ok(state.panX !== 0 || state.panY !== 0);
}

testViewportRoi();
testScaleRoiForFullRes();
testFitScaleToViewport();
testResolvePreviewFilterRequest();
testComputePreviewZoomPolicy();
testCapFullResRoi();
testPreviewRoiFromFullRes();
testApplyCursorAnchoredZoom();
testIsPreviewZoomEligible();
testAutoStretchImageDataIfFlat();
testFindSignalPreviewAbs();
testIsProcessableTiffName();
testParsePreviewJson();
testNoAutoPreviewOnInteraction();
testApplyDisplayWindow();
testBakeFilterIntoBaseImageData();
testResolvePreviewRequestFromFilterView();
console.log("test-preprocess-wizard: ok");
