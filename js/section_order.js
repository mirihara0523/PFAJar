"use strict";
const { ipcRenderer: ipc } = require("electron");
const fs = require("fs"), path = require("path");
const project = require("./project"), workspace = require("./workspace");
const sectionOrder = require("./detect_live_order");
const el = id => document.getElementById(id);
let files = [], baseline = [], selected = null, metadata = { saved_order: [], ap_positions: {}, warnings: [] };
let input = "", busy = false, dirty = false, active = {};

function status(message, error) {
	el("orderStatus").textContent = message;
	el("orderStatus").classList.toggle("error", !!error);
}
function context() {
	const root = project.isActive() ? project.getBundleRoot() : "";
	const relative = root ? path.relative(root, input) : "..";
	const inProject = root && !relative.startsWith("..") && !path.isAbsolute(relative);
	return { input_dir: input, bundle_root: inProject ? root : "", dapi_dir: inProject ? project.resolveRolePath("dapi") : input };
}
function controls() {
	["orderInput", "orderBrowse", "reloadOrder", "orderMode"].forEach(id => { el(id).disabled = busy; });
	el("orderMode").disabled = busy || !files.length;
	const index = files.indexOf(selected);
	el("sectionActive").disabled = busy || !selected;
	el("flipSection").disabled = busy || dirty || !selected || !context().bundle_root;
	el("orderPrevious").disabled = busy || index <= 0;
	el("orderNext").disabled = busy || index < 0 || index >= files.length - 1;
	el("orderUp").disabled = busy || index <= 0;
	el("orderDown").disabled = busy || index < 0 || index >= files.length - 1;
	el("saveSectionOrder").disabled = busy || !dirty || !files.length;
	Array.from(el("orderMode").options).forEach(option => {
		if (option.value.startsWith("ap_")) option.disabled = !files.some(name => Number.isFinite(metadata.ap_positions[sectionOrder.sliceId(name)]));
	});
}
async function request(action, data) {
	if (busy) throw new Error("Wait for the current operation to finish.");
	busy = true; controls();
	try { return await ipc.invoke("sectionOrderRequest", Object.assign({ action: action }, data)); }
	finally { busy = false; controls(); }
}
function render() {
	el("orderList").replaceChildren();
	files.forEach((name, index) => {
		const item = document.createElement("li"), ap = metadata.ap_positions[sectionOrder.sliceId(name)];
		item.textContent = (index + 1) + ". " + name + " · AP " + (Number.isFinite(ap) ? ap : "—");
		if (active[sectionOrder.sliceId(name)] === false) { item.textContent += " · Inactive"; item.style.opacity = "0.55"; }
		if (metadata.sections && metadata.sections[sectionOrder.sliceId(name)] && metadata.sections[sectionOrder.sliceId(name)].flip_x) item.textContent += " · Mirrored";
		item.dataset.file = name; item.draggable = true; item.tabIndex = 0;
		item.setAttribute("role", "option"); item.setAttribute("aria-selected", String(name === selected));
		item.addEventListener("click", () => select(name));
		item.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); void select(name); } });
		item.addEventListener("dragstart", event => { if (busy) { event.preventDefault(); return; } event.dataTransfer.setData("text/plain", name); });
		item.addEventListener("dragover", event => { if (!busy) event.preventDefault(); });
		item.addEventListener("drop", event => {
			event.preventDefault(); if (busy) return;
			const from = files.indexOf(event.dataTransfer.getData("text/plain")), to = files.indexOf(name);
			if (from < 0 || from === to) return;
			files.splice(to, 0, files.splice(from, 1)[0]); changed("custom");
		});
		el("orderList").appendChild(item);
	});
	const index = files.indexOf(selected), ap = metadata.ap_positions[sectionOrder.sliceId(selected || "")];
	el("count").textContent = selected ? (index + 1) + " / " + files.length + " · " + selected + (Number.isFinite(ap) ? " · AP " + ap : "") : "No sections loaded";
	el("sectionActive").checked = active[sectionOrder.sliceId(selected || "")] !== false;
	controls();
}
function changed(mode) {
	el("orderMode").value = mode;
	dirty = files.some((name, index) => baseline[index] !== name || active[sectionOrder.sliceId(name)] !== !(metadata.sections && metadata.sections[sectionOrder.sliceId(name)] && metadata.sections[sectionOrder.sliceId(name)].active === false));
	render();
	status(dirty ? "Section changes pending. Save order / active states to apply." : "Saved order restored.");
}
async function select(name) {
	if (busy || !files.includes(name)) return;
	selected = name; render();
	el("sectionPreview").hidden = true;
	status("Loading preview for " + name + "…");
	try {
		const result = await request("order_preview", { source: path.join(input, name) });
		const image = new Image();
		await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error("Could not display section preview.")); image.src = result.image; });
		if (selected !== name) return;
		el("sectionPreview").src = result.image; el("sectionPreview").hidden = false;
		status(dirty ? "Section changes pending. Save order / active states to apply." : "Preview ready. Select, drag or sort sections.");
	} catch (error) { status("Preview unavailable: " + error.message + ". You can still edit the order.", true); }
}
async function load() {
	if (busy || (dirty && !window.confirm("Discard the unsaved section order and reload?"))) return;
	const previousInput = input;
	try {
		input = el("orderInput").value.trim();
		const names = fs.readdirSync(input, { withFileTypes: true }).filter(entry => entry.isFile() && /\.(tiff?|png|jpe?g)$/i.test(entry.name)).map(entry => entry.name);
		if (!names.length) throw new Error("No section images found in this folder.");
		if (new Set(names.map(sectionOrder.sliceId)).size !== names.length) throw new Error("Multiple images share a slice ID. Choose a folder with one preview per section.");
		try { metadata = await request("order_info", context()); }
        catch (error) {
            if (!String(error.message).includes("interrupted section flip") || !window.confirm("An interrupted section flip was found. Restore all original files from its backup now? Close other analysis windows first.")) throw error;
            metadata = await request("order_info", Object.assign(context(), {recover_flip: true}));
        }
		files = sectionOrder.sortSections(names, metadata.saved_order.length ? "custom" : "filename", metadata.ap_positions, metadata.saved_order);
		baseline = files.slice(); dirty = false;
		active = Object.fromEntries(files.map(name => [sectionOrder.sliceId(name), !(metadata.sections && metadata.sections[sectionOrder.sliceId(name)] && metadata.sections[sectionOrder.sliceId(name)].active === false)]));
		el("orderMode").value = metadata.saved_order.length ? "custom" : "filename";
		el("orderSource").textContent = "Save location: " + metadata.path + (metadata.warnings.length ? " · " + metadata.warnings.join(" ") : "");
		if (!files.includes(selected)) selected = files[0];
		render(); await select(selected);
	} catch (error) { input = previousInput; el("orderInput").value = previousInput; status(error.message, true); }
}
function move(delta) {
	if (busy) return;
	const from = files.indexOf(selected), to = from + delta;
	if (from < 0 || to < 0 || to >= files.length) return;
	files.splice(to, 0, files.splice(from, 1)[0]); changed("custom");
}
el("orderMode").addEventListener("change", () => {
	files = sectionOrder.sortSections(files, el("orderMode").value, metadata.ap_positions, metadata.saved_order);
	changed(el("orderMode").value);
});
el("orderUp").addEventListener("click", () => move(-1));
el("orderDown").addEventListener("click", () => move(1));
el("orderPrevious").addEventListener("click", () => select(files[files.indexOf(selected) - 1]));
el("orderNext").addEventListener("click", () => select(files[files.indexOf(selected) + 1]));
el("reloadOrder").addEventListener("click", load);
el("orderBrowse").addEventListener("click", async () => {
	try { const folder = await ipc.invoke("sectionOrderBrowse", "input"); if (folder) { el("orderInput").value = folder; await load(); } }
	catch (error) { status(error.message, true); }
});
el("saveSectionOrder").addEventListener("click", async () => {
	try {
		const result = await request("order_save", Object.assign(context(), { slice_ids: files.map(sectionOrder.sliceId), expected: metadata.saved_order, active: active, expected_sections: metadata.sections || {} }));
		metadata.saved_order = result.saved_order; metadata.sections = result.sections || metadata.sections || {}; baseline = files.slice(); dirty = false; render();
		status("Section order and active states saved. Reopen or reload Cell Detection Viewer to use this order.");
	} catch (error) { status(error.message, true); }
});
window.addEventListener("beforeunload", event => { if (dirty || busy) { event.preventDefault(); event.returnValue = false; } });
try {
	project.tryRestoreActiveProject(); workspace.loadWorkspace();
	el("orderInput").value = project.isActive() ? project.resolveRolePath("dapi") : (workspace.getWorkspace().paths.dapi || "");
} catch (error) { status(error.message, true); }
controls();
if (el("orderInput").value) void load();

