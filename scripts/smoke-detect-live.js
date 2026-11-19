"use strict";
// Run with Electron. Uses a mocked inference worker and temporary fixture only.
const { app, BrowserWindow, ipcMain, nativeImage } = require("electron");
const fs = require("fs"), path = require("path"), assert = require("assert");
const { registerDetectionViewer } = require("../js/detect_live_host");
const root = path.resolve(__dirname, "..");
const temporary = fs.mkdtempSync(path.join(root, "../test-temp/detect-live-ui-"));
app.setPath("userData", path.join(temporary, "profile"));
app.setPath("crashDumps", path.join(temporary, "crash"));
let viewer;
class HiddenWindow extends BrowserWindow {
	constructor(options) { super(options); viewer = this; }
	show() {}
	focus() {}
	maximize() {}
}
const calls = [];
let failNextSave = false, stopNextSave = false;
let showDetectionProgress = true;
let savedOrder = [];
let transferred = null;
let currentSource = "", lastInference = {};
const savedSlices = new Map();
const records = [[5, 5, 20, 20, .8, 225, .5, 100], [25, 5, 40, 20, .7, 225, .6, 180]];
registerDetectionViewer({
	openDetectionWizard: async payload => { transferred=payload; },
	ipcMain, BrowserWindow: HiddenWindow, dialog: { showMessageBoxSync: () => 1 }, appDir: root, focusMain: () => {},
	startJob: (_script, _args, options) => ({
		pyshell: { send: line => {
			const request = JSON.parse(line); calls.push(request.action);
			let result;
			if (request.action === "order_info") result = { saved_order: savedOrder, ap_positions: { "second.slice": 200, "slice.with.dots": 100 }, warnings: [] };
			if (request.action === "order_save") { savedOrder = request.slice_ids; result = { saved_order: savedOrder }; }
			if (request.action === "load") { currentSource = request.source; result = {
                saved_result: savedSlices.get(request.output + "|" + request.source) || null,
				token: "fixture", width: 64, height: 48,
				image: nativeImage.createFromBitmap(Buffer.alloc(64 * 48 * 4, 120), { width: 64, height: 48 }).toDataURL(),
				original_image: nativeImage.createFromBitmap(Buffer.alloc(64 * 48 * 4, 80), { width: 64, height: 48 }).toDataURL(),
			}; }
            if (request.action === "detect") { lastInference = {model:request.model,tile:request.tile,floor:request.floor}; result = { records, floor: .1, cached: false, device: "fixture" }; }
			if (request.action === "filter") result = { indices: records.map((_row, i) => i).filter(i => records[i][7] >= request.params.intensity) };
			if (request.action === "save") {
                result = { path: path.join(temporary, "Predictions_slice.with.dots.pkl"), count: 1 };
                if (!failNextSave) savedSlices.set(request.output + "|" + currentSource, {records, indices:records.map((_row,i)=>i).filter(i=>records[i][7]>=request.params.intensity), params:request.params, floor:.1, inference:lastInference, method:request.method || "custom"});
            }
			setImmediate(async () => {
				if (request.action === "detect" && showDetectionProgress) {
					showDetectionProgress = false;
					options.onMessage(JSON.stringify({ event: "progress", id: request.id, stage: "inference", current: 1, total: 4, idle_ms: 0 }));
					await new Promise(resolve => setTimeout(resolve, 400));
				}
				if (request.action === "save" && stopNextSave) {
					stopNextSave = false;
					await viewer.webContents.executeJavaScript("document.getElementById('stopAll').click()");
				}
				if (request.action === "save" && failNextSave) {
					failNextSave = false;
					options.onMessage(JSON.stringify({ id: request.id, ok: false, error: "fixture save failure" }));
				} else options.onMessage(JSON.stringify({ id: request.id, ok: true, result }));
			});
		} }, wait: () => new Promise(() => {}), kill: () => {},
	}),
});
const errors = [];
async function waitFor(expression) {
	for (let i = 0; i < 100; i++) {
		if (await viewer.webContents.executeJavaScript(expression)) return;
		await new Promise(resolve => setTimeout(resolve, 50));
	}
	const diagnostic = await viewer.webContents.executeJavaScript("({input:document.getElementById('input')?.value,status:document.getElementById('status')?.textContent,sections:document.getElementById('slice')?.textContent,workspace:localStorage.getItem('masonjar.workspace')})");
	throw new Error("Timed out waiting for " + expression + " " + JSON.stringify(diagnostic));
}
app.whenReady().then(async () => {
	fs.writeFileSync(path.join(temporary, "slice.with.dots.tif"), "fixture");
	fs.writeFileSync(path.join(temporary, "second.slice.tif"), "fixture");
	const opener = new BrowserWindow({ show: false, webPreferences: { nodeIntegration: true, contextIsolation: false } });
	await opener.loadURL("about:blank");
	await opener.webContents.executeJavaScript("require('electron').ipcRenderer.invoke('openDetectionViewer')");
	viewer.webContents.on("console-message", (_event, level, message) => { if (level >= 3) errors.push(message); });
	await waitFor("!!document.getElementById('loadFolder') && document.getElementById('detect').disabled");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('plane')"), null, "single-channel viewer must not show a plane selector");
	const initialOutput = await viewer.webContents.executeJavaScript("document.getElementById('output').value");
	assert.ok(path.isAbsolute(initialOutput), "output default must be present immediately on opening");
	assert.ok(!fs.existsSync(initialOutput), "showing the default must not create an output folder");
	const customOutput = path.join(temporary, "custom-predictions");
	await viewer.webContents.executeJavaScript(`document.getElementById('output').value=${JSON.stringify(customOutput)}`);
	await viewer.webContents.executeJavaScript(`document.getElementById('input').value=${JSON.stringify(temporary)}; document.getElementById('loadFolder').click();`);
	await waitFor("!document.getElementById('detect').disabled");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('output').value"), customOutput, "manual output must survive loading an input folder");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('sectionSort')"), null, "section editing belongs to the standalone order viewer");
	await viewer.webContents.executeJavaScript("document.getElementById('goTo').click(); document.getElementById('sectionNumber').value='0'; document.getElementById('goToForm').requestSubmit()");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('goToDialog').open"), true, "out-of-range section must stay in the dialog");
	await viewer.webContents.executeJavaScript("document.getElementById('sectionNumber').value='2'; document.getElementById('goToForm').requestSubmit()");
	await waitFor("document.getElementById('slice').value==='1' && !document.getElementById('detect').disabled");
	await viewer.webContents.executeJavaScript("document.getElementById('goTo').click(); document.getElementById('sectionNumber').value='1'; document.getElementById('goToForm').requestSubmit()");
	await waitFor("document.getElementById('slice').value==='0' && !document.getElementById('detect').disabled");
	await viewer.webContents.executeJavaScript("document.getElementById('detect').click()");
	await waitFor("document.getElementById('workStage').textContent.includes('1 / 4 (25%)')");
	assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('workResponse').textContent")).includes("Worker responding"));
	await waitFor("document.getElementById('count').textContent === '2 kept / 2 candidates'");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('workStage').textContent"), "Processing completed");
    await viewer.webContents.executeJavaScript("document.getElementById('useInDetection').click()");
    await waitFor("document.getElementById('status').textContent.startsWith('Settings sent')");
    assert.ok(transferred && transferred.tile===640 && transferred.confidence===.5);
    assert.strictEqual(calls.filter(action=>action==='detect').length,1,"handoff must not start another detection");
	const callsBeforeHover = calls.length;
	await viewer.webContents.executeJavaScript(`
		const view = document.getElementById('viewport'), bounds = view.getBoundingClientRect();
		const scale = Math.min(view.clientWidth/64,view.clientHeight/48)*.95;
		view.dispatchEvent(new PointerEvent('pointermove',{
			clientX:bounds.left+(view.clientWidth-64*scale)/2+30*scale,
			clientY:bounds.top+(view.clientHeight-48*scale)/2+10*scale
		}));
	`);
	await waitFor("!document.getElementById('bboxInfo').hidden");
	const tooltipText = await viewer.webContents.executeJavaScript("document.getElementById('bboxInfo').textContent");
	assert.ok(tooltipText.includes("BBox #2") && tooltipText.includes("Confidence ≥ 0.7001") && tooltipText.includes("Intensity cutoff ≥ 181"));
	assert.strictEqual(calls.length, callsBeforeHover, "hover information must not rerun detection or call the worker");
	await viewer.webContents.executeJavaScript("document.getElementById('boxes').click()");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('bboxInfo').hidden"), true);
	await viewer.webContents.executeJavaScript("document.getElementById('boxes').click()");
	await viewer.webContents.executeJavaScript("document.getElementById('intensity').value='150'; document.getElementById('intensity').dispatchEvent(new Event('input'))");
	await waitFor("document.getElementById('count').textContent === '1 kept / 2 candidates'");
	assert.strictEqual(calls.filter(action => action === "detect").length, 1, "filter edits must not rerun inference");
	await viewer.webContents.executeJavaScript("document.getElementById('intensity').value=''; document.getElementById('intensity').dispatchEvent(new Event('input'))");
	await waitFor("document.getElementById('status').classList.contains('error') && document.getElementById('save').disabled");
	await viewer.webContents.executeJavaScript("document.getElementById('intensity').value='150'; document.getElementById('intensity').dispatchEvent(new Event('input'))");
	await waitFor("!document.getElementById('status').classList.contains('error') && !document.getElementById('save').disabled");
	await viewer.webContents.executeJavaScript("document.getElementById('save').click()");
	await waitFor("document.getElementById('status').textContent.startsWith('Saved 1 cell.')");
	assert.strictEqual(calls.filter(action => action === "detect").length, 1, "default detect must process only the current slice");
	await viewer.webContents.executeJavaScript("window.confirm=()=>true; document.getElementById('applyAll').click()");
	await waitFor("document.getElementById('status').textContent.startsWith('Completed. 2 slices saved, 0 failed.')");
	assert.strictEqual(calls.filter(action => action === "save").length, 3, "one calibration save and two bulk saves expected");
	assert.strictEqual(calls.filter(action => action === "detect").length, 3, "one calibration and two bulk detections; restoration must not infer again");
    const detectedBeforeNavigation = calls.filter(action=>action==="detect").length;
    const bulkOutput = await viewer.webContents.executeJavaScript("document.getElementById('output').value");
    assert.ok(bulkOutput.endsWith("_all"));
    await viewer.webContents.executeJavaScript("document.getElementById('next').click()");
    await waitFor("document.getElementById('slice').value==='1' && document.getElementById('status').textContent.startsWith('Saved BBoxes')");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('intensity').value"),"150");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('count').textContent"),"1 kept / 2 candidates");
    await viewer.webContents.executeJavaScript("document.getElementById('previous').click()");
    await waitFor("document.getElementById('slice').value==='0' && document.getElementById('status').textContent.startsWith('Saved BBoxes')");
    assert.strictEqual(calls.filter(action=>action==="detect").length,detectedBeforeNavigation,"navigation after Apply All must not rerun inference");
	let summary = JSON.parse(fs.readFileSync(path.join(await viewer.webContents.executeJavaScript("document.getElementById('output').value"), "live_batch_summary.json")));
	assert.strictEqual(summary.saved.length, 2);
	assert.strictEqual(summary.params.intensity, 150);
	stopNextSave = true;
	await viewer.webContents.executeJavaScript("document.getElementById('applyAll').click()");
	await waitFor("document.getElementById('status').textContent.startsWith('Stopped. 1 slices saved, 0 failed.')");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('detect').disabled"), false);
	failNextSave = true;
	await viewer.webContents.executeJavaScript("document.getElementById('applyAll').click()");
	await waitFor("document.getElementById('status').textContent.startsWith('Completed. 1 slices saved, 1 failed.')");
	summary = JSON.parse(fs.readFileSync(path.join(await viewer.webContents.executeJavaScript("document.getElementById('output').value"), "live_batch_summary.json")));
	assert.strictEqual(summary.failed.length, 1);
	assert.strictEqual(summary.saved.length, 1);
	const layout = await viewer.webContents.executeJavaScript("({ canvas:document.getElementById('viewport').getBoundingClientRect().toJSON(), panel:document.querySelector('aside').getBoundingClientRect().toJSON() })");
	assert.ok(layout.canvas.right <= layout.panel.left + 1);
	await viewer.webContents.executeJavaScript(`
		const originalDraw = CanvasRenderingContext2D.prototype.drawImage;
		CanvasRenderingContext2D.prototype.drawImage = function (...args) {
			window.lastImageTransform = this.getTransform().toJSON();
			return originalDraw.apply(this, args);
		};
		document.getElementById('viewport').dispatchEvent(new WheelEvent('wheel', {deltaY:-100, clientX:200, clientY:200, cancelable:true}));
	`);
	const beforeResize = await viewer.webContents.executeJavaScript("({ transform:window.lastImageTransform, width:document.getElementById('viewport').clientWidth, bar:document.getElementById('optionsSplitter').getBoundingClientRect().toJSON() })");
	const callsBeforeToggle = calls.length;
	const displayedBefore = await viewer.webContents.executeJavaScript("document.getElementById('imageCanvas').toDataURL()");
	await viewer.webContents.executeJavaScript("document.getElementById('original').click()");
	const displayedOriginal = await viewer.webContents.executeJavaScript("document.getElementById('imageCanvas').toDataURL()");
	assert.notStrictEqual(displayedOriginal, displayedBefore, "original toggle must change the displayed pixels");
	assert.deepStrictEqual(await viewer.webContents.executeJavaScript("window.lastImageTransform"), beforeResize.transform, "original toggle must preserve view transform");
	assert.strictEqual(calls.length, callsBeforeToggle, "original toggle must not call the worker");
	await viewer.webContents.executeJavaScript("document.getElementById('original').click()");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('imageCanvas').toDataURL()"), displayedBefore, "toggle back must restore the same preview and BBoxes");
	const dividerX = Math.round(beforeResize.bar.left + 4), dividerY = Math.round(beforeResize.bar.top + 100);
	// Hidden windows do not receive OS mouse input. Dispatch pointer events to
	// exercise the production drag handlers; capture needs an active OS pointer.
	await viewer.webContents.executeJavaScript(`
		const bar = document.getElementById('optionsSplitter');
		bar.setPointerCapture = () => {};
		bar.dispatchEvent(new PointerEvent('pointerdown',{button:0,pointerId:7,clientX:${dividerX},clientY:${dividerY}}));
		bar.dispatchEvent(new PointerEvent('pointermove',{pointerId:7,clientX:${dividerX - 100},clientY:${dividerY}}));
		bar.dispatchEvent(new PointerEvent('pointerup',{pointerId:7,clientX:${dividerX - 100},clientY:${dividerY}}));
	`);
	await waitFor(`document.querySelector('aside').getBoundingClientRect().width > ${layout.panel.width + 70}`);
	await viewer.webContents.executeJavaScript("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
	const afterResize = await viewer.webContents.executeJavaScript("({ transform:window.lastImageTransform, width:document.getElementById('viewport').clientWidth })");
	assert.strictEqual(afterResize.transform.a, beforeResize.transform.a, "dragging sidebar must preserve zoom");
	const oldCenter = (beforeResize.width / 2 - beforeResize.transform.e) / beforeResize.transform.a;
	const newCenter = (afterResize.width / 2 - afterResize.transform.e) / afterResize.transform.a;
	assert.ok(Math.abs(oldCenter - newCenter) < .01, "dragging sidebar must preserve the viewed image center");
	await viewer.webContents.executeJavaScript("for(let i=0;i<100;i++)document.getElementById('optionsSplitter').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'}))");
	await waitFor("document.querySelector('aside').getBoundingClientRect().width===220");
	await viewer.webContents.executeJavaScript("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
	fs.mkdirSync(path.join(temporary, "artifacts"));
	fs.writeFileSync(path.join(temporary, "artifacts", "viewer.png"), (await viewer.webContents.capturePage()).toPNG());
	assert.deepStrictEqual(errors, []);
	await viewer.webContents.executeJavaScript("document.getElementById('intensity').value='151'; document.getElementById('intensity').dispatchEvent(new Event('input'))");
	await waitFor("!document.getElementById('save').disabled");
	const loadsBeforeCancel = calls.filter(action => action === "load").length;
	await viewer.webContents.executeJavaScript("document.getElementById('next').click()");
	await waitFor("document.getElementById('unsavedDialog').open");
	await viewer.webContents.executeJavaScript("document.getElementById('unsavedCancel').click()");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('slice').value"), "0");
	assert.strictEqual(calls.filter(action => action === "load").length, loadsBeforeCancel);
	failNextSave = true;
	await viewer.webContents.executeJavaScript("document.getElementById('next').click()");
	await waitFor("document.getElementById('unsavedDialog').open");
	await viewer.webContents.executeJavaScript("document.getElementById('unsavedSave').click()");
	await waitFor("document.getElementById('status').classList.contains('error') && !document.getElementById('save').disabled");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('slice').value"), "0", "failed save must retain the current section");
	assert.strictEqual(calls.filter(action => action === "load").length, loadsBeforeCancel);
	await viewer.webContents.executeJavaScript("document.getElementById('next').click()");
	await waitFor("document.getElementById('unsavedDialog').open");
	await viewer.webContents.executeJavaScript("document.getElementById('unsavedSave').click()");
	await waitFor("document.getElementById('slice').value==='1' && !document.getElementById('detect').disabled");
	assert.deepStrictEqual(calls.slice(-2), ["save", "load"], "successful save must complete before the next section loads");
	await viewer.webContents.executeJavaScript("document.getElementById('detect').click()");
	await waitFor("!document.getElementById('save').disabled");
	const savesBeforeDiscard = calls.filter(action => action === "save").length;
	await viewer.webContents.executeJavaScript("document.getElementById('previous').click()");
	await waitFor("document.getElementById('unsavedDialog').open");
	await viewer.webContents.executeJavaScript("document.getElementById('unsavedDiscard').click()");
	await waitFor("document.getElementById('slice').value==='0' && !document.getElementById('detect').disabled");
	assert.strictEqual(calls.filter(action => action === "save").length, savesBeforeDiscard);
	await viewer.webContents.executeJavaScript("document.getElementById('tile').dispatchEvent(new Event('change')); document.getElementById('next').click()");
	await waitFor("document.getElementById('unsavedDialog').open");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('unsavedSave').disabled"), true, "save must stay disabled without valid detected candidates");
	await viewer.webContents.executeJavaScript("document.getElementById('unsavedDialog').dispatchEvent(new Event('cancel',{cancelable:true}))");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('slice').value"), "0");
	const detectionCount = calls.filter(action => action === "detect").length;
	await viewer.webContents.executeJavaScript(`localStorage.setItem('masonjar.workspace',${JSON.stringify(JSON.stringify({ paths: { max: temporary, predictions: customOutput } }))})`);
	savedOrder = ["slice.with.dots", "second.slice"];
	viewer.destroy();
	await opener.webContents.executeJavaScript("require('electron').ipcRenderer.invoke('openDetectionViewer')");
	await waitFor("!!document.getElementById('detect') && document.getElementById('slice').options.length===2 && !document.getElementById('detect').disabled");
	assert.strictEqual(calls.filter(action => action === "detect").length, detectionCount, "opening must load sections without starting detection");
	assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('slice').selectedOptions[0].textContent")).includes("slice.with.dots.tif"), "detection viewer must follow the shared order");
	const emptyFolder = path.join(temporary, "empty");
	fs.mkdirSync(emptyFolder);
	await viewer.webContents.executeJavaScript(`document.getElementById('input').value=${JSON.stringify(emptyFolder)}; document.getElementById('input').dispatchEvent(new Event('change'))`);
	await waitFor("document.getElementById('slice').disabled && document.getElementById('slice').textContent==='No sections loaded'");
	assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('goTo').disabled"), true);
	await viewer.webContents.executeJavaScript(`document.getElementById('input').value=${JSON.stringify(temporary)}; document.getElementById('input').dispatchEvent(new Event('change'))`);
	await waitFor("document.getElementById('slice').options.length===2 && !document.getElementById('detect').disabled");
	console.log("Detection viewer UI: load, detect, live filtering without reinference, save, and side-by-side layout passed. " + temporary);
	viewer.destroy(); opener.destroy(); app.exit(0);
}).catch(error => { console.error(error); app.exit(1); });
