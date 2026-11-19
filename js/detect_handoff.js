"use strict";
const path = require("path");
function normalize(input) {
  if (!input || input.version !== 1) throw new Error("Invalid Viewer settings transfer.");
  const out = {version:1};
  for (const key of ["indir","outdir","model","bundleRoot","signalBranch","method"]) {
    const value = input[key] == null ? "" : String(input[key]).trim();
    if (value.includes("\0")) throw new Error("Invalid path or setting.");
    out[key] = value;
  }
  if (!out.indir || !out.outdir || !out.model) throw new Error("Select the input, output base and model before sending settings.");
  if (!["somata","nuclei","custom"].includes(out.method)) throw new Error("Invalid detection model selection.");
  for (const key of ["confidence","area","eccentricity","intensity","tile"]) {
    if (input[key] === "" || input[key] == null) throw new Error("Missing Viewer parameter: " + key);
    out[key] = Number(input[key]);
    if (!Number.isFinite(out[key])) throw new Error("Invalid Viewer parameter: " + key);
  }
  if (!(out.confidence > 0 && out.confidence <= 1) || out.area < 0 || out.eccentricity < 0 || out.eccentricity > 1 || out.intensity < 0 || out.intensity > 255 || !Number.isInteger(out.tile) || out.tile < 64 || out.tile > 4096) throw new Error("Viewer parameters are outside supported ranges.");
  return out;
}
function assertProject(settings, currentRoot) {
  const canonical = value => value ? path.resolve(value).toLowerCase() : "";
  if (canonical(settings.bundleRoot) !== canonical(currentRoot)) throw new Error("The active project changed. Return to the Viewer's project before transferring these settings.");
}
function applyForm(form, input) {
  const settings = normalize(input);
  for (const [field,key] of [["indir","indir"],["outdir","outdir"],["model","model"],["tile","tile"],["confidence","confidence"],["area","area"],["eccentricity","eccentricity"],["intensityMin","intensity"]]) form[field].value = String(settings[key]);
  form.multichannel.checked = false;
  if (form.flatOutput) form.flatOutput.checked = false;
  return settings;
}
module.exports = {normalize,assertProject,applyForm};
