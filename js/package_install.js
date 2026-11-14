"use strict";

const { spawn } = require("child_process");
const { StringDecoder } = require("string_decoder");
let sequence = 0;
const MAX_LINES = 150;

/** Stream pip output without buffering the entire installation in exec(). */
function installRequirements(options) {
	const id = "packages-" + Date.now() + "-" + (++sequence);
	const startedAt = Date.now();
	let lastOutputAt = startedAt, activity = "Starting pip…";
	let pending = [], tail = [], done = false;
	let flushTimer, heartbeat;
	const streams = [
		{ decoder: new StringDecoder("utf8"), partial: "" },
		{ decoder: new StringDecoder("utf8"), partial: "" },
	];
	function notify(state) {
		const now = Date.now();
		const event = { id, state, startedAt, lastOutputAt, elapsedMs: now - startedAt,
			quietMs: now - lastOutputAt, activity, lines: pending.splice(0) };
		try { if (options.onEvent) options.onEvent(event); } catch (_error) { /* UI may close. */ }
	}
	function line(text) {
		text = text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "").trim();
		if (!text) return;
		text = text.slice(-8192);
		lastOutputAt = Date.now();
		activity = text;
		tail.push(text); if (tail.length > MAX_LINES) tail.shift();
		pending.push(text); if (pending.length > MAX_LINES) pending.shift();
		try { if (options.onLog) options.onLog(text); } catch (_error) { /* Best effort. */ }
		if (!flushTimer) flushTimer = setTimeout(() => { flushTimer = null; notify("running"); }, 200);
	}
	function consume(index, chunk, end = false) {
		const stream = streams[index];
		const text = stream.partial + (end ? stream.decoder.end() : stream.decoder.write(chunk));
		const parts = text.split(/\r\n|\n|\r/);
		stream.partial = parts.pop().slice(-8192);
		for (const part of parts) line(part);
		if (end && stream.partial) { line(stream.partial); stream.partial = ""; }
	}
	return new Promise((resolve, reject) => {
		function finish(error) {
			if (done) return;
			done = true;
			consume(0, null, true); consume(1, null, true);
			clearTimeout(flushTimer); clearInterval(heartbeat);
			notify(error ? "failed" : "complete");
			if (error) {
				error.stderr = tail.join("\n");
				reject(error);
			} else resolve();
		}
		const args = ["-u", "-m", "pip", "install", "-r", options.requirementsPath,
			"--use-pep517", "--progress-bar", "off"];
		if (options.noCache) args.push("--no-cache-dir");
		notify("running");
		let child;
		try {
			child = (options.spawn || spawn)(options.pythonPath, args, {
				cwd: options.cwd, windowsHide: true, shell: false,
				env: { ...options.env, PYTHONUNBUFFERED: "1", PIP_PROGRESS_BAR: "off" },
				stdio: ["ignore", "pipe", "pipe"],
			});
		} catch (error) { finish(error); return; }
		child.stdout.on("data", chunk => { if (!done) consume(0, chunk); });
		child.stderr.on("data", chunk => { if (!done) consume(1, chunk); });
		child.once("error", finish);
		child.once("close", (code, signal) => {
			if (code === 0) finish();
			else finish(new Error("Package installation stopped (exit " + code +
				(signal ? ", signal " + signal : "") + ")."));
		});
		heartbeat = setInterval(() => notify("running"), 1000);
	});
}

module.exports = { installRequirements };
