"use strict";

const { ipcRenderer: ipc } = require("electron");
const fs = require("fs");
const path = require("path");
const os = require("os");
const project = require("./project");
const pipelineRuns = require("./pipeline_runs");
const workspace = require("./workspace");
const branding = require("./branding");
const bboxInfo = require("./detect_live_info");
const sectionOrder = require("./detect_live_order");
const canvas = document.getElementById("imageCanvas");
const viewport = document.getElementById("viewport");
const splitter = document.getElementById("optionsSplitter");
const main = document.querySelector("main");
const context = canvas.getContext("2d");
const filterIds = ["confidence", "area", "eccentricity", "intensity"];
const lockIds = ["previous", "next", "slice", "goTo", "detect", "save", "loadFolder", "input", "output", "browseInput", "browseOutput", "browseModel", "method", "model", "tile", "floor", "liveSignalBranch", "liveDataset"];
let files = [], index = -1, token = null, records = [], indices = [], detected = false;
let busy = false, dirty = false, filterPending = false, filterTimer = null;
let filtersValid = false;
let loadedFolder = "";
let automaticOutput = "";
let activeAction = null;
let applyingAll = false, stopAll = false, bulkPosition = "";
let image = null, imageWidth = 1, imageHeight = 1, zoom = 1, offsetX = 0, offsetY = 0, drag = null;
let originalImage = null;
let optionsWidth = 340, splitterDrag = null;
let hoverFrame = null;
let workProgress = null, workTimer = null;
let ordering = { saved_order: [], ap_positions: {}, warnings: [] };
let resolveUnsaved = null;

function timeText(ms) {
	const seconds = Math.max(0, Math.floor(ms / 1000));
	return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
}
function renderWorkProgress() {
	if (!workProgress) return;
	const now = Date.now(), progress = workProgress;
	const labels = {
		load: "Loading image", reading: "Reading input image", preprocess: "Adjusting detection contrast",
		preview: "Preparing preview", detect: "Starting detection", preparing: "Preparing detector",
		cache_read: "Checking candidate cache", model: "Loading detection model", inference: "Detecting tiles",
		postprocess: "Final image pass / merging detections", measurements: "Measuring candidate features",
		restore_saved: "Loading saved BBoxes and parameters", cache_write: "Saving candidate cache", save: "Saving predictions", saving: "Saving predictions",
		order_info: "Reading section metadata", order_save: "Saving section order",
	};
	let title = labels[progress.stage] || progress.stage;
	const bar = el("workBar");
	if (progress.done) {
		title = progress.failed ? "Processing failed" : "Processing completed";
		bar.max = 1; bar.value = progress.failed ? 0 : 1;
	} else if (Number.isFinite(progress.current) && progress.total > 0) {
		title += " · " + progress.current + " / " + progress.total + " (" + Math.floor(progress.current / progress.total * 100) + "%)";
		bar.max = progress.total; bar.value = progress.current;
	} else bar.removeAttribute("value");
	el("workStage").textContent = (applyingAll ? "Slice " + bulkPosition + " · " : "") + title;
	el("workTiming").textContent = "Elapsed " + timeText((progress.finished || now) - progress.started);
	if (progress.done) el("workResponse").textContent = progress.failed ? "See the error message below." : "Finished.";
	else if (!progress.received) el("workResponse").textContent = "Waiting for worker response…";
	else {
		const quiet = now - progress.received;
		el("workResponse").textContent = (quiet < 5000 ? "Worker responding" : "No worker response for " + Math.floor(quiet / 1000) + " s") + " · Last progress " + timeText(progress.idle + quiet) + " ago";
	}
}
function startWorkProgress(action) {
	clearInterval(workTimer);
	workProgress = { stage: action, started: Date.now(), received: null, idle: 0, current: null, total: null, done: false };
	el("workProgress").hidden = false;
	renderWorkProgress();
	workTimer = setInterval(renderWorkProgress, 1000);
}
function finishWorkProgress(failed) {
	clearInterval(workTimer);
	if (!workProgress) return;
	workProgress.done = true; workProgress.failed = failed; workProgress.finished = Date.now();
	renderWorkProgress();
}

