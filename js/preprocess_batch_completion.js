"use strict";

/**
 * Decide whether a sharpen/tophat batch Python run succeeded for IPC.
 * Exported for unit tests and main process spawnPreprocessBatch.
 */

function evaluatePreprocessBatchResult(state) {
	state = state || {};
	var runFailed = !!state.runFailed;
	var exitCode =
		typeof state.exitCode === "number" ? state.exitCode : Number(state.exitCode) || 0;
	var pyFail = state.pyFail || "";
	var total = typeof state.total === "number" ? state.total : 0;
	var completedCount =
		typeof state.completedCount === "number" ? state.completedCount : 0;
	var failMessage = state.failMessage || "";

	if (runFailed) {
		return {
			ok: false,
			message: failMessage || pyFail || "Processing failed.",
		};
	}

	var someCompleted = completedCount > 0;
	var allCompleted = total > 0 && completedCount >= total;

	if (!someCompleted) {
		return {
			ok: false,
			message: failMessage || "Process ended without writing outputs.",
		};
	}

	if (exitCode === 0 && !pyFail) {
		return { ok: true, message: "" };
	}

	if (allCompleted) {
		var warn =
			"Outputs were written but Python reported a non-zero exit " +
			"(code " +
			exitCode +
			"). Check run_manifest.json in the output folder.";
		if (pyFail) {
			warn = pyFail + " " + warn;
		}
		return { ok: true, message: warn, warnOnly: true };
	}

	return {
		ok: false,
		message: failMessage || pyFail || "Python exited with code " + exitCode,
	};
}

module.exports = {
	evaluatePreprocessBatchResult: evaluatePreprocessBatchResult,
};
