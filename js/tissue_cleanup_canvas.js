"use strict";

/**
 * Static-fit canvas editor for tissue keep masks (255 = keep, 0 = remove).
 */
function createTissueCleanupCanvas(opts) {
	opts = opts || {};
	var canvas = opts.canvas;
	var viewport = opts.viewport;
	var ctx = canvas ? canvas.getContext("2d") : null;
	var onTraceChange = opts.onTraceChange;

	var ORPHAN_MIN_AREA = 32;
	var ORPHAN_AREA_FRAC = 0.0002;

	var state = {
		image: null,
		imageUrl: "",
		mask: null,
		maskVisible: false,
		sliceUntouched: true,
		scale: 1,
		panX: 0,
		panY: 0,
		mode: "idle",
		eraserSize: 16,
		tracePoints: [],
		undoStack: [],
	};

	function imageCoords(clientX, clientY) {
		if (!viewport || !canvas) {
			return { x: 0, y: 0 };
		}
		var rect = viewport.getBoundingClientRect();
		var x = (clientX - rect.left - state.panX) / state.scale;
		var y = (clientY - rect.top - state.panY) / state.scale;
		return {
			x: Math.max(0, Math.min(canvas.width - 1, x)),
			y: Math.max(0, Math.min(canvas.height - 1, y)),
		};
	}

	function notifyTraceChange() {
		if (typeof onTraceChange === "function") {
			onTraceChange(state.tracePoints.length);
		}
	}

	function resizeCanvasToImage() {
		if (!canvas || !state.image) {
			return;
		}
		canvas.width = state.image.naturalWidth;
		canvas.height = state.image.naturalHeight;
		if (!state.mask || state.mask.width !== canvas.width || state.mask.height !== canvas.height) {
			state.mask = newMask(canvas.width, canvas.height, 255);
		}
		fitToViewport();
		draw();
	}

	function newMask(w, h, fill) {
		var c = document.createElement("canvas");
		c.width = w;
		c.height = h;
		var mctx = c.getContext("2d");
		mctx.fillStyle = "rgb(" + fill + "," + fill + "," + fill + ")";
		mctx.fillRect(0, 0, w, h);
		return c;
	}

	function fitToViewport() {
		if (!viewport || !canvas || !canvas.width) {
			return;
		}
		var vw = viewport.clientWidth || 512;
		var vh = viewport.clientHeight || 512;
		state.scale = Math.min(vw / canvas.width, vh / canvas.height, 1);
		state.panX = (vw - canvas.width * state.scale) / 2;
		state.panY = (vh - canvas.height * state.scale) / 2;
		applyTransform();
	}

	function applyTransform() {
		if (!canvas) {
			return;
		}
		// Force top-left origin inline (stylesheet value was being overridden to
		// center, which shifted wide images right and out of the viewport frame).
		canvas.style.transformOrigin = "0 0";
		canvas.style.transform =
			"translate(" + state.panX + "px," + state.panY + "px) scale(" + state.scale + ")";
	}

	// Scale at which the image just fits the viewport — the zoom-out floor.
	function currentFitScale() {
		if (!viewport || !canvas || !canvas.width || !canvas.height) {
			return 1;
		}
		var vw = viewport.clientWidth || 512;
		var vh = viewport.clientHeight || 512;
		return Math.min(vw / canvas.width, vh / canvas.height, 1);
	}

	// Zoom toward the cursor (scroll-wheel). Keeps the image point under the
	// pointer fixed. Clamped between fit (recenters) and a max magnification.
	function zoomAt(clientX, clientY, factor) {
		if (!viewport || !canvas || !state.image) {
			return;
		}
		var rect = viewport.getBoundingClientRect();
		var sx = clientX - rect.left;
		var sy = clientY - rect.top;
		var fit = currentFitScale();
		var maxScale = fit * 12;
		var newScale = Math.max(fit, Math.min(maxScale, state.scale * factor));
		if (Math.abs(newScale - state.scale) < 1e-6) {
			return;
		}
		if (newScale <= fit + 1e-6) {
			// Back at (or below) fit — recenter the whole image.
			fitToViewport();
			return;
		}
		var ratio = newScale / state.scale;
		state.panX = sx - (sx - state.panX) * ratio;
		state.panY = sy - (sy - state.panY) * ratio;
		state.scale = newScale;
		applyTransform();
	}

	function draw() {
		if (!ctx || !state.image || !state.mask) {
			return;
		}
		ctx.clearRect(0, 0, canvas.width, canvas.height);
		ctx.drawImage(state.image, 0, 0);
		if (
			!state.maskVisible ||
			(state.sliceUntouched && maskIsAllKeep())
		) {
			if (state.mode === "trace" && state.tracePoints.length > 0) {
				ctx.strokeStyle = "#00e5ff";
				ctx.fillStyle = "#00e5ff";
				ctx.lineWidth = 12;
				ctx.lineCap = "round";
				ctx.lineJoin = "round";
				if (state.tracePoints.length === 1) {
					var tp0 = state.tracePoints[0];
					ctx.beginPath();
					ctx.arc(tp0.x, tp0.y, 6, 0, Math.PI * 2);
					ctx.fill();
				} else {
					ctx.beginPath();
					ctx.moveTo(state.tracePoints[0].x, state.tracePoints[0].y);
					for (var tp = 1; tp < state.tracePoints.length; tp++) {
						ctx.lineTo(state.tracePoints[tp].x, state.tracePoints[tp].y);
					}
					ctx.stroke();
				}
			}
			return;
		}
		var overlay = document.createElement("canvas");
		overlay.width = canvas.width;
		overlay.height = canvas.height;
		var octx = overlay.getContext("2d");
		octx.drawImage(state.mask, 0, 0);
		var imgData = octx.getImageData(0, 0, overlay.width, overlay.height);
		var data = imgData.data;
		for (var i = 0; i < data.length; i += 4) {
			if (data[i] >= 128) {
				data[i] = 40;
				data[i + 1] = 200;
				data[i + 2] = 80;
				data[i + 3] = 115;
			} else {
				data[i] = 255;
				data[i + 1] = 40;
				data[i + 2] = 40;
				data[i + 3] = 140;
			}
		}
		octx.putImageData(imgData, 0, 0);
		ctx.drawImage(overlay, 0, 0);
		if (state.mode === "trace" && state.tracePoints.length > 0) {
			ctx.strokeStyle = "#00e5ff";
			ctx.fillStyle = "#00e5ff";
			ctx.lineWidth = 12;
			ctx.lineCap = "round";
			ctx.lineJoin = "round";
			if (state.tracePoints.length === 1) {
				var p0 = state.tracePoints[0];
				ctx.beginPath();
				ctx.arc(p0.x, p0.y, 6, 0, Math.PI * 2);
				ctx.fill();
			} else {
				ctx.beginPath();
				ctx.moveTo(state.tracePoints[0].x, state.tracePoints[0].y);
				for (var p = 1; p < state.tracePoints.length; p++) {
					ctx.lineTo(state.tracePoints[p].x, state.tracePoints[p].y);
				}
				ctx.stroke();
			}
		}
	}

	function pushUndo() {
		if (!state.mask) {
			return;
		}
		var snap = document.createElement("canvas");
		snap.width = state.mask.width;
		snap.height = state.mask.height;
		snap.getContext("2d").drawImage(state.mask, 0, 0);
		state.undoStack.push(snap);
		if (state.undoStack.length > 8) {
			state.undoStack.shift();
		}
	}

	function undo() {
		if (!state.undoStack.length || !state.mask) {
			return false;
		}
		var snap = state.undoStack.pop();
		state.mask.getContext("2d").clearRect(0, 0, state.mask.width, state.mask.height);
		state.mask.getContext("2d").drawImage(snap, 0, 0);
		draw();
		return true;
	}

	function pruneOrphanKeepIslands() {
		if (!state.mask) {
			return 0;
		}
		var w = state.mask.width;
		var h = state.mask.height;
		var mctx = state.mask.getContext("2d");
		var data = mctx.getImageData(0, 0, w, h).data;
		var orphanMax = Math.max(
			ORPHAN_MIN_AREA,
			Math.floor(w * h * ORPHAN_AREA_FRAC),
		);
		var labels = new Int32Array(w * h);
		var nextLabel = 1;
		var areas = [];
		var stack = [];

		function idx(x, y) {
			return y * w + x;
		}

		for (var y = 0; y < h; y++) {
			for (var x = 0; x < w; x++) {
				var i = idx(x, y);
				if (data[i * 4] < 128 || labels[i] !== 0) {
					continue;
				}
				var label = nextLabel++;
				var area = 0;
				stack.push(i);
				labels[i] = label;
				while (stack.length) {
					var cur = stack.pop();
					area += 1;
					var cx = cur % w;
					var cy = (cur / w) | 0;
					var neighbors = [
						[cx - 1, cy],
						[cx + 1, cy],
						[cx, cy - 1],
						[cx, cy + 1],
					];
					for (var n = 0; n < neighbors.length; n++) {
						var nx = neighbors[n][0];
						var ny = neighbors[n][1];
						if (nx < 0 || ny < 0 || nx >= w || ny >= h) {
							continue;
						}
						var ni = idx(nx, ny);
						if (labels[ni] !== 0 || data[ni * 4] < 128) {
							continue;
						}
						labels[ni] = label;
						stack.push(ni);
					}
				}
				areas[label] = area;
			}
		}

		var largest = 0;
		var largestLabel = 0;
		for (var li = 1; li < nextLabel; li++) {
			if ((areas[li] || 0) > largest) {
				largest = areas[li];
				largestLabel = li;
			}
		}

		var cleared = 0;
		for (var pi = 0; pi < labels.length; pi++) {
			var lab = labels[pi];
			if (!lab || lab === largestLabel) {
				continue;
			}
			if ((areas[lab] || 0) >= orphanMax) {
				continue;
			}
			data[pi * 4] = 0;
			data[pi * 4 + 1] = 0;
			data[pi * 4 + 2] = 0;
			data[pi * 4 + 3] = 255;
			cleared += 1;
		}
		if (cleared) {
			mctx.putImageData(new ImageData(data, w, h), 0, 0);
			draw();
		}
		return cleared;
	}

	function loadImageUrl(url) {
		return new Promise(function (resolve, reject) {
			var img = new Image();
			img.onload = function () {
				state.image = img;
				state.imageUrl = url;
				resizeCanvasToImage();
				resolve();
			};
			img.onerror = reject;
			img.src = url;
		});
	}

	function loadMaskFromImage(imgOrCanvas) {
		if (!state.mask || !imgOrCanvas) {
			return;
		}
		pushUndo();
		var mctx = state.mask.getContext("2d");
		mctx.clearRect(0, 0, state.mask.width, state.mask.height);
		mctx.drawImage(imgOrCanvas, 0, 0, state.mask.width, state.mask.height);
		draw();
	}

	function loadMaskFromBase64(b64) {
		return new Promise(function (resolve, reject) {
			var img = new Image();
			img.onload = function () {
				loadMaskFromImage(img);
				resolve();
			};
			img.onerror = reject;
			img.src = "data:image/png;base64," + b64;
		});
	}

	function resetMaskAllKeep() {
		if (!state.mask) {
			return;
		}
		pushUndo();
		var mctx = state.mask.getContext("2d");
		mctx.fillStyle = "#ffffff";
		mctx.fillRect(0, 0, state.mask.width, state.mask.height);
		state.maskVisible = false;
		state.sliceUntouched = true;
		draw();
	}

	function setMaskVisible(visible) {
		state.maskVisible = !!visible;
		draw();
	}

	function setSliceUntouched(untouched) {
		state.sliceUntouched = !!untouched;
		draw();
	}

	function maskIsAllKeep() {
		if (!state.mask) {
			return true;
		}
		var mctx = state.mask.getContext("2d");
		var data = mctx.getImageData(0, 0, state.mask.width, state.mask.height).data;
		for (var i = 0; i < data.length; i += 4) {
			if (data[i] < 128) {
				return false;
			}
		}
		return true;
	}

	function exportMaskPngPath(fs, pathMod, outPath) {
		if (!state.mask || !fs) {
			return;
		}
		var tmp = document.createElement("canvas");
		tmp.width = state.mask.width;
		tmp.height = state.mask.height;
		var tctx = tmp.getContext("2d");
		tctx.drawImage(state.mask, 0, 0);
		var buf = Buffer.from(
			tmp.toDataURL("image/png").replace(/^data:image\/png;base64,/, ""),
			"base64",
		);
		fs.mkdirSync(pathMod.dirname(outPath), { recursive: true });
		fs.writeFileSync(outPath, buf);
	}

	function loadMaskFromFile(fs, pathMod, maskPath) {
		if (!state.mask) {
			return Promise.resolve();
		}
		if (!fs || !fs.existsSync(maskPath)) {
			resetMaskAllKeep();
			return Promise.resolve();
		}
		return new Promise(function (resolve, reject) {
			var img = new Image();
			img.onload = function () {
				state.mask.getContext("2d").clearRect(0, 0, state.mask.width, state.mask.height);
				state.mask.getContext("2d").drawImage(img, 0, 0, state.mask.width, state.mask.height);
				draw();
				resolve();
			};
			img.onerror = reject;
			img.src = "file://" + maskPath.split(pathMod.sep).join("/") + "?m=" + Date.now();
		});
	}

	function setMode(mode) {
		state.mode = mode;
		if (viewport) {
			viewport.classList.toggle("erase-mode", mode === "erase");
			viewport.classList.toggle("keep-mode", mode === "keep");
			viewport.classList.toggle("trace-mode", mode === "trace");
		}
		if (mode !== "trace") {
			state.tracePoints = [];
			notifyTraceChange();
		}
		if (!isPaintMode(mode)) {
			hideBrushCursor();
		} else if (lastBrushPointer) {
			updateBrushCursor(lastBrushPointer.x, lastBrushPointer.y);
		}
		draw();
	}

	function stampBrush(x, y, keepValue) {
		if (!state.mask) {
			return;
		}
		var mctx = state.mask.getContext("2d");
		mctx.fillStyle = keepValue ? "#ffffff" : "#000000";
		mctx.beginPath();
		mctx.arc(x, y, state.eraserSize / 2, 0, Math.PI * 2);
		mctx.fill();
	}

	function paintBrush(x, y, keepValue) {
		stampBrush(x, y, keepValue);
		draw();
	}

	function paintBrushSegment(from, to, keepValue) {
		if (!from) {
			paintBrush(to.x, to.y, keepValue);
			return;
		}
		if (!state.mask) {
			return;
		}
		// A round-capped canvas stroke joins two brush centers with one native
		// operation. This is both seamless and cheaper than filling hundreds of
		// intermediate circles after a coalesced fast mouse movement.
		var mctx = state.mask.getContext("2d");
		mctx.strokeStyle = keepValue ? "#ffffff" : "#000000";
		mctx.lineWidth = state.eraserSize;
		mctx.lineCap = "round";
		mctx.beginPath();
		mctx.moveTo(from.x, from.y);
		mctx.lineTo(to.x, to.y);
		mctx.stroke();
		draw();
	}

	function paintErase(x, y) {
		paintBrush(x, y, false);
	}

	// Brush-size cursor ring: a lightweight DOM circle following the pointer over
	// the viewport while a paint tool is active. Diameter mirrors the actual mask
	// stamp (eraserSize in image px) scaled to screen px.
	var brushCursorEl = null;
	var lastBrushPointer = null;
	function brushCursor() {
		if (!brushCursorEl) {
			brushCursorEl = document.getElementById("brushCursor");
		}
		return brushCursorEl;
	}
	function updateBrushCursor(clientX, clientY) {
		var el = brushCursor();
		if (!el || !viewport) {
			return;
		}
		lastBrushPointer = { x: clientX, y: clientY };
		if (!isPaintMode(state.mode) || !state.image || spaceDown) {
			el.hidden = true;
			return;
		}
		var rect = viewport.getBoundingClientRect();
		// At fit-to-screen scale a real 5–50px image brush can be sub-pixel.
		// Keep a visible 10px minimum while retaining exact proportional size once
		// the user zooms in.
		var d = Math.max(10, state.eraserSize * state.scale);
		el.style.width = d + "px";
		el.style.height = d + "px";
		el.style.left = clientX - rect.left + "px";
		el.style.top = clientY - rect.top + "px";
		el.classList.toggle("erase-cursor", state.mode === "erase");
		el.classList.toggle("keep-cursor", state.mode === "keep");
		el.hidden = false;
	}
	function hideBrushCursor() {
		var el = brushCursor();
		if (el) {
			el.hidden = true;
		}
	}

	// Space + left-drag = pan. spaceDown tracks the held spacebar; panning tracks
	// an active drag.
	var spaceDown = false;
	var panning = false;
	var panStart = { x: 0, y: 0, panX: 0, panY: 0 };

	function isTextInput(el) {
		if (!el) {
			return false;
		}
		var tag = (el.tagName || "").toUpperCase();
		return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
	}

	// Keep at least a margin of the image inside the viewport so it can't be
	// panned completely out of view.
	function clampPan() {
		if (!viewport || !canvas) {
			return;
		}
		var vw = viewport.clientWidth || 512;
		var vh = viewport.clientHeight || 512;
		var iw = canvas.width * state.scale;
		var ih = canvas.height * state.scale;
		var margin = 40;
		state.panX = Math.max(margin - iw, Math.min(vw - margin, state.panX));
		state.panY = Math.max(margin - ih, Math.min(vh - margin, state.panY));
	}

	function updatePanCursor() {
		if (!viewport) {
			return;
		}
		if (spaceDown) {
			viewport.style.cursor = panning ? "grabbing" : "grab";
		} else {
			viewport.style.cursor = "";
		}
	}

	var painting = false;
	var paintedDuringStroke = false;
	var strokeKeepValue = false;
	var lastStrokePoint = null;

	function isPaintMode(mode) {
		return mode === "erase" || mode === "keep";
	}

	function wirePointerEvents() {
		if (!viewport) {
			return;
		}

		viewport.addEventListener("mousedown", function (ev) {
			updateBrushCursor(ev.clientX, ev.clientY);
			if (spaceDown && ev.button === 0) {
				// Space + left-drag pans instead of painting.
				ev.preventDefault();
				panning = true;
				panStart = {
					x: ev.clientX,
					y: ev.clientY,
					panX: state.panX,
					panY: state.panY,
				};
				updatePanCursor();
				hideBrushCursor();
				return;
			}
			if (!isPaintMode(state.mode) && state.mode !== "trace") {
				return;
			}
			ev.preventDefault();
			var pt = imageCoords(ev.clientX, ev.clientY);
			if (isPaintMode(state.mode)) {
				pushUndo();
				paintedDuringStroke = true;
				painting = true;
				strokeKeepValue = state.mode === "keep";
				state.maskVisible = true;
				state.sliceUntouched = false;
				paintBrush(pt.x, pt.y, strokeKeepValue);
				lastStrokePoint = pt;
				return;
			}
			state.tracePoints.push(pt);
			draw();
			notifyTraceChange();
		});

		window.addEventListener("mousemove", function (ev) {
			if (!painting || !isPaintMode(state.mode)) {
				return;
			}
			var pt = imageCoords(ev.clientX, ev.clientY);
			paintBrushSegment(lastStrokePoint, pt, strokeKeepValue);
			lastStrokePoint = pt;
		});

		viewport.addEventListener("mousemove", function (ev) {
			updateBrushCursor(ev.clientX, ev.clientY);
		});
		viewport.addEventListener("mouseleave", function () {
			hideBrushCursor();
		});
		viewport.addEventListener(
			"wheel",
			function (ev) {
				if (!state.image) {
					return;
				}
				ev.preventDefault();
				var factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
				zoomAt(ev.clientX, ev.clientY, factor);
				updateBrushCursor(ev.clientX, ev.clientY);
			},
			{ passive: false },
		);

		window.addEventListener("mousemove", function (ev) {
			if (!panning) {
				return;
			}
			state.panX = panStart.panX + (ev.clientX - panStart.x);
			state.panY = panStart.panY + (ev.clientY - panStart.y);
			clampPan();
			applyTransform();
		});

		window.addEventListener("mouseup", function () {
			if (panning) {
				panning = false;
				updatePanCursor();
			}
		});

		window.addEventListener("keydown", function (ev) {
			if (ev.code === "Space" && !isTextInput(document.activeElement)) {
				if (!spaceDown) {
					spaceDown = true;
					updatePanCursor();
					hideBrushCursor();
				}
				ev.preventDefault();
			}
		});

		window.addEventListener("keyup", function (ev) {
			if (ev.code === "Space") {
				spaceDown = false;
				panning = false;
				updatePanCursor();
			}
		});

		window.addEventListener("mouseup", function () {
			if (painting && paintedDuringStroke) {
				// Only prune orphan keep islands after an ERASE stroke. A keep-brush
				// stroke may intentionally add a detached keep region (recovering real
				// tissue near the edge); pruning would delete it.
				if (state.mode === "erase") {
					var cleared = pruneOrphanKeepIslands();
					if (cleared > 0 && typeof opts.onOrphansPruned === "function") {
						opts.onOrphansPruned(cleared);
					}
				}
				if (typeof opts.onMaskEdited === "function") {
					opts.onMaskEdited(state.mode === "keep" ? "keep" : "eraser");
				}
			}
			painting = false;
			paintedDuringStroke = false;
			lastStrokePoint = null;
		});
	}

	wirePointerEvents();

	return {
		state: state,
		draw: draw,
		fitToViewport: fitToViewport,
		loadImageUrl: loadImageUrl,
		loadMaskFromBase64: loadMaskFromBase64,
		loadMaskFromFile: loadMaskFromFile,
		exportMaskPngPath: exportMaskPngPath,
		resetMaskAllKeep: resetMaskAllKeep,
		setMaskVisible: setMaskVisible,
		setSliceUntouched: setSliceUntouched,
		maskIsAllKeep: maskIsAllKeep,
		setMode: setMode,
		undo: undo,
		pushUndo: pushUndo,
		getTracePoints: function () {
			return state.tracePoints.slice();
		},
		getTracePointsForJson: function () {
			var pts = state.tracePoints;
			var out = [];
			for (var i = 0; i < pts.length; i++) {
				out.push([Math.round(pts[i].x), Math.round(pts[i].y)]);
			}
			return out;
		},
		clearTrace: function () {
			state.tracePoints = [];
			notifyTraceChange();
			draw();
		},
		setEraserSize: function (n) {
			state.eraserSize = n;
			// A numeric-field edit may occur while the pointer is stationary over
			// the image. Refresh at its last known location instead of waiting for
			// the next mousemove event to resize the visible brush ring.
			if (lastBrushPointer) {
				updateBrushCursor(lastBrushPointer.x, lastBrushPointer.y);
			}
		},
	};
}

module.exports = {
	createTissueCleanupCanvas: createTissueCleanupCanvas,
};
