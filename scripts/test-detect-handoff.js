"use strict";
const assert=require("assert"),h=require("../js/detect_handoff"),common=require("../js/detect_common");
const input={version:1,indir:"D:\\brain\\starters\\run",outdir:"D:\\brain\\predictions",model:"D:\\model.pt",bundleRoot:"D:\\brain",method:"custom",signalBranch:"starters",tile:640,confidence:.65,area:100,eccentricity:.15,intensity:70};
function form(){return Object.fromEntries(["indir","outdir","model","tile","confidence","area","eccentricity","intensityMin","multichannel","flatOutput"].map(id=>[id,{value:"",checked:true}]))}
const f=form();h.applyForm(f,input);let parsed=common.parseDetectParams(f);
assert.strictEqual(parsed.confidence,.65);assert.strictEqual(parsed.area,100);assert.strictEqual(parsed.eccentricity,.15);assert.strictEqual(parsed.intensityMin,70);assert.strictEqual(parsed.tile,640);assert.strictEqual(parsed.model,input.model);assert.strictEqual(parsed.multichannel,false);assert.strictEqual(f.flatOutput.checked,false);
h.applyForm(f,{...input,confidence:1,area:0,eccentricity:0,intensity:0});parsed=common.parseDetectParams(f);assert.strictEqual(parsed.confidence,1);assert.strictEqual(parsed.area,0);assert.strictEqual(parsed.eccentricity,0);assert.strictEqual(parsed.intensityMin,0);
h.applyForm(f,{...input,eccentricity:1});assert.strictEqual(common.parseDetectParams(f).eccentricity,1);
for(const change of [{confidence:0},{confidence:NaN},{area:-1},{eccentricity:2},{intensity:256},{tile:640.5},{model:""},{version:2}])assert.throws(()=>h.normalize({...input,...change}));
h.assertProject(input,"D:\\brain");assert.throws(()=>h.assertProject(input,"D:\\other"),/project changed/);assert.throws(()=>h.assertProject(input,""),/project changed/);
console.log("Viewer settings transfer: exact values, zero/end-point filters, single-channel/non-flat output and stale-project validation passed.");
