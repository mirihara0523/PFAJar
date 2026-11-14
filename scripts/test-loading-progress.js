"use strict";
const assert=require("assert"),fs=require("fs"),path=require("path"),vm=require("vm");
const listeners={},nodes={};
for(const id of ["status","loadingSpinner","setupFailure","setupFailureDetails","packageProgress","packageActivity","packageTiming","packageProcessState","packageOutput"]){
	nodes[id]={hidden:false,textContent:"",scrollTop:0,clientHeight:100,scrollHeight:100};
}
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../js/loading.js"),"utf8"),{
	require:()=>({ipcRenderer:{on:(event,fn)=>listeners[event]=fn,send:()=>{}}}),document:{getElementById:id=>nodes[id]||null},
});
const base={id:"one",startedAt:10,elapsedMs:65000,quietMs:1000,activity:"Downloading torch",lines:["Collecting torch"],state:"running"};
listeners.packageInstallProgress(null,base);
assert.ok(nodes.packageActivity.textContent.includes("Downloading torch"));assert.ok(nodes.packageTiming.textContent.includes("01:05"));
assert.strictEqual(nodes.packageProgress.hidden,false);
listeners.packageInstallProgress(null,{...base,quietMs:30000,lines:[]});
assert.ok(nodes.packageProcessState.textContent.includes("still running"));
listeners.packageInstallProgress(null,{...base,lines:Array.from({length:200},(_,i)=>"line "+i)});
assert.strictEqual(nodes.packageOutput.textContent.split("\n").length,150);
listeners.packageInstallProgress(null,{...base,state:"complete",lines:[]});
assert.ok(nodes.packageProcessState.textContent.includes("completed"));
listeners.bootstrapFailed(null,{environment:".pfajar",detail:"fixture failure"});
const previous=nodes.packageProcessState.textContent;
listeners.packageInstallProgress(null,{...base,lines:["late output"],quietMs:0});
assert.strictEqual(nodes.packageProcessState.textContent,previous);
assert.ok(nodes.status.textContent.includes("failed"));
console.log("test-loading-progress.js: OK (activity, elapsed/quiet time, bounded output, completion, fatal-error guard)");