function el(id) { return document.getElementById(id); }
function status(message, error) {
	el("status").textContent = message;
	el("status").classList.toggle("error", !!error);
	if (error) {
		el("count").textContent = "Operation failed — see message";
		el("status").scrollIntoView({ block: "nearest" });
	}
}
function controls() {
	const locked = busy || applyingAll;
	lockIds.forEach(id => { el(id).disabled = locked; });
	el("slice").disabled = locked || !files.length;
	el("previous").disabled = locked || index <= 0;
	el("next").disabled = locked || index < 0 || index >= files.length - 1;
	el("goTo").disabled = locked || !files.length;
	el("detect").disabled = locked || !token;
	el("save").disabled = locked || !detected || !filtersValid || filterPending;
	el("applyAll").disabled = locked || !detected || !filtersValid || filterPending;
	el("useInDetection").disabled = locked || !detected || !filtersValid || filterPending;
	el("filters").disabled = !detected || applyingAll || (busy && activeAction !== "filter");
	el("original").disabled = !originalImage;
	if (busy || applyingAll || filterPending || !filtersValid) el("bboxInfo").hidden = true;
	if (el("unsavedDialog").open) updateUnsavedDialog();
}
async function request(action, data) {
	if (busy) throw new Error("Wait for the current operation to finish.");
	busy = true;
	activeAction = action;
	controls();
	if (action !== "filter") startWorkProgress(action);
	try {
		const result = await ipc.invoke("detectLiveRequest", Object.assign({ action: action, token: token }, data));
		if (action !== "filter") finishWorkProgress(false);
		return result;
	} catch (error) {
		if (action !== "filter") finishWorkProgress(true);
		throw error;
	}
	finally {
		busy = false;
		activeAction = null;
		controls();
		if (filterPending && detected && !applyingAll) scheduleFilter();
	}
}
function params() {
	const values = {};
	filterIds.forEach(id => {
		if (el(id).value.trim() === "") throw new Error("Enter a value for " + id + ".");
		values[id] = Number(el(id).value);
	});
	return values;
}
function draw() {
	el("bboxInfo").hidden = true;
	context.clearRect(0, 0, canvas.width, canvas.height);
	if (!image) return;
	context.save();
	context.translate(offsetX, offsetY);
	context.scale(zoom, zoom);
	const displayedImage = el("original").getAttribute("aria-pressed") === "true" ? originalImage : image;
	context.drawImage(displayedImage || image, 0, 0, imageWidth, imageHeight);
	if (el("boxes").checked) {
		context.lineWidth = 1.5 / zoom;
		context.strokeStyle = "#ff5454";
		context.beginPath();
		indices.forEach(i => { const row = records[i]; context.rect(row[0], row[1], row[2] - row[0], row[3] - row[1]); });
		context.stroke();
	}
	context.restore();
}
function fit() {
	zoom = Math.min(canvas.width / imageWidth, canvas.height / imageHeight) * 0.95;
	offsetX = (canvas.width - imageWidth * zoom) / 2;
	offsetY = (canvas.height - imageHeight * zoom) / 2;
	draw();
}
function canSaveCurrent() { return detected && filtersValid && !filterPending && !busy && !applyingAll; }
function updateUnsavedDialog() {
	el("unsavedSave").disabled = !canSaveCurrent();
	el("unsavedMessage").textContent = "Save changes to " + (files[index] || "the current section") + " before moving?" + (canSaveCurrent() ? "" : " Return to this section and complete detection with valid filters before saving.");
}
async function mayLeave() {
	if (!dirty) return true;
	if (resolveUnsaved) return false;
	const choice = await new Promise(resolve => {
		resolveUnsaved = resolve;
		updateUnsavedDialog();
		el("unsavedDialog").showModal();
		el("unsavedCancel").focus();
	});
	if (choice === "save") return await save();
	if (choice === "discard") { dirty = false; return true; }
	return false;
}
function closeUnsaved(choice) {
	if (!resolveUnsaved) return;
	el("unsavedDialog").close();
	const resolve = resolveUnsaved;
	resolveUnsaved = null;
	resolve(choice);
}
el("unsavedSave").addEventListener("click", () => { if (canSaveCurrent()) closeUnsaved("save"); });
el("unsavedDiscard").addEventListener("click", () => closeUnsaved("discard"));
el("unsavedCancel").addEventListener("click", () => closeUnsaved("cancel"));
el("unsavedDialog").addEventListener("cancel", event => { event.preventDefault(); closeUnsaved("cancel"); });
function applySavedResult(saved) {
    records = saved.records; indices = saved.indices;
    detected = true; filtersValid = true; filterPending = false; dirty = false;
    filterIds.forEach(id => { el(id).value = String(saved.params[id]); });
    el("confidence").min = String(saved.floor);
    const inference = saved.inference || {};
    if (inference.model) {
        // Use the exact model path which produced these candidates.
        el("method").value = ["somata", "nuclei", "custom"].includes(saved.method) ? saved.method : "custom";
        el("model").value = inference.model;
        if (path.resolve(selectedModel()).toLowerCase() !== path.resolve(inference.model).toLowerCase()) el("method").value = "custom";
    }
    if (inference.tile) el("tile").value = String(inference.tile);
    el("floor").value = String(saved.floor);
    el("detectionSetup").open = false;
    el("count").textContent = indices.length + " kept / " + records.length + " candidates";
    draw(); controls();
}

