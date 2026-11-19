"use strict";
const fs = require("fs"), path = require("path");
function readSettings(root) {
  if (!root) return {};
  const meta = path.join(root, ".masonjar");
  if (fs.existsSync(path.join(meta, "section_flip_pending.json"))) throw new Error("An interrupted section flip needs recovery. Open Align Sections Order before analysing this project.");
  const file = path.join(meta, "section_settings.json");
  if (!fs.existsSync(file)) return {};
  const raw = JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, ""));
  if (raw.version !== 1 || !raw.sections || typeof raw.sections !== "object" || Array.isArray(raw.sections)) throw new Error("Invalid section settings.");
  for (const [id, state] of Object.entries(raw.sections)) {
    if (!id || !state || typeof state !== "object" || Array.isArray(state) || ["active", "flip_x"].some(key => key in state && typeof state[key] !== "boolean")) throw new Error("Invalid section settings entry.");
  }
  return raw.sections;
}
function activeIds(ids, root) {
  const states = readSettings(root);
  return orderedIds(ids.filter(id => !states[id] || states[id].active !== false), root);
}
function orderedIds(ids, root) {
  const active = ids.slice();
  if (!root) return active;
  const file = path.join(root, ".masonjar", "slice_order.json");
  if (!fs.existsSync(file)) return active;
  const raw = JSON.parse(fs.readFileSync(file,"utf8").replace(/^\uFEFF/, ""));
  if (!raw || !Array.isArray(raw.slice_ids) || raw.slice_ids.some(id => typeof id !== "string" || !id)) throw new Error("Invalid shared section order.");
  if (!raw.slice_ids.length) return active;
  const ranks = new Map(raw.slice_ids.map((id,index)=>[id,index]));
  return active.sort((a,b)=>(ranks.has(a)?ranks.get(a):ranks.size)-(ranks.has(b)?ranks.get(b):ranks.size) || a.localeCompare(b,undefined,{numeric:true}));
}
module.exports = { readSettings, activeIds, orderedIds };
