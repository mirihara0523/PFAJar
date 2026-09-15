"use strict";

/** stdout lines from py/map.py / py/adjust.py that complete viewer-tool IPC. */
var ALIGN_MSG_DONE = "Done!";
var ALIGN_MSG_VIEWER_CLOSED = "Viewer closed";
var ALIGN_MSG_WARPING = "ALIGN_WARPING";
var VIEWER_TOOL_MSG_DONE = ALIGN_MSG_DONE;
var VIEWER_TOOL_MSG_VIEWER_CLOSED = ALIGN_MSG_VIEWER_CLOSED;

/**
 * Classify a python-shell stdout line from map.py.
 * @param {string} message
 * @returns {"done"|"viewer_closed"|"warping"|"other"}
 */
function classifyAlignStdoutMessage(message) {
	var text = String(message || "").trim();
	if (text === ALIGN_MSG_DONE) {
		return "done";
	}
	if (text === ALIGN_MSG_VIEWER_CLOSED) {
		return "viewer_closed";
	}
	if (text === ALIGN_MSG_WARPING) {
		return "warping";
	}
	return "other";
}

/**
 * Parse RESULT:{json} lines from map.py warp completion.
 * @param {string} message
 * @returns {object|null}
 */
function parseAlignResultLine(message) {
	var text = String(message || "").trim();
	if (!text.startsWith("RESULT:")) {
		return null;
	}
	try {
		return JSON.parse(text.slice("RESULT:".length));
	} catch (_err) {
		return null;
	}
}

/**
 * Payload sent to the renderer on alignResult.
 * @param {"done"|"viewer_closed"} kind
 * @param {object} [summary]
 * @returns {{ cancelled: boolean, summary?: object }}
 */
function alignResultPayloadForKind(kind, summary) {
	var payload = { cancelled: kind === "viewer_closed" };
	if (!payload.cancelled && summary && typeof summary === "object") {
		payload.summary = summary;
	}
	return payload;
}

/**
 * Whether alignResult should update active runs / warp report.
 * @param {{ cancelled?: boolean }|null|undefined} response
 * @returns {boolean}
 */
function shouldApplyAlignRunSideEffects(response) {
	return !(response && response.cancelled);
}

function classifyViewerToolStdoutMessage(message) {
	return classifyAlignStdoutMessage(message);
}

/**
 * Payload sent to the renderer on adjustResult / alignResult.
 * @param {"done"|"viewer_closed"} kind
 * @returns {{ cancelled: boolean }}
 */
function viewerToolResultPayloadForKind(kind) {
	return alignResultPayloadForKind(kind);
}

function shouldApplyViewerToolSideEffects(response) {
	return shouldApplyAlignRunSideEffects(response);
}

/**
 * Whether runAlign should finalize as cancelled when the process closes.
 * @param {{ exitCode: number, viewerClosedHandshake?: boolean, sessionSavedOnClose?: boolean, warpingStarted?: boolean }} opts
 */
function shouldTreatAlignCloseAsCancelled(opts) {
	var exitCode = Number(opts && opts.exitCode) || 0;
	if (opts && opts.warpingStarted && exitCode === 0) {
		return false;
	}
	if (exitCode === 0) {
		return true;
	}
	if (opts && opts.viewerClosedHandshake) {
		return true;
	}
	if (opts && opts.sessionSavedOnClose) {
		return true;
	}
	return false;
}

/**
 * Whether runAlign should report a Python failure on process close.
 * @param {{ exitCode: number, viewerClosedHandshake?: boolean, sessionSavedOnClose?: boolean, warpingStarted?: boolean }} opts
 */
function shouldReportAlignCloseFailure(opts) {
	return !shouldTreatAlignCloseAsCancelled(opts);
}

module.exports = {
	ALIGN_MSG_DONE: ALIGN_MSG_DONE,
	ALIGN_MSG_VIEWER_CLOSED: ALIGN_MSG_VIEWER_CLOSED,
	ALIGN_MSG_WARPING: ALIGN_MSG_WARPING,
	VIEWER_TOOL_MSG_DONE: VIEWER_TOOL_MSG_DONE,
	VIEWER_TOOL_MSG_VIEWER_CLOSED: VIEWER_TOOL_MSG_VIEWER_CLOSED,
	classifyAlignStdoutMessage: classifyAlignStdoutMessage,
	classifyViewerToolStdoutMessage: classifyViewerToolStdoutMessage,
	parseAlignResultLine: parseAlignResultLine,
	alignResultPayloadForKind: alignResultPayloadForKind,
	viewerToolResultPayloadForKind: viewerToolResultPayloadForKind,
	shouldApplyAlignRunSideEffects: shouldApplyAlignRunSideEffects,
	shouldApplyViewerToolSideEffects: shouldApplyViewerToolSideEffects,
	shouldTreatAlignCloseAsCancelled: shouldTreatAlignCloseAsCancelled,
	shouldReportAlignCloseFailure: shouldReportAlignCloseFailure,
};