async function loadSlice(nextIndex) {
	if (busy || resolveUnsaved || nextIndex < 0 || nextIndex >= files.length) return;
	if (!await mayLeave()) { el("slice").value = String(index); return; }
	index = nextIndex;
	el("slice").value = String(index);
	token = null; detected = false; filtersValid = false; records = []; indices = []; dirty = false;
	filterPending = false; clearTimeout(filterTimer);
	image = null; originalImage = null; draw();
	status("Loading " + files[index] + "…");
	try {
		const result = await request("load", { source: path.join(el("input").value, files[index]), output: el("output").value.trim() });
		const preview = new Image();
		const original = result.original_image ? new Image() : null;
		await Promise.all([[preview, result.image], [original, result.original_image]].filter(([picture]) => picture).map(([picture, source]) => new Promise((resolve, reject) => {
			picture.onload = resolve; picture.onerror = () => reject(new Error("Could not display image.")); picture.src = source;
		})));
		originalImage = original;
		token = result.token; imageWidth = result.width; imageHeight = result.height; image = preview; fit();
		el("detectionSetup").open = true;
		el("count").textContent = files[index] + " · " + imageWidth + " × " + imageHeight;
		if (result.saved_result) {
            applySavedResult(result.saved_result);
            status("Saved BBoxes and applied parameters loaded. No detection rerun required.");
        } else {
            status(result.restore_warning || "Ready. Detect this slice, then adjust the live filters.", !!result.restore_warning);
        }
	} catch (error) { token = null; detected = false; controls(); status(error.message, true); }
	controls();
}
function chooseOutput() {
	const slug = "live_" + new Date().toISOString().replace(/[:.]/g, "-") + "_" + Math.random().toString(16).slice(2, 8);
	const input = el("input").value.trim();
	const defaultBase = workspace.getPreset("detect").outdir || (input
		? path.join(path.dirname(input), "live_predictions")
		: path.join(os.homedir(), "PFAJar", "predictions"));
	return pipelineRuns.computeFinalOutputPath("detect", {
		slug: slug, runMode: "overwrite", branchOverride: el("method").value,
		indirAbs: input, signalBranch: el("liveSignalBranch").value,
		legacyOutBase: defaultBase,
	});
}
function refreshDefaultOutput() {
	if (!el("output").value.trim() || el("output").value === automaticOutput) {
		automaticOutput = chooseOutput();
		el("output").value = automaticOutput;
	}
}
function orderContext() {
	return {
		input_dir: loadedFolder,
		bundle_root: project.isActive() ? project.getBundleRoot() : "",
		dapi_dir: project.isActive() ? project.resolveRolePath("dapi") : (workspace.getWorkspace().paths.dapi || ""),
	};
}
function renderSectionOptions() {
	el("slice").replaceChildren();
	files.forEach((name, i) => {
		const option = document.createElement("option"), ap = ordering.ap_positions[sectionOrder.sliceId(name)];
		option.value = String(i); option.textContent = (i + 1) + ". " + name + (Number.isFinite(ap) ? " · AP " + ap : "");
		el("slice").appendChild(option);
	});
	if (index >= 0) el("slice").value = String(index);
}
async function loadFolder() {
	if (busy || applyingAll || resolveUnsaved) return;
	if (!await mayLeave()) return;
	try {
		const input = el("input").value.trim();
		files = fs.readdirSync(input, { withFileTypes: true }).filter(entry => entry.isFile() && /\.(tiff?|png|jpe?g)$/i.test(entry.name)).map(entry => entry.name).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
		if (!files.length) throw new Error("No TIFF, PNG, or JPEG images found in this folder.");
		el("input").value = input;
		loadedFolder = input;
		refreshDefaultOutput();
		index = -1;
		try { ordering = await request("order_info", orderContext()); }
		catch (error) { throw error; }
		files = files.filter(name => !(ordering.sections && ordering.sections[sectionOrder.sliceId(name)] && ordering.sections[sectionOrder.sliceId(name)].active === false));
		if (!files.length) throw new Error("No active sections in this folder.");
		files = sectionOrder.sortSections(files, ordering.saved_order.length ? "custom" : "filename", {}, ordering.saved_order);
		renderSectionOptions();
		dirty = false;
		await loadSlice(0);
	} catch (error) {
		files = []; index = -1;
		el("slice").replaceChildren();
		const placeholder = document.createElement("option");
		placeholder.textContent = "No sections loaded";
		el("slice").appendChild(placeholder);
		token = null; detected = false; filtersValid = false; indices = []; image = null; originalImage = null;
		controls(); draw(); status(error.message, true);
	}
}
async function inputChanged() {
	if (resolveUnsaved) return;
	if (el("input").value === loadedFolder && files.length) { refreshDefaultOutput(); return; }
	if (!await mayLeave()) { el("input").value = loadedFolder; return; }
	refreshDefaultOutput();
	token = null; detected = false; filtersValid = false; dirty = false;
	indices = []; image = null; originalImage = null; controls(); draw();
	void loadFolder();
}
function selectedModel() {
	if (el("method").value === "custom") return el("model").value.trim();
	return path.join(os.homedir(), branding.HOME_DIR, "models", el("method").value === "nuclei" ? "ankou.pt" : "chaosdruid.pt");
}
async function filterNow() {
	if (busy || !detected) return;
	filterPending = false;
	filtersValid = false;
	try {
		const result = await request("filter", { params: params() });
		indices = result.indices;
		filtersValid = true;
		el("count").textContent = indices.length + " kept / " + records.length + " candidates";
		draw();
		status("Filters updated. Save this slice to keep the result.");
		controls();
		return true;
	} catch (error) { status(error.message, true); controls(); return false; }
}
function scheduleFilter() {
	clearTimeout(filterTimer);
	filterTimer = setTimeout(filterNow, 120);
}
async function detect() {
	if (!el("output").value.trim()) { status("Choose a predictions output folder.", true); return; }
	status("Detecting candidates… Progress appears below.");
	el("count").textContent = "Detecting candidates…";
	detected = false; records = []; indices = []; draw();
	try {
		const result = await request("detect", {
			model: selectedModel(), tile: Number(el("tile").value),
			floor: Number(el("floor").value), output: el("output").value.trim(),
		});
		records = result.records; detected = true; dirty = true;
		if (result.saved_params) filterIds.forEach(id => { el(id).value = String(result.saved_params[id]); });
		el("detectionSetup").open = false;
		el("confidence").min = String(result.floor);
		if (Number(el("confidence").value) < result.floor) el("confidence").value = String(result.floor);
		if (await filterNow()) status((result.cached ? "Cached candidates loaded" : "Detection complete") + " · " + result.device + ". Adjust filters, then save this slice." + (result.cache_warning ? " " + result.cache_warning : ""));
	} catch (error) { status(error.message, true); }
	controls();
}
async function save() {
	try {
		if (!canSaveCurrent()) throw new Error("Run detection with valid filters before saving this section.");
		const output = el("output").value.trim();
		if (!output) throw new Error("Choose a predictions output folder.");
		const stem = path.parse(files[index]).name.replace(/\.ome$/i, "");
		if (fs.existsSync(path.join(output, "Predictions_" + stem + ".pkl")) && !window.confirm("Replace the saved predictions for this slice?")) return false;
		const result = await request("save", { output: output, params: params(), method: el("method").value });
		dirty = false;
		if (project.isActive()) {
			const base = project.resolveRolePath("predictions");
			const relative = path.relative(base, output);
			if (relative && !relative.startsWith("..") && !path.isAbsolute(relative)) project.setActiveRunForRole("predictions", relative.replace(/\\/g, "/"));
		}
		status("Saved " + result.count + (result.count === 1 ? " cell" : " cells") + ". Count Brain can use " + output);
		return true;
	} catch (error) { status(error.message, true); return false; }
}

