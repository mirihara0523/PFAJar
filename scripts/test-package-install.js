"use strict";
const assert=require("assert"), {EventEmitter}=require("events"), {PassThrough}=require("stream");
const {installRequirements}=require("../js/package_install");
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function fixture(noCache=false){
	const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();
	const events=[],logs=[],calls=[];
	const promise=installRequirements({pythonPath:"C:\\PFA Test\\python.exe",requirementsPath:"C:\\PFA Test\\requirements.txt",
		cwd:"fixture",env:{PFAJAR_HOME:"fixture/.pfajar"},noCache,
		spawn:(...args)=>{calls.push(args);return child;},onEvent:e=>events.push(e),onLog:s=>logs.push(s)});
	return {child,events,logs,calls,promise};
}
async function main(){
	const f=fixture();
	f.child.stdout.write("Collecting torch\nDownloading torch.whl\n");
	const korean=Buffer.from("설치 중\n");f.child.stderr.write(korean.subarray(0,2));f.child.stderr.write(korean.subarray(2));
	await delay(260);
	assert.ok(f.events.some(e=>e.state==="running" && e.lines.includes("Downloading torch.whl")),"output must arrive before close");
	assert.ok(f.logs.includes("설치 중"));
	f.child.stdout.write("Successfully installed torch");f.child.emit("close",0,null);await f.promise;
	assert.strictEqual(f.events.at(-1).state,"complete");assert.ok(f.logs.includes("Successfully installed torch"));
	const [exe,args,options]=f.calls[0];assert.strictEqual(exe,"C:\\PFA Test\\python.exe");
	assert.ok(args.includes("C:\\PFA Test\\requirements.txt"));assert.ok(args.includes("-u"));
	assert.strictEqual(options.shell,false);assert.strictEqual(options.windowsHide,true);
	assert.ok(!args.includes("--no-cache-dir"));
	const failing=fixture(true);
	const rejected=assert.rejects(failing.promise,error=>error.message.includes("exit 1") && error.stderr.includes("fixture error"));
	failing.child.stderr.write("ERROR: fixture error\n");failing.child.emit("close",1,null);await rejected;
	assert.strictEqual(failing.events.at(-1).state,"failed");assert.ok(failing.calls[0][1].includes("--no-cache-dir"));
	await delay(260);assert.strictEqual(failing.events.at(-1).state,"failed","no late running events after failure");
	const quiet=fixture();await delay(1100);
	assert.ok(quiet.events.some(e=>e.state==="running"&&e.quietMs>=900));quiet.child.emit("close",0,null);await quiet.promise;
	const burst=fixture();for(let i=0;i<2000;i++)burst.child.stdout.write("line "+i+"\n");
	burst.child.emit("close",0,null);await burst.promise;
	assert.strictEqual(burst.logs.length,2000,"every completed line reaches durable logging");
	assert.ok(burst.events.every(e=>e.lines.length<=150));assert.ok(burst.events.at(-1).lines.includes("line 1999"));
	const spawnFailure=fixture();const spawnRejected=assert.rejects(spawnFailure.promise,/spawn failed/);
	spawnFailure.child.emit("error",Error("spawn failed"));spawnFailure.child.emit("close",-1,null);await spawnRejected;
	assert.strictEqual(spawnFailure.events.filter(e=>e.state==="failed").length,1);
	console.log("test-package-install.js: OK (streaming, UTF-8, success/failure, quiet heartbeat, bounds, launch arguments; no pip run)");
}
main().catch(error=>{console.error(error);process.exitCode=1;});
