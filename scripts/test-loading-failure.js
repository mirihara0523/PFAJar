"use strict";
const assert=require("assert"), fs=require("fs"), path=require("path"), vm=require("vm");
const listeners={},sent=[],nodes={};
for(const id of ["status","loadingSpinner","setupFailure","setupFailureDetails","showSetupLog","version"]){
	nodes[id]={hidden:false,textContent:"",addEventListener:(event,action)=>{nodes[id][event]=action;}};
}
nodes.setupFailure.hidden=true;
const ipc={on:(event,action)=>listeners[event]=action,send:(event)=>sent.push(event)};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../js/loading.js"),"utf8"),{
	require:()=>({ipcRenderer:ipc}),document:{getElementById:id=>nodes[id]||null},
});
listeners.updateStatus(null,"Updating packages...");
assert.strictEqual(nodes.status.textContent,"Updating packages...");
listeners.bootstrapFailed(null,{environment:"test/.pfajar",detail:"pip failed: <not HTML>"});
assert.strictEqual(nodes.loadingSpinner.hidden,true);
assert.strictEqual(nodes.setupFailure.hidden,false);
assert.ok(nodes.status.textContent.includes("failed"));
assert.ok(nodes.setupFailureDetails.textContent.includes("pip failed: <not HTML>"));
listeners.updateStatus(null,{timestamp:Date.now(),message:"Updating packages..."});
assert.ok(nodes.status.textContent.includes("failed"),"late progress must not hide a fatal error");
nodes.showSetupLog.click();assert.ok(sent.includes("showLogWindow"));
console.log("test-loading-failure.js: OK (failure state, stopped spinner, details, stale-progress guard, log button)");