async function applyAll() {
	if (busy || applyingAll || !detected || !filtersValid || filterPending) return;
	const settings = params();
	const baseOutput = el("output").value.trim();
	const method = el("method").value;
	const inference = { model: selectedModel(), tile: Number(el("tile").value), floor: Number(el("floor").value) };
	const output = chooseOutput() + "_all";
	const stems = files.map(name => path.parse(name).name.replace(/\.ome$/i, ""));
	if (new Set(stems).size !== stems.length) throw new Error("Multiple input images share a slice ID. Choose a folder with one image per slice.");
	const message = [
		"Apply the current settings to all " + files.length + " slices in this input folder?",
		"", "Confidence " + settings.confidence + " · Area " + settings.area + " px²",
		"Eccentricity " + settings.eccentricity + " · Intensity " + settings.intensity,
		"Model " + inference.model,
		"Tile " + inference.tile + " · Candidate floor " + inference.floor,
		"", "Results will be saved in a new run:", output,
	].join("\n");
	if (!window.confirm(message)) return;
	applyingAll = true; stopAll = false;
	filterPending = false; clearTimeout(filterTimer);
	el("stopAll").hidden = false;
	el("stopAll").disabled = false;
	controls();
	const saved = [], failed = [];
	let restored = false;
	try {
		for (let i = 0; i < files.length && !stopAll; i++) {
			bulkPosition = (i + 1) + " / " + files.length;
			el("count").textContent = "Processing slice " + bulkPosition;
			status("Processing " + files[i] + " with the confirmed settings…");
			try {
				const loaded = await request("load", { source: path.join(loadedFolder, files[i]), preview: false });
				await request("detect", Object.assign({ token: loaded.token, output: baseOutput }, inference));
				const result = await request("save", { token: loaded.token, output: output, params: settings, method: method });
				saved.push({ slice: files[i], count: result.count });
			} catch (error) {
				failed.push({ slice: files[i], error: error.message });
				el("log").textContent = (el("log").textContent + files[i] + ": " + error.message + "\n").slice(-16000);
			}
		}
	} finally {
		// Reload only the worker's current plane. Keep the displayed image, pan,
		// zoom and live filter choices from the calibration slice.
		try {
            const currentWasSaved = saved.some(item => item.slice === files[index]);
            if (saved.length) el("output").value = output;
            const loaded = await request("load", { source: path.join(loadedFolder, files[index]), preview: false, output: currentWasSaved ? output : "" });
            token = loaded.token;
            if (loaded.saved_result) {
                applySavedResult(loaded.saved_result);
            } else {
                const original = await request("detect", Object.assign({ output: baseOutput }, inference));
                records = original.records;
                indices = (await request("filter", { params: settings })).indices;
                detected = true; filtersValid = true; dirty = !currentWasSaved;
            }
            restored = true;
		} catch (error) {
			token = null; detected = false; filtersValid = false; indices = [];
			el("log").textContent += "Reload the calibration slice: " + error.message + "\n";
		}
		applyingAll = false; bulkPosition = "";
		el("stopAll").hidden = true;
		controls(); draw();
	}
	try {
		fs.mkdirSync(output, { recursive: true });
		const target = path.join(output, "live_batch_summary.json");
		const temporary = target + ".tmp";
		fs.writeFileSync(temporary, JSON.stringify({
			timestamp: new Date().toISOString(), input: loadedFolder,
			params: settings, inference: inference, planned: files, saved: saved,
			failed: failed, stopped: stopAll,
		}, null, 2));
		fs.renameSync(temporary, target);
	} catch (error) { el("log").textContent += "Could not write batch summary: " + error.message + "\n"; }
	el("count").textContent = restored ? indices.length + " kept / " + records.length + " candidates" : "Reload the calibration slice";
	if (project.isActive() && saved.length) {
		const relative = pipelineRuns.relFromRoleBase("detect", output);
		if (relative) project.setActiveRunForRole("predictions", relative);
	}
	status((stopAll ? "Stopped. " : "Completed. ") + saved.length + " slices saved, " + failed.length + " failed. Output: " + output + (restored ? "" : ". Reload the current slice before editing."), failed.length > 0 || !restored);
}

