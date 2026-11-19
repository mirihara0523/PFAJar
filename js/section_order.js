"use strict";
const { ipcRenderer: ipc } = require("electron");
const fs = require("fs"), path = require("path");
const project = require("./project"), workspace = require("./workspace");
const sectionOrder = require("./detect_live_order");
const el = id => document.getElementById(id);
let files = [], baseline = [], selected = null, metadata = { saved_order: [], ap_positions: {}, warnings: [] };
let input = "", busy = false, dirty = false, active = {};
let customOrder = [], baselineCustom = [], baselineMode = "filename";

// Reserve the status bar height under Save, including wrapped messages and resizing.
const statusSizeObserver = new ResizeObserver(() => {
    document.body.style.setProperty("--section-status-height", el("orderStatus").getBoundingClientRect().height + "px");
});
statusSizeObserver.observe(el("orderStatus"));

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
    document.body.setAttribute("aria-busy", String(busy));
	["orderMode"].forEach(id => { el(id).disabled = busy; });
	el("orderMode").disabled = busy || !files.length;
	const index = files.indexOf(selected);
	document.querySelectorAll("#orderList .section-active").forEach(box => { box.disabled = busy; });
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
function keepSelectionVisible() {
    const list = el("orderList"), row = list.querySelector('[aria-current="true"]');
    if (!row || !list.clientHeight) return;
    const bounds = list.getBoundingClientRect(), item = row.getBoundingClientRect();
    const top = bounds.top + list.clientTop, bottom = top + list.clientHeight;
    // Scroll only this list, not the surrounding Options panel or document.
    if (item.top < top) list.scrollTop += item.top - top;
    else if (item.bottom > bottom) list.scrollTop += Math.min(item.bottom - bottom, item.top - top);
}
function render(revealSelection = true) {
    const list = el("orderList"), previousScrollTop = list.scrollTop;
    const rows = document.createDocumentFragment();
	files.forEach((name, index) => {
		const item = document.createElement("li"), ap = metadata.ap_positions[sectionOrder.sliceId(name)];
        const sid = sectionOrder.sliceId(name), checkbox = document.createElement("input"), description = document.createElement("span");
        checkbox.type = "checkbox"; checkbox.className = "section-active";
        checkbox.checked = active[sid] !== false; checkbox.disabled = busy;
        checkbox.setAttribute("aria-label", "Include " + name + " in subsequent analyses");
        checkbox.setAttribute("aria-keyshortcuts", "Backspace Delete Space Enter");
        checkbox.title = "Checked: Active · Unchecked: Inactive (applies after Save). Backspace / Delete / Space / Enter: toggle the current section between Active and Inactive. ↑ / ↓: Previous / Next.";
        checkbox.addEventListener("click", event => event.stopPropagation());
        checkbox.addEventListener("change", () => {
            if (busy) return;
            active[sid] = checkbox.checked;
            changed(el("orderMode").value, false);
            // Redrawing must preserve keyboard focus and the user's list position.
            el("orderList").children[files.indexOf(name)].querySelector(".section-active").focus({preventScroll:true});
        });
        description.className = "section-description";
        description.textContent = (index + 1) + ". " + name + " · AP " + (Number.isFinite(ap) ? ap : "—");
        if (active[sid] === false) { description.textContent += " · Inactive"; description.style.opacity = "0.55"; }
        if (metadata.sections && metadata.sections[sid] && metadata.sections[sid].flip_x) description.textContent += " · Mirrored";
        item.append(checkbox, description);
		item.dataset.file = name; item.draggable = true; item.tabIndex = 0;
		item.setAttribute("role", "listitem"); item.setAttribute("aria-current", String(name === selected));
        item.title = "Click to select. Backspace / Delete / Space / Enter: toggle the current section between Active and Inactive.";
		item.addEventListener("click", () => select(name));

		item.addEventListener("dragstart", event => { if (busy) { event.preventDefault(); return; } event.dataTransfer.setData("text/plain", name); });
		item.addEventListener("dragover", event => { if (!busy) event.preventDefault(); });
		item.addEventListener("drop", event => {
			event.preventDefault(); if (busy) return;
			const from = files.indexOf(event.dataTransfer.getData("text/plain")), to = files.indexOf(name);
			if (from < 0 || from === to) return;
			files.splice(to, 0, files.splice(from, 1)[0]); customOrder = files.map(sectionOrder.sliceId); changed("custom");
		});
		rows.appendChild(item);
	});
    list.replaceChildren(rows);
    list.scrollTop = previousScrollTop;
	const index = files.indexOf(selected), ap = metadata.ap_positions[sectionOrder.sliceId(selected || "")];
	el("count").textContent = selected ? (index + 1) + " / " + files.length + " · " + selected + (Number.isFinite(ap) ? " · AP " + ap : "") : "No sections loaded";
	controls();
    if (revealSelection) keepSelectionVisible();
}
function changed(mode, revealSelection = true) {
	el("orderMode").value = mode;
	dirty = files.some((name, index) => baseline[index] !== name || active[sectionOrder.sliceId(name)] !== !(metadata.sections && metadata.sections[sectionOrder.sliceId(name)] && metadata.sections[sectionOrder.sliceId(name)].active === false));
    dirty = dirty || mode !== baselineMode || JSON.stringify(customOrder) !== JSON.stringify(baselineCustom);
	render(revealSelection);
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
async function loadInitialSections() {
	if (busy) return;
	try {
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
        customOrder = sectionOrder.sortSections(names, "custom", {}, metadata.custom_order || metadata.saved_order).map(sectionOrder.sliceId);
        baselineCustom = customOrder.slice(); baselineMode = metadata.mode || (metadata.saved_order.length ? "custom" : "filename");
		active = Object.fromEntries(files.map(name => [sectionOrder.sliceId(name), !(metadata.sections && metadata.sections[sectionOrder.sliceId(name)] && metadata.sections[sectionOrder.sliceId(name)].active === false)]));
		el("orderMode").value = baselineMode;
		el("orderSource").textContent = "Save location: " + metadata.path + (metadata.warnings.length ? " · " + metadata.warnings.join(" ") : "");
		if (!files.includes(selected)) selected = files[0];
		render(); await select(selected);
	} catch (error) { status(error.message, true); }
}
function move(delta) {
	if (busy) return;
	const from = files.indexOf(selected), to = from + delta;
	if (from < 0 || to < 0 || to >= files.length) return;
	files.splice(to, 0, files.splice(from, 1)[0]); customOrder = files.map(sectionOrder.sliceId); changed("custom");
}
el("orderMode").addEventListener("change", () => {
	files = sectionOrder.sortSections(files, el("orderMode").value, metadata.ap_positions, customOrder);
	changed(el("orderMode").value);
});
el("orderUp").addEventListener("click", () => move(-1));
el("orderDown").addEventListener("click", () => move(1));
el("orderPrevious").addEventListener("click", () => select(files[files.indexOf(selected) - 1]));
el("orderNext").addEventListener("click", () => select(files[files.indexOf(selected) + 1]));
document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.altKey || event.metaKey || event.shiftKey || event.isComposing) return;
    const target = event.target;
    if (target.closest && target.closest('select, textarea, [contenteditable="true"], input:not([type="checkbox"]), dialog[open]')) return;
    if (event.ctrlKey) {
        const reorder = event.key === "ArrowUp" ? "orderUp" : event.key === "ArrowDown" ? "orderDown" : null;
        if (!reorder) return;
        event.preventDefault();
        if (!busy && selected && !el(reorder).disabled) el(reorder).click();
        return;
    }
    const navigation = event.key === "ArrowUp" ? "orderPrevious" : event.key === "ArrowDown" ? "orderNext" : null;
    const activation = event.key === " " || event.key === "Enter";
    // Keep native activation for buttons and links; list/preview shortcuts toggle selection.
    if (activation && target.closest && target.closest("button, a[href]")) return;
    const toggle = event.key === "Backspace" || event.key === "Delete" || activation;
    if (!navigation && !toggle) return;
    event.preventDefault();
    if (busy || !selected) return;
    if (navigation) { if (!el(navigation).disabled) el(navigation).click(); return; }
    if (event.repeat) return;
    const box = el("orderList").querySelector('[aria-current="true"] .section-active');
    if (box && !box.disabled) box.click();
});
el("saveSectionOrder").addEventListener("click", async () => {
	try {
		const result = await request("order_save", Object.assign(context(), { slice_ids: files.map(sectionOrder.sliceId), expected: metadata.saved_order, active: active, expected_sections: metadata.sections || {}, custom_order: customOrder, mode: el("orderMode").value, expected_custom: metadata.custom_order || metadata.saved_order, expected_mode: metadata.mode || (metadata.saved_order.length ? "custom" : "filename") }));
		metadata.saved_order = result.saved_order; metadata.custom_order = result.custom_order || customOrder.slice(); metadata.mode = result.mode || el("orderMode").value; baselineCustom = customOrder.slice(); baselineMode = metadata.mode; metadata.sections = result.sections || metadata.sections || {}; baseline = files.slice(); dirty = false; render();
		status("Section order and active states saved. Reopen or reload Cell Detection Viewer to use this order.");
    } catch (error) {
        const message = error.message.replace(/Reload the folder/gi, "Close and reopen Section Manager").replace(/\bReload\b/gi, "Close and reopen Section Manager").replace(/Reopen the dialog/gi, "Close and reopen Section Manager");
        status(message, true);
    }
});
window.addEventListener("beforeunload", event => { if (dirty || busy) { event.preventDefault(); event.returnValue = false; } });
try {
	project.tryRestoreActiveProject(); workspace.loadWorkspace();
	input = (project.isActive() ? project.resolveRolePath("dapi") : (workspace.getWorkspace().paths.dapi || "")).trim();
} catch (error) { status(error.message, true); }
el("orderInput").textContent = "DAPI preview folder: " + input;
el("orderInput").title = input;
controls();
if (input) void loadInitialSections();
else status("No DAPI folder is configured. Open a project or configure the workspace, then reopen Section Manager.", true);

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
