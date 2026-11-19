"use strict";
const { app, BrowserWindow, ipcMain, nativeImage } = require("electron");
const fs = require("fs"), path = require("path"), assert = require("assert");
const { registerDetectionViewer } = require("../js/detect_live_host");
const root = path.resolve(__dirname, "..");
const fixtureRoot = fs.mkdtempSync(path.join(root, "../test-temp/section-order-ui-"));
const temporary = path.join(fixtureRoot,"masonjar_projects","M457_masonjar","data","counting","00_dapi");
fs.mkdirSync(temporary,{recursive:true});
app.setPath("userData", path.join(temporary, "profile"));
app.setPath("crashDumps", path.join(temporary, "crash"));
let viewer, saved = [], states = {}, customSaved = [], savedMode = "filename";
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
			if (request.action === "order_info") result = { sections: states, saved_order: saved, custom_order: customSaved, mode: savedMode, ap_positions: { s1: 120, s2: 100 }, warnings: [], path: path.join(temporary, "slice_order.json") };
			if (request.action === "order_flip_plan") result = {files:[{},{}],bytes:1024};
            if (request.action === "order_flip") { states[request.slice_id] = Object.assign({}, states[request.slice_id], {flip_x:!states[request.slice_id]?.flip_x}); result = {sections:states,backup:"fixture backup"}; }
            if (request.action === "order_preview") result = { image: nativeImage.createFromBitmap(Buffer.alloc(64 * 48 * 4, 120), { width: 64, height: 48 }).toDataURL() };
			if (request.action === "order_save") {
				if (JSON.stringify(request.expected) !== JSON.stringify(saved)) error = "Slice order changed in another window.";
				else { saved = request.slice_ids; customSaved = request.custom_order; savedMode = request.mode; states = Object.fromEntries(Object.entries(request.active || {}).map(([id, active]) => [id, {active}])); result = { saved_order: saved, custom_order: customSaved, mode:savedMode, sections: states }; }
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
    await waitFor("!!document.getElementById('orderInput')");
    await viewer.webContents.executeJavaScript(`localStorage.setItem('masonjar.workspace',${JSON.stringify(JSON.stringify({paths:{dapi:temporary}}))})`);
    async function reopenManager() {
        viewer.destroy();
        await opener.webContents.executeJavaScript("require('electron').ipcRenderer.invoke('openSectionOrderViewer')");
        await waitFor("!document.getElementById('sectionPreview').hidden && document.body.getAttribute('aria-busy')==='false'");
    }
    await reopenManager();
	assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).startsWith("1. s1.png"));
    assert.strictEqual(await viewer.webContents.executeJavaScript("Array.from(document.querySelectorAll('#orderList .section-active')).every(box=>box.checked)"),true,'new sections default to Active');
    const previewsBeforeToggle=calls.filter(action=>action==='order_preview').length;
    const selectionBeforeToggle=await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true]').dataset.file");
    await viewer.webContents.executeJavaScript("document.getElementById('orderList').children[1].querySelector('.section-active').click()");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true]').dataset.file"),selectionBeforeToggle);
    assert.strictEqual(calls.filter(action=>action==='order_preview').length,previewsBeforeToggle,'checkbox must not change preview selection');
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.activeElement.className"),'section-active');
    await viewer.webContents.executeJavaScript("document.getElementById('orderList').children[1].querySelector('.section-active').click()");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('saveSectionOrder').disabled"),true,'rechecking restores baseline Active state');
    await viewer.webContents.executeJavaScript("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}))");
    await waitFor("document.getElementById('count').textContent.includes('s2.png') && document.body.getAttribute('aria-busy')==='false'");
    await viewer.webContents.executeJavaScript("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Delete',bubbles:true}))");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true] .section-active').checked"),false);
    await viewer.webContents.executeJavaScript("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Delete',repeat:true,bubbles:true}))");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true] .section-active').checked"),false,'held Delete must not toggle repeatedly');
    await viewer.webContents.executeJavaScript("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Backspace',bubbles:true}));document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))");
    await waitFor("document.getElementById('count').textContent.includes('s1.png') && document.body.getAttribute('aria-busy')==='false'");
    const activeBeforeActivation=await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true] .section-active').checked");
    await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true] .section-active').dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true}))");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true] .section-active').checked"),!activeBeforeActivation);
    await viewer.webContents.executeJavaScript("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',repeat:true,bubbles:true,cancelable:true}))");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true] .section-active').checked"),!activeBeforeActivation,'held Enter does not repeatedly toggle');
    await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true] .section-active').checked"),activeBeforeActivation);
    assert.ok(await viewer.webContents.executeJavaScript("document.querySelector('.section-active').title.includes('Space / Enter')"));
    const keyboardOrderBefore=await viewer.webContents.executeJavaScript("Array.from(document.querySelectorAll('#orderList li')).map(row=>row.dataset.file)");
    const previewCountBeforeReorder=calls.filter(action=>action==='order_preview').length;
    await viewer.webContents.executeJavaScript("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',ctrlKey:true,bubbles:true}))");
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true]').dataset.file"),'s1.png','Ctrl arrows reorder without changing selection');
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList [aria-current=true]').previousElementSibling.dataset.file"),'s2.png');
    assert.strictEqual(calls.filter(action=>action==='order_preview').length,previewCountBeforeReorder,'reorder does not reload preview');
    await viewer.webContents.executeJavaScript("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',ctrlKey:true,bubbles:true}))");
    assert.deepStrictEqual(await viewer.webContents.executeJavaScript("Array.from(document.querySelectorAll('#orderList li')).map(row=>row.dataset.file)"),keyboardOrderBefore);
    assert.ok(await viewer.webContents.executeJavaScript("document.getElementById('orderUp').title.includes('Ctrl + ↑') && document.getElementById('orderDown').title.includes('Ctrl + ↓')"));
    await reopenManager();
    const footerLayout=await viewer.webContents.executeJavaScript(`(() => {
        const rect=id=>document.getElementById(id).getBoundingClientRect();
        return {aligned:Math.abs(rect('saveSectionOrder').bottom-rect('orderStatus').top)<1,equal:Math.abs(rect('orderPrevious').width-rect('orderNext').width)<1,tooltip:document.querySelector('.section-active').title};
    })()`);
    assert.ok(footerLayout.aligned && footerLayout.equal,'Save aligns to status bar top and navigation widths match');
    assert.ok(footerLayout.tooltip.includes('Backspace / Delete'),'checkbox tooltip documents shortcut');
    assert.ok(await viewer.webContents.executeJavaScript(`(async () => {
        const status=document.getElementById('orderStatus'),text=status.textContent;
        status.textContent='Long status message for wrapping and footer alignment. '.repeat(30);
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const aligned=Math.abs(document.getElementById('saveSectionOrder').getBoundingClientRect().bottom-status.getBoundingClientRect().top)<1;
        status.textContent=text;
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        return aligned;
    })()`),'Save must stay aligned when status text wraps');

    const pathLayout=await viewer.webContents.executeJavaScript(`(() => {
        const field=document.getElementById('orderInput');
        return {noBrowse:!document.getElementById('orderBrowse'),plain:field.tagName==='P',inHeader:!!field.closest('header'),noRefresh:!document.getElementById('reloadOrder'),fullHeight:field.scrollHeight<=field.clientHeight+1,noHorizontalScroll:field.scrollWidth<=field.clientWidth+1,value:field.textContent,title:field.title,font:getComputedStyle(field).fontSize,saveFont:getComputedStyle(document.getElementById('orderSource')).fontSize};
    })()`);
    assert.ok(pathLayout.noBrowse && pathLayout.plain && pathLayout.inHeader,'path must be plain text in the header; Browse removed');
    assert.ok(pathLayout.noRefresh,'Refresh must be absent; startup automatically loads sections');
    assert.ok(pathLayout.fullHeight && pathLayout.noHorizontalScroll,'the whole wrapped path must be visible');
    assert.strictEqual(pathLayout.value,'DAPI preview folder: '+temporary);
    assert.strictEqual(pathLayout.title,temporary);assert.strictEqual(pathLayout.font,pathLayout.saveFont,'path format must match Save location');
    const addressRows=await viewer.webContents.executeJavaScript(`(() => {
        const input=document.getElementById('orderInput'),save=document.getElementById('orderSource');
        return {adjacent:input.nextElementSibling===save,header:!!save.closest('header'),below:save.getBoundingClientRect().top>=input.getBoundingClientRect().bottom,text:save.textContent};
    })()`);
    assert.ok(addressRows.adjacent && addressRows.header && addressRows.below,'Save location must be immediately below DAPI folder in the header');
    assert.ok(addressRows.text.startsWith('Save location: '));
    const compactLayout=await viewer.webContents.executeJavaScript(`(() => {
        const box=id=>document.getElementById(id).getBoundingClientRect();
        const status=document.getElementById('orderStatus'),list=box('orderList'),flip=box('flipSection'),save=box('saveSectionOrder');
        return {statusInPreview:!!status.closest('.section-preview-pane'),below:box('orderStatus').top>=box('viewport').bottom,compact:['orderMode','orderUp','orderDown','flipSection'].every(id=>box(id).height<=28),topGap:list.top-box('orderUp').bottom,bottomGap:flip.top-list.bottom,saveGap:save.top-flip.bottom,label:document.getElementById('flipSection').textContent};
    })()`);
    assert.ok(compactLayout.statusInPreview && compactLayout.below,'status must sit below the image viewport');
    assert.ok(compactLayout.compact,'sidebar controls must fit their font height');
    assert.ok(Math.abs(compactLayout.topGap-15)<1 && Math.abs(compactLayout.bottomGap-15)<1,'list gaps must match Flip/Save spacing');
    assert.strictEqual(compactLayout.label,'Flip Left/Right…');
    const sortRow=await viewer.webContents.executeJavaScript(`(() => {
        const label=document.querySelector('label[for="orderMode"]'),box=document.getElementById('orderMode');
        const l=label.getBoundingClientRect(),b=box.getBoundingClientRect();
        return {text:label.textContent,left:l.right<=b.left,aligned:Math.abs((l.top+l.bottom)/2-(b.top+b.bottom)/2)<1};
    })()`);
    assert.ok(sortRow.text==='Sort sections:' && sortRow.left && sortRow.aligned,'sort label and box must share one row');



    const headerLayout = await viewer.webContents.executeJavaScript(`(() => {
        const box = id => document.getElementById(id).getBoundingClientRect().toJSON();
        return {count:box('count'),previous:box('orderPrevious'),next:box('orderNext'),width:window.innerWidth};
    })()`);
    const columns = await viewer.webContents.executeJavaScript(`(() => {
        const rect = selector => document.querySelector(selector).getBoundingClientRect();
        const left=rect('.section-header-main'),nav=rect('.section-navigation'),heading=rect('.section-heading'),addresses=rect('.section-addresses');
        return {separate:left.right<nav.left,below:addresses.top>=heading.bottom,centered:Math.abs((left.top+left.bottom)/2-(nav.top+nav.bottom)/2)<1,grouped:document.getElementById('orderInput').parentElement===document.getElementById('orderSource').parentElement};
    })()`);
    assert.ok(columns.separate && columns.below && columns.centered && columns.grouped,'header needs two independent columns with grouped address lines below the heading');
    assert.ok(headerLayout.count.right<=headerLayout.previous.left,'section info must precede Previous');
    assert.ok(headerLayout.previous.right<=headerLayout.next.left,'Previous must precede Next');
    assert.ok(Math.abs(headerLayout.width-headerLayout.next.right-20)<=1,'navigation must retain the right header margin');

	await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='ap_ascending'; document.getElementById('orderMode').dispatchEvent(new Event('change')); document.getElementById('saveSectionOrder').click()");
	await waitFor("!document.getElementById('saveSectionOrder').disabled === false");
	assert.deepStrictEqual(saved, ["s2", "s1", "s10"]);
    assert.deepStrictEqual(customSaved,["s1","s2","s10"],"saving AP must not overwrite Custom");
    await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='custom';document.getElementById('orderMode').dispatchEvent(new Event('change'))");
    assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).startsWith("1. s1.png"));
    await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='ap_ascending';document.getElementById('orderMode').dispatchEvent(new Event('change'))");

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
	saved = ["s1", "s2", "s10"]; customSaved=saved.slice();savedMode="custom";
	await viewer.webContents.executeJavaScript("document.getElementById('orderDown').click(); document.getElementById('saveSectionOrder').click()");
	await waitFor("document.getElementById('orderStatus').classList.contains('error')");
	assert.deepStrictEqual(saved, ["s1", "s2", "s10"], "conflict must preserve the external order");
	await reopenManager();
	await waitFor("document.body.getAttribute('aria-busy')==='false' && document.getElementById('saveSectionOrder').disabled");
	await viewer.webContents.executeJavaScript(`localStorage.setItem('masonjar.workspace',${JSON.stringify(JSON.stringify({ paths: { dapi: temporary } }))})`);
	viewer.destroy();
	await opener.webContents.executeJavaScript("require('electron').ipcRenderer.invoke('openSectionOrderViewer')");
	await waitFor("!!document.getElementById('sectionPreview') && !document.getElementById('sectionPreview').hidden");
	assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).startsWith("1. s1.png"));
    await viewer.webContents.executeJavaScript("document.querySelector('#orderList .section-active').click(); document.getElementById('saveSectionOrder').click()");
    await waitFor("document.getElementById('saveSectionOrder').disabled && document.body.getAttribute('aria-busy')==='false'");
    assert.ok(Object.values(states).some(state => state.active === false));
    await reopenManager();
    await waitFor("document.body.getAttribute('aria-busy')==='false' && !document.getElementById('sectionPreview').hidden");
    assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).includes("Inactive"));
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.querySelector('#orderList .section-active').checked"),false,'saved Inactive must remain unchecked after reopening');

    await viewer.webContents.executeJavaScript(`
        const project = require(${JSON.stringify(path.join(root,"js","project"))});
        project.isActive=()=>true; project.getBundleRoot=()=>${JSON.stringify(temporary)};
        project.resolveRolePath=()=>${JSON.stringify(temporary)};
        window.confirm=()=>true; document.querySelector('#orderList .section-active').click();document.querySelector('#orderList .section-active').click();
    `);
    await waitFor("!document.getElementById('flipSection').disabled");
    await viewer.webContents.executeJavaScript("document.getElementById('flipSection').click()");
    await waitFor("document.getElementById('orderStatus').textContent.startsWith('Section mirrored.')");
    assert.ok((await viewer.webContents.executeJavaScript("document.getElementById('orderList').textContent")).includes("Mirrored"));
    assert.ok(calls.includes("order_flip_plan") && calls.includes("order_flip"));
    // A long list must follow selection without moving the outer sidebar.
    for (let i=1;i<=60;i++) fs.writeFileSync(path.join(temporary,"many_"+String(i).padStart(3,"0")+".png"),"fixture");
    await reopenManager();
    await waitFor("document.getElementById('orderList').children.length===63 && document.body.getAttribute('aria-busy')==='false'");
    const visible = async () => viewer.webContents.executeJavaScript(`(() => {
        const list=document.getElementById('orderList'),row=list.querySelector('[aria-current="true"]');
        const box=list.getBoundingClientRect(),item=row.getBoundingClientRect(),top=box.top+list.clientTop;
        return {visible:item.top>=top-1 && item.bottom<=top+list.clientHeight+1,scroll:list.scrollTop,outer:document.querySelector('aside').scrollTop};
    })()`);
    const initialOuter = (await visible()).outer;
    const offscreenToggle=await viewer.webContents.executeJavaScript(`(() => {
      const list=document.getElementById('orderList');list.scrollTop=list.scrollHeight;
      const before=list.scrollTop,chosen=list.querySelector('[aria-current=true]').dataset.file;
      list.lastElementChild.querySelector('.section-active').click();
      return {before,after:list.scrollTop,chosen,afterChosen:list.querySelector('[aria-current=true]').dataset.file,checked:list.lastElementChild.querySelector('.section-active').checked};
    })()`);
    assert.strictEqual(offscreenToggle.before,offscreenToggle.after,'toggling another row must not jump back to current preview');
    assert.strictEqual(offscreenToggle.chosen,offscreenToggle.afterChosen);assert.strictEqual(offscreenToggle.checked,false);
    await viewer.webContents.executeJavaScript("document.getElementById('orderList').lastElementChild.querySelector('.section-active').click()");

    await viewer.webContents.executeJavaScript("document.getElementById('orderList').lastElementChild.click()");
    await waitFor("document.getElementById('orderNext').disabled && document.body.getAttribute('aria-busy')==='false'");
    let position = await visible(); assert.ok(position.visible && position.scroll>0); assert.strictEqual(position.outer,initialOuter);
    const lastScroll=position.scroll;
    await viewer.webContents.executeJavaScript("document.getElementById('orderPrevious').click()");
    await waitFor("!document.getElementById('orderNext').disabled && document.body.getAttribute('aria-busy')==='false'");
    position=await visible();assert.ok(position.visible);assert.strictEqual(position.scroll,lastScroll,'already visible selection must not jump');assert.strictEqual(position.outer,initialOuter);
    await viewer.webContents.executeJavaScript("document.getElementById('orderList').firstElementChild.click()");
    await waitFor("document.getElementById('orderPrevious').disabled && document.body.getAttribute('aria-busy')==='false'");
    assert.ok((await visible()).visible);
    for(let i=0;i<18;i++) {
        await viewer.webContents.executeJavaScript("document.getElementById('orderNext').click()");
        await waitFor("document.body.getAttribute('aria-busy')==='false' && !document.getElementById('sectionPreview').hidden");
        position=await visible();assert.ok(position.visible,'Next must keep the selected row visible');assert.strictEqual(position.outer,initialOuter);
    }
    for(let i=0;i<18;i++) {
        await viewer.webContents.executeJavaScript("document.getElementById('orderPrevious').click()");
        await waitFor("document.body.getAttribute('aria-busy')==='false' && !document.getElementById('sectionPreview').hidden");
        position=await visible();assert.ok(position.visible,'Previous must keep the selected row visible');assert.strictEqual(position.outer,initialOuter);
    }
    await viewer.webContents.executeJavaScript("for(let i=0;i<28;i++) document.getElementById('orderDown').click()");
    assert.ok((await visible()).visible,'Down across the viewport boundary must retain visibility');
    await viewer.webContents.executeJavaScript("for(let i=0;i<28;i++) document.getElementById('orderUp').click()");
    assert.ok((await visible()).visible,'Up across the viewport boundary must retain visibility');
    await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='filename';document.getElementById('orderMode').dispatchEvent(new Event('change'))");
    position=await visible();assert.ok(position.visible && position.scroll>0,'sorting must reveal the retained section at its new position');assert.strictEqual(position.outer,initialOuter);
    await viewer.webContents.executeJavaScript("document.getElementById('orderUp').click()");
    const manualDraft=await viewer.webContents.executeJavaScript("Array.from(document.getElementById('orderList').children).map(row=>row.dataset.file)");
    await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='filename';document.getElementById('orderMode').dispatchEvent(new Event('change'));document.getElementById('orderMode').value='custom';document.getElementById('orderMode').dispatchEvent(new Event('change'))");
    assert.deepStrictEqual(await viewer.webContents.executeJavaScript("Array.from(document.getElementById('orderList').children).map(row=>row.dataset.file)"),manualDraft,"unsaved Custom edits must survive switching sort modes");
    await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='filename';document.getElementById('orderMode').dispatchEvent(new Event('change'));document.getElementById('saveSectionOrder').click()");
    await waitFor("document.body.getAttribute('aria-busy')==='false' && document.getElementById('saveSectionOrder').disabled");
    await reopenManager();
    assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value"),'filename');
    await viewer.webContents.executeJavaScript("document.getElementById('orderMode').value='custom';document.getElementById('orderMode').dispatchEvent(new Event('change'))");
    assert.deepStrictEqual(await viewer.webContents.executeJavaScript("Array.from(document.getElementById('orderList').children).map(row=>row.dataset.file)"),manualDraft,"Custom must survive saving Filename and reopening");
	assert.ok(calls.every(action => action.startsWith("order_")), "order viewer must never start detection");
	console.log("Standalone section order: DAPI preview, AP sort, drag/Up/Down, save, conflicts, reopen and no inference passed.");
	viewer.destroy(); opener.destroy(); app.exit(0);
}).catch(error => { console.error(error); app.exit(1); });