filterIds.forEach(id => el(id).addEventListener("input", () => { dirty = true; filterPending = true; controls(); scheduleFilter(); }));
["tile", "floor", "method", "model"].forEach(id => el(id).addEventListener("change", () => {
	if (id === "method") refreshDefaultOutput();
	detected = false; indices = []; filterPending = false; dirty = true; controls(); draw();
	status("Detection settings changed. Detect this slice again.");
}));
el("slice").addEventListener("change", () => loadSlice(Number(el("slice").value)));
el("previous").addEventListener("click", () => loadSlice(index - 1));
el("next").addEventListener("click", () => loadSlice(index + 1));
el("goTo").addEventListener("click", () => {
	el("sectionNumber").max = String(files.length);
	el("sectionNumber").value = String(index + 1);
	el("sectionNumber").setCustomValidity("");
	el("sectionRange").textContent = "Section 1–" + files.length;
	el("goToDialog").showModal();
	el("sectionNumber").focus(); el("sectionNumber").select();
});
el("goToForm").addEventListener("submit", event => {
	event.preventDefault();
	const section = Number(el("sectionNumber").value);
	if (!Number.isInteger(section) || section < 1 || section > files.length) {
		el("sectionNumber").setCustomValidity("Enter a section number from 1 to " + files.length + ".");
		el("sectionNumber").reportValidity();
		return;
	}
	el("goToDialog").close();
	void loadSlice(section - 1);
});
el("sectionNumber").addEventListener("input", () => el("sectionNumber").setCustomValidity(""));
el("cancelGoTo").addEventListener("click", () => el("goToDialog").close());
el("loadFolder").addEventListener("click", loadFolder);
el("detect").addEventListener("click", detect);
el("save").addEventListener("click", save);
el("applyAll").addEventListener("click", () => applyAll().catch(error => status(error.message, true)));
el("stopAll").addEventListener("click", () => { stopAll = true; el("stopAll").disabled = true; status("Stopping after the current slice is saved…"); });
el("fit").addEventListener("click", fit);
el("boxes").addEventListener("change", draw);
el("original").addEventListener("click", () => {
	el("original").setAttribute("aria-pressed", String(el("original").getAttribute("aria-pressed") !== "true"));
	draw();
});
[["browseInput", "input", "input"], ["browseOutput", "output", "output"], ["browseModel", "model", "model"]].forEach(([button, field, kind]) => {
	el(button).addEventListener("click", async () => {
		try { const selected = await ipc.invoke("detectLiveBrowse", kind); if (selected) { el(field).value = selected; el(field).dispatchEvent(new Event("change")); } }
		catch (error) { status(error.message, true); }
	});
});
viewport.addEventListener("wheel", event => {
	event.preventDefault();
	const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
	const next = Math.max(0.005, Math.min(16, zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
	offsetX = x - (x - offsetX) * next / zoom; offsetY = y - (y - offsetY) * next / zoom;
	zoom = next; draw();
}, { passive: false });
viewport.addEventListener("pointerdown", event => { drag = [event.clientX - offsetX, event.clientY - offsetY]; viewport.setPointerCapture(event.pointerId); });
viewport.addEventListener("pointermove", event => { if (drag) { offsetX = event.clientX - drag[0]; offsetY = event.clientY - drag[1]; draw(); } });
viewport.addEventListener("pointermove", event => {
	if (hoverFrame !== null) cancelAnimationFrame(hoverFrame);
	hoverFrame = requestAnimationFrame(() => {
		hoverFrame = null;
		const tooltip = el("bboxInfo");
		tooltip.hidden = true;
		if (drag || busy || applyingAll || filterPending || !filtersValid || !detected || !image || !el("boxes").checked) return;
		const rect = viewport.getBoundingClientRect();
		const x = event.clientX - rect.left, y = event.clientY - rect.top;
		const imageX = (x - offsetX) / zoom, imageY = (y - offsetY) / zoom;
		if (imageX < 0 || imageY < 0 || imageX > imageWidth || imageY > imageHeight) return;
		const index = bboxInfo.hitCandidate(records, indices, imageX, imageY, 4 / zoom);
		if (index === null) return;
		tooltip.textContent = bboxInfo.candidateDescription(records[index], params(), index);
		tooltip.hidden = false;
		tooltip.style.left = Math.max(8, Math.min(x + 16, viewport.clientWidth - tooltip.offsetWidth - 8)) + "px";
		tooltip.style.top = Math.max(8, Math.min(y + 16, viewport.clientHeight - tooltip.offsetHeight - 8)) + "px";
	});
});
viewport.addEventListener("pointerleave", () => {
	if (hoverFrame !== null) cancelAnimationFrame(hoverFrame);
	hoverFrame = null;
	el("bboxInfo").hidden = true;
});
viewport.addEventListener("pointerup", () => { drag = null; });
viewport.addEventListener("pointercancel", () => { drag = null; });
window.addEventListener("beforeunload", event => { if (dirty || busy || applyingAll) { event.preventDefault(); event.returnValue = false; } });
function setOptionsWidth(width) {
	const maximum = Math.max(0, main.clientWidth - 188);
	const minimum = Math.min(220, maximum);
	optionsWidth = Math.max(minimum, Math.min(maximum, width));
	main.style.setProperty("--options-width", optionsWidth + "px");
	splitter.setAttribute("aria-valuemin", String(minimum));
	splitter.setAttribute("aria-valuemax", String(maximum));
	splitter.setAttribute("aria-valuenow", String(Math.round(optionsWidth)));
}
splitter.addEventListener("pointerdown", event => {
	if (event.button !== 0) return;
	event.preventDefault();
	splitterDrag = { x: event.clientX, width: optionsWidth };
	splitter.setPointerCapture(event.pointerId);
	document.body.classList.add("resizing-options");
});
splitter.addEventListener("pointermove", event => {
	if (splitterDrag) setOptionsWidth(splitterDrag.width + splitterDrag.x - event.clientX);
});
function endSplitterDrag() {
	splitterDrag = null;
	document.body.classList.remove("resizing-options");
}
splitter.addEventListener("pointerup", endSplitterDrag);
splitter.addEventListener("pointercancel", endSplitterDrag);
splitter.addEventListener("lostpointercapture", endSplitterDrag);
splitter.addEventListener("keydown", event => {
	if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
	event.preventDefault();
	setOptionsWidth(optionsWidth + (event.key === "ArrowLeft" ? 20 : -20));
});
window.addEventListener("resize", () => setOptionsWidth(optionsWidth));
setOptionsWidth(optionsWidth);
new ResizeObserver(() => {
	const previousWidth = canvas.width, previousHeight = canvas.height;
	canvas.width = viewport.clientWidth; canvas.height = viewport.clientHeight;
	if (image) {
		offsetX += (canvas.width - previousWidth) / 2;
		offsetY += (canvas.height - previousHeight) / 2;
	}
	draw();
}).observe(viewport);
ipc.on("detectLiveProgress", (_event, line) => {
	el("log").textContent = (el("log").textContent + line + "\n").slice(-16000);
	if (activeAction !== "detect") return;
	const tiles = /: tile (\d+)\/(\d+)/.exec(line);
	if (tiles) el("count").textContent = (applyingAll ? "Slice " + bulkPosition + " · " : "") + "Detecting tiles " + tiles[1] + " / " + tiles[2];
	if (line.includes("stage=model")) el("count").textContent = "Loading detection model…";
	if (line.includes("stage=inference")) el("count").textContent = "Running tiled detection…";
	if (line.includes("stage=measurements")) el("count").textContent = "Measuring candidate features…";
});
ipc.on("detectLiveStatus", (_event, progress) => {
	if (!workProgress || workProgress.done || !busy || activeAction === "filter") return;
	Object.assign(workProgress, {
		stage: progress.stage, current: progress.current, total: progress.total,
		received: Date.now(), idle: progress.idle_ms,
	});
	renderWorkProgress();
});
try {
	project.tryRestoreActiveProject();
	workspace.loadWorkspace();
	el("input").value = project.isActive() ? pipelineRuns.resolveInputLeafAbsForStep("detect", "max") : (workspace.getPreset("detect").indir || "");
	if (project.isActive()) {
		el("liveDatasetSection").hidden = false;
		require("./max_dataset_picker").wireMaxDatasetPicker({
			storageKey: "pfa.detectLive.dataset", indirInput: el("input"),
			sectionId: "liveDatasetSection", branchSelectId: "liveSignalBranch", datasetSelectId: "liveDataset",
			defaultBranch: () => "somata",
		});
	}
	refreshDefaultOutput();
} catch (error) { status(error.message, true); }
controls();
el("input").addEventListener("change", inputChanged);
el("liveSignalBranch").addEventListener("change", inputChanged);
el("liveDataset").addEventListener("change", inputChanged);
if (el("input").value.trim()) void loadFolder();

el("useInDetection").addEventListener("click", async () => {
    if (busy || applyingAll || !detected || !filtersValid || filterPending) return;
    try {
        const values = params();
        const settings = require("./detect_handoff").normalize(Object.assign({}, values, {
            version:1, indir:loadedFolder,
            outdir:project.isActive() ? project.resolveRolePath("predictions") : (workspace.getPreset("detect").outdir || path.dirname(el("output").value.trim())),
            model:selectedModel(), method:el("method").value, tile:Number(el("tile").value),
            bundleRoot:project.isActive() ? project.getBundleRoot() : "", signalBranch:el("liveSignalBranch").value,
        }));
        busy = true; controls();
        await ipc.invoke("detectLiveUseSettings", settings);
        status("Settings sent to Cell Detection. Review the main window, then select Next — Process to run.");
    } catch (error) { status(error.message, true); }
    finally { busy = false; controls(); }
});
