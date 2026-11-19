"use strict";

const path = require("path");

function registerWorkerViewer(options, config) {
	const { ipcMain, BrowserWindow, dialog, appDir, startJob, focusMain } = options;
	let viewer = null;
	let job = null;
	let pending = null;
	let sequence = 0;
	let releaseMaintenance = null;

	function finishPending(error, result, release = true) {
		if (release && releaseMaintenance) { releaseMaintenance(); releaseMaintenance = null; }
		if (!pending) return;
		const request = pending;
		pending = null;
		if (error) request.reject(error);
		else request.resolve(result);
	}

	function ensureWorker() {
		if (job) return;
		const started = startJob("detect_live_worker.py", [], {
			forceShell: true,
			onMessage: function (message) {
				let reply;
				try { reply = JSON.parse(message); } catch (_error) { console.log(message); return; }
				if (!pending || reply.id !== pending.id) return;
				if (reply.event === "progress") {
					if (viewer && !viewer.isDestroyed()) viewer.webContents.send(config.status, reply);
					return;
				}
				finishPending(reply.ok ? null : new Error(reply.error), reply.result);
			},
			onStderr: function (line) {
				console.log(line);
				if (viewer && !viewer.isDestroyed()) viewer.webContents.send(config.progress, line);
			},
		});
		job = started;
		void started.wait().then(function (exit) {
			if (job !== started) {
				if (releaseMaintenance) { releaseMaintenance(); releaseMaintenance = null; }
				return;
			}
			job = null;
			finishPending(new Error("Detection worker stopped. Load the image again to retry."));
			console.log("LOG: detect_live worker_exit code=" + exit.code);
		});
	}

	ipcMain.handle(config.open, function () {
		if (viewer && !viewer.isDestroyed()) {
			viewer.show(); viewer.focus(); return;
		}
		viewer = new BrowserWindow({
			title: config.title + " — PFA Jar", width: 1400, height: 900,
			show: false, webPreferences: { nodeIntegration: true, contextIsolation: false },
		});
		viewer.once("ready-to-show", function () { viewer.maximize(); viewer.show(); viewer.focus(); });
		viewer.webContents.on("will-prevent-unload", function (event) {
			const answer = dialog.showMessageBoxSync(viewer, {
				type: "question", buttons: ["Keep working", "Close viewer"], defaultId: 0, cancelId: 0,
				message: "Close " + config.title + " and discard unsaved changes?",
				detail: "Any operation still running in this window will stop.",
			});
			if (answer === 1) event.preventDefault();
		});
		viewer.on("closed", function () {
			if (job) { const previous = job; job = null; previous.kill(); }
			finishPending(new Error("Detection viewer closed."), null, false);
			viewer = null;
			focusMain();
		});
		void viewer.loadFile(path.join(appDir, "pages", config.page));
	});

	function assertViewer(event) {
		if (!viewer || viewer.isDestroyed() || event.sender !== viewer.webContents) {
			throw new Error("Open " + config.title + " first.");
		}
	}
	ipcMain.handle(config.browse, async function (event, kind) {
		assertViewer(event);
		const result = await dialog.showOpenDialog(viewer, {
			properties: [kind === "model" ? "openFile" : "openDirectory"],
			filters: kind === "model" ? [{ name: "YOLO model", extensions: ["pt"] }] : undefined,
		});
		return result.canceled ? "" : result.filePaths[0];
	});
    if (config.handoff) ipcMain.handle(config.handoff, async function(event, payload) {
        assertViewer(event);
        if (pending) throw new Error("Wait for the current Viewer operation to finish.");
        if (!options.openDetectionWizard) throw new Error("Restart PFA Jar to enable settings transfer.");
        const settings = require("./detect_handoff").normalize(payload);
        await options.openDetectionWizard(settings, job ? job.jobId : null);
        return {opened:true};
    });
	ipcMain.handle(config.request, function (event, request) {
		assertViewer(event);
		if (pending) throw new Error("The previous detection operation is still running.");
		if (!config.actions.includes(request.action)) throw new Error("Unsupported operation for this viewer.");
		ensureWorker();
		if (request.action === "order_flip" || request.recover_flip) {
			if (!options.acquireMaintenance) throw new Error("Restart PFA Jar to enable safe section flipping.");
			releaseMaintenance = options.acquireMaintenance(job.jobId);
		}
		return new Promise(function (resolve, reject) {
			const id = ++sequence;
			pending = { id: id, resolve: resolve, reject: reject };
			try { job.pyshell.send(JSON.stringify(Object.assign({}, request, { id: id }))); }
			catch (error) { finishPending(error); }
		});
	});
}

function registerDetectionViewer(options) {
	registerWorkerViewer(options, {
		title: "Cell Detection Viewer", page: "detect_live.html", open: "openDetectionViewer",
		handoff: "detectLiveUseSettings", browse: "detectLiveBrowse", request: "detectLiveRequest", progress: "detectLiveProgress", status: "detectLiveStatus",
		actions: ["load", "detect", "filter", "save", "order_info"],
	});
	registerWorkerViewer(options, {
		title: "Section Manager", page: "section_order.html", open: "openSectionOrderViewer",
		browse: "sectionOrderBrowse", request: "sectionOrderRequest", progress: "sectionOrderProgress", status: "sectionOrderStatus",
		actions: ["order_info", "order_save", "order_preview", "order_flip_plan", "order_flip"],
	});
}

module.exports = { registerDetectionViewer };
