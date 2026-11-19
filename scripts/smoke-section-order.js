"use strict";
const { app, BrowserWindow, ipcMain, nativeImage } = require("electron");
const fs = require("fs"), path = require("path"), assert = require("assert");
const { registerDetectionViewer } = require("../js/detect_live_host");
const root = path.resolve(__dirname, "..");
const temporary = fs.mkdtempSync(path.join(root, "../test-temp/section-order-ui-"));
app.setPath("userData", path.join(temporary, "profile"));
app.setPath("crashDumps", path.join(temporary, "crash"));
let viewer, saved = [], states = {};
const calls = [];
class HiddenWindow extends BrowserWindow {
	constructor(options) { super(options); viewer = this; }
	show() {} focus() {} maximize() {}
}
registerDetectionViewer({
	ipcMain, BrowserWindow: HiddenWindow, appDir: root, focusMain: () => {}, acquireMaintenance: () => () => {}, dialog: { showMessageBoxSync: () => 1 },
	startJob: (_script, _args, options) => ({
		pyshell: { send: line => {
			const request = JSON.parse(line); calls.push(request.action);
			let result, error;
			if (request.action === "order_info") result = { sections: states, saved_order: saved, ap_positions: { s1: 120, s2: 100 }, warnings: [], path: path.join(temporary, "slice_order.json") };
			if (request.action === "order_flip_plan") result = {files:[{},{}],bytes:1024};
            if (request.action === "order_flip") { states[request.slice_id] = Object.assign({}, states[request.slice_id], {flip_x:!states[request.slice_id]?.flip_x}); result = {sections:states,backup:"fixture backup"}; }
            if (request.action === "order_preview") result = { image: nativeImage.createFromBitmap(Buffer.alloc(64 * 48 * 4, 120), { width: 64, height: 48 }).toDataURL() };
			if (request.action === "order_save") {
				if (JSON.stringify(request.expected) !== JSON.stringify(saved)) error = "Slice order changed in another window.";
				else { saved = request.slice_ids; states = Object.fromEntries(Object.entries(request.active || {}).map(([id, active]) => [id, {active}])); result = { saved_order: saved, sections: states }; }
			}
			setImmediate(() => options.onMessage(JSON.stringify({ id: request.id, ok: !error, result, error })));
		} }, wait: () => new Promise(() => {}), kill: () => {},
	}),
});
async function waitFor(expression) {
	for (let i = 0; i < 100; i++) {
		if (await viewer.webContents.executeJavaScript(expression)) return;
		await new Promise(resolve => setTimeout(resolve, 50));
	}
	throw new Error("Timed out: " + expression);
}
app.whenReady().then(async () => {
	for (const name of ["s10.png", "s2.png", "s1.png"]) fs.writeFileSync(path.join(temporary, name), "fixture");
	const opener = new BrowserWindow({ show: false, webPreferences: { nodeIntegration: true, contextIsolation: false } });
	await opener.loadURL("about:blank");
	await opener.webContents.executeJavaScript("require('electron').ipcRenderer.invoke('openSectionOrderViewer')");
	await waitFor("!!document.getElementById('reloadOrder')");
	await viewer.webContents.executeJavaScript(`document.getElementById('orderInput').value=${JSON.stringify(temporary)}; document.getElementById('reloadOrder').click()`);
	await waitFor("!document.getElementById('sectionPreview').hidden && !document.getElementById('reloadOrder').disabled");
	assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).startsWith("1. s1.png"));
	await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='ap_ascending'; document.getElementById('orderMode').dispatchEvent(new Event('change')); document.getElementById('saveSectionOrder').click()");
	await waitFor("!document.getElementById('saveSectionOrder').disabled === false");
	assert.deepStrictEqual(saved, ["s2", "s1", "s10"]);
	assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('count').textContent")).includes("s1.png"));
	await viewer.webContents.executeJavaScript("document.getElementById('orderList').children[2].click()");
	await waitFor("!document.getElementById('orderUp').disabled");
	await viewer.webContents.executeJavaScript(`
		document.getElementById('orderUp').click(); document.getElementById('orderDown').click();
		const list=document.getElementById('orderList'), transfer=new DataTransfer();
		list.children[2].dispatchEvent(new DragEvent('dragstart',{dataTransfer:transfer}));
		list.children[0].dispatchEvent(new DragEvent('drop',{dataTransfer:transfer}));
		document.getElementById('saveSectionOrder').click();
	`);
	await waitFor("document.getElementById('saveSectionOrder').disabled && document.getElementById('orderStatus').textContent.startsWith('Section order and active states saved')");
	assert.deepStrictEqual(saved, ["s10", "s2", "s1"]);
	saved = ["s1", "s2", "s10"];
	await viewer.webContents.executeJavaScript("document.getElementById('orderDown').click(); document.getElementById('saveSectionOrder').click()");
	await waitFor("document.getElementById('orderStatus').classList.contains('error')");
	assert.deepStrictEqual(saved, ["s1", "s2", "s10"], "conflict must preserve the external order");
	await viewer.webContents.executeJavaScript("window.confirm=()=>true; document.getElementById('reloadOrder').click()");
	await waitFor("!document.getElementById('reloadOrder').disabled && document.getElementById('saveSectionOrder').disabled");
	await viewer.webContents.executeJavaScript(`localStorage.setItem('masonjar.workspace',${JSON.stringify(JSON.stringify({ paths: { dapi: temporary } }))})`);
	viewer.destroy();
	await opener.webContents.executeJavaScript("require('electron').ipcRenderer.invoke('openSectionOrderViewer')");
	await waitFor("!!document.getElementById('sectionPreview') && !document.getElementById('sectionPreview').hidden");
	assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).startsWith("1. s1.png"));
    await viewer.webContents.executeJavaScript("document.getElementById('sectionActive').click(); document.getElementById('saveSectionOrder').click()");
    await waitFor("document.getElementById('saveSectionOrder').disabled && !document.getElementById('reloadOrder').disabled");
    assert.ok(Object.values(states).some(state => state.active === false));
    await viewer.webContents.executeJavaScript("document.getElementById('reloadOrder').click()");
    await waitFor("!document.getElementById('reloadOrder').disabled && !document.getElementById('sectionPreview').hidden");
    assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).includes("Inactive"));
    await viewer.webContents.executeJavaScript(`
        const project = require(${JSON.stringify(path.join(root,"js","project"))});
        project.isActive=()=>true; project.getBundleRoot=()=>${JSON.stringify(temporary)};
        project.resolveRolePath=()=>${JSON.stringify(temporary)};
        window.confirm=()=>true; document.getElementById('reloadOrder').click();
    `);
    await waitFor("!document.getElementById('flipSection').disabled");
    await viewer.webContents.executeJavaScript("document.getElementById('flipSection').click()");
    await waitFor("document.getElementById('orderStatus').textContent.startsWith('Section mirrored.')");
    assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).includes("Mirrored"));
    assert.ok(calls.includes("order_flip_plan") && calls.includes("order_flip"));
	assert.ok(calls.every(action => action.startsWith("order_")), "order viewer must never start detection");
	console.log("Standalone section order: DAPI preview, AP sort, drag/Up/Down, save, conflicts, reopen and no inference passed.");
	viewer.destroy(); opener.destroy(); app.exit(0);
}).catch(error => { console.error(error); app.exit(1); });