el("sectionActive").addEventListener("change", () => {
  active[sectionOrder.sliceId(selected)] = el("sectionActive").checked;
  changed(el("orderMode").value);
});
el("flipSection").addEventListener("click", async () => {
  if (busy || dirty || !selected) return;
  try {
    const data = Object.assign(context(), {slice_id: sectionOrder.sliceId(selected)});
    const plan = await request("order_flip_plan", data);
    if (!window.confirm("Flip " + selected + " left/right across DAPI, analysis channels, annotations and existing BBoxes?\n" + plan.files.length + " files, " + Math.round(plan.bytes / 1048576) + " MB. Originals will be backed up. Close other analysis windows first.")) return;
    status("Backing up and flipping section files…");
    const result = await request("order_flip", Object.assign(data, {plan: plan}));
    metadata.sections = result.sections;
    render(); await select(selected);
    status("Section mirrored. Backup: " + result.backup + ". Reopen analysis viewers before continuing.");
  } catch (error) { status(error.message, true); }
});

ipc.on("sectionOrderStatus", (_event, progress) => {
  if (!busy || !String(progress.stage).startsWith("flip_")) return;
  const stage = progress.stage === "flip_staging" ? "Backing up and preparing" : "Applying section reflection";
  status(stage + "… " + (Number.isFinite(progress.current) && Number.isFinite(progress.total) ? progress.current + " / " + progress.total + " files" : ""));
});
