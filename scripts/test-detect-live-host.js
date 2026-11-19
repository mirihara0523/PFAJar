"use strict";
const assert = require("assert");
const { EventEmitter } = require("events");
const { registerDetectionViewer } = require("../js/detect_live_host");

async function main() {
	const handlers = {};
	const notifications = [];
	let maintenanceHeld = false, maintenanceBlocked = false, transferred = null;
	let currentWindow, workerOptions, completeWorker, sent, killed = false, focused = false;
	class Window extends EventEmitter {
		constructor() { super(); this.webContents = new EventEmitter(); this.webContents.send = (...args) => notifications.push(args); currentWindow = this; }
		isDestroyed() { return false; }
		loadFile(file) { this.file = file; return Promise.resolve(); }
		show() {}
		focus() {}
		maximize() {}
	}
	registerDetectionViewer({
		ipcMain: { handle: (name, fn) => { handlers[name] = fn; } }, BrowserWindow: Window,
		dialog: { showOpenDialog: async () => ({ canceled: true }), showMessageBoxSync: () => 0 },
		openDetectionWizard: async settings => { transferred = settings; },
        acquireMaintenance: () => {
            if (maintenanceBlocked) throw new Error("Close other analysis windows");
            maintenanceHeld = true; return () => { maintenanceHeld = false; };
        },
        appDir: "/fixture", focusMain: () => { focused = true; },
		startJob: (_script, _args, opts) => {
			workerOptions = opts;
			return { pyshell: { send: line => { sent = JSON.parse(line); } },
				wait: () => new Promise(resolve => { completeWorker = resolve; }), kill: () => { killed = true; } };
		},
	});
	handlers.openDetectionViewer();
	assert.ok(currentWindow.file.endsWith("detect_live.html"));
	const event = { sender: currentWindow.webContents };
	assert.throws(() => handlers.detectLiveRequest({ sender: {} }, {}), /Open/);
    const transfer = {version:1,indir:"input",outdir:"output",model:"model.pt",method:"custom",tile:640,confidence:.5,area:0,eccentricity:0,intensity:70};
    await handlers.detectLiveUseSettings(event,transfer);
    assert.strictEqual(transferred.intensity,70);
    await assert.rejects(handlers.detectLiveUseSettings({sender:{}},transfer),/Open/);
	const result = handlers.detectLiveRequest(event, { action: "load", source: "a.tif" });
	assert.strictEqual(sent.action, "load");
    await assert.rejects(handlers.detectLiveUseSettings(event,transfer),/Wait/);
	workerOptions.onMessage(JSON.stringify({ event: "progress", id: sent.id, stage: "reading", current: null, total: null }));
	assert.strictEqual(notifications.at(-1)[0], "detectLiveStatus");
	assert.throws(() => handlers.detectLiveRequest(event, {}), /still running/);
	workerOptions.onMessage(JSON.stringify({ id: sent.id, ok: true, result: { token: "a" } }));
	assert.deepStrictEqual(await result, { token: "a" });
	const failure = handlers.detectLiveRequest(event, { action: "detect" });
	const rejected = assert.rejects(failure, /fixture error/);
	workerOptions.onMessage(JSON.stringify({ id: sent.id, ok: false, error: "fixture error" }));
	await rejected;
	const interrupted = handlers.detectLiveRequest(event, { action: "detect" });
	const interruption = assert.rejects(interrupted, /viewer closed/);
	currentWindow.emit("closed");
	await interruption;
	assert.ok(killed && focused);
	completeWorker({ code: 0 });
	handlers.openSectionOrderViewer();
	assert.ok(currentWindow.file.endsWith("section_order.html"));
	assert.throws(() => handlers.sectionOrderRequest({ sender: currentWindow.webContents }, { action: "detect" }), /Unsupported/);
	const orderWindow = currentWindow;
	handlers.openDetectionViewer();
	const detectionWindow = currentWindow;
	assert.throws(() => handlers.sectionOrderRequest({ sender: detectionWindow.webContents }, { action: "order_info" }), /Open/);
	assert.throws(() => handlers.detectLiveRequest({ sender: orderWindow.webContents }, { action: "load" }), /Open/);
    const orderEvent = {sender: orderWindow.webContents};
    maintenanceBlocked = true;
    assert.throws(() => handlers.sectionOrderRequest(orderEvent, {action:"order_flip"}), /Close other/);
    maintenanceBlocked = false;
    const flip = handlers.sectionOrderRequest(orderEvent, {action:"order_flip"});
    assert.ok(maintenanceHeld);
    workerOptions.onMessage(JSON.stringify({id:sent.id,ok:true,result:{backup:"fixture"}}));
    await flip;
    assert.ok(!maintenanceHeld);
    const stoppedFlip = handlers.sectionOrderRequest(orderEvent, {action:"order_flip"});
    const stopped = assert.rejects(stoppedFlip,/viewer closed/);
    orderWindow.emit("closed");
    assert.ok(maintenanceHeld, "hold maintenance until the Python child has exited");
    completeWorker({code:1}); await stopped; await Promise.resolve();
    assert.ok(!maintenanceHeld);
    detectionWindow.emit("closed");
	console.log("Detection viewer host: singleton window, sender guard, serialized requests, failures and child cleanup passed.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
