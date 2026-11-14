"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const ts = require("typescript");
const tar = require("tar");
const helpers = require("./test-helpers");
const code = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
const ast = ts.createSourceFile("main.js", code, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = ["setupPython", "setupEnvironment", "downloadResources", "installRuntimeArchive", "installPackages",
	"pythonShellEnvBase", "updatePythonDependencies", "fixMissingDirectories", "reportBootstrapFailure",
	"beginAppBootstrap", "downloadFile", "flushLogUiQueue"];
const functions = ast.statements.filter(n => ts.isFunctionDeclaration(n) && names.includes(n.name.text))
	.map(n => n.getText(ast)).join("\n");
const compilerHelpers = ast.statements.filter(n => ts.isVariableStatement(n) &&
	n.declarationList.declarations.some(d => ["__awaiter", "__assign"].includes(d.name.getText(ast))))
	.map(n => n.getText(ast)).join("\n");

function subject(root, overrides = {}) {
	const sends = [], errors = [], commands = [], installs = [];
	const home = path.join(root, ".pfajar");
	fs.mkdirSync(home, {recursive:true});
	const context = {
		fs, path, tar, URL, Buffer, stream:require("stream"), console,
		homeDir:home, pythonPath:path.join(home,"python"), envPath:path.join(home,"benv"),
		envPythonPath:path.join(home,"benv/Scripts"), pyCommand:"python.exe", appDir:__dirname,
		process:{platform:"win32",arch:"x64",env:{PYTHONHOME:"Mason Python",PYTHONPATH:"Mason packages",VIRTUAL_ENV:"Mason env"}},
		sendLoadingStatus:(_win,message)=>sends.push(message),
		dialog:{showMessageBoxSync:(_win,message)=>errors.push(message)},
		createLogFile:()=>{}, exec:async (command,options)=>{commands.push({command,options});return {stdout:"ok",stderr:""};},
		loadMenuAndCheckUpdates:()=>sends.push("MENU"),
		packageInstaller:{installRequirements:async options=>{installs.push(options);}},
		ensureLogWindowVisible:opts=>{assert.ok(opts.force);sends.push("LOG_OPENED");}, logWin:null,
		...overrides,
	};
	vm.createContext(context);
	vm.runInContext(compilerHelpers + "\n" + functions + "\nthis.subject = {"+names.join(",")+"};",context);
	return {context,api:context.subject,home,sends,errors,commands,installs,
		win:{webContents:{send:(_event,message)=>sends.push(message)},isDestroyed:()=>false}};
}

async function main() {
	const root = helpers.tmpDir("pfa-runtime-tests-");
	let passed = 0;
	try {
		// Existing interpreter, missing/complete venv: paths stay under PFA's home.
		const s=subject(root);
		fs.mkdirSync(s.context.pythonPath,{recursive:true});
		fs.writeFileSync(path.join(s.context.pythonPath,"python.exe"),"fixture");
		assert.strictEqual(await s.api.setupPython(s.win),true);
		fs.mkdirSync(s.context.envPythonPath,{recursive:true});
		fs.writeFileSync(path.join(s.context.envPythonPath,"python.exe"),"fixture");
		assert.strictEqual(await s.api.setupPython(s.win),false); passed++;
		const env=s.api.pythonShellEnvBase();
		for(const key of ["PYTHONHOME","PYTHONPATH","VIRTUAL_ENV"])assert.strictEqual(env[key],undefined);
		assert.strictEqual(env.PFAJAR_HOME,s.home); assert.strictEqual(env.PYTHONNOUSERSITE,"1"); passed++;
		await s.api.updatePythonDependencies(s.win);
		assert.strictEqual(s.installs[0].pythonPath,path.join(s.context.envPythonPath,"python.exe"));
		assert.strictEqual(s.installs[0].env.PYTHONHOME,undefined);
		assert.strictEqual(s.installs[0].noCache,true); passed++;
		// A real package failure must reach the single visible bootstrap error.
		s.context.updatePythonDependencies=async()=>{throw Error("fixture pip failure");};
		s.context.maybeMigrateLegacyHome=async()=>true;
		s.context.checkLocalDir=()=>{};s.context.ensureCoordinatorDir=()=>{};
		s.context.ioFairshareDir="fixture";s.context.detectLinkMbps=()=>{};
		s.context.io_fairshare_1={ensureCoordinatorDir:()=>{},detectLinkMbps:()=>Promise.resolve()};
		s.context.require=()=>{throw Error("optional dialog module unavailable");};
		await s.api.beginAppBootstrap(s.win);
		assert.strictEqual(s.errors.length,1);assert.ok(s.errors[0].detail.includes("fixture pip failure"));
		assert.ok(s.sends.includes("LOG_OPENED"));
		assert.ok(s.sends.some(message=>message && message.detail && message.detail.includes("fixture pip failure")));
		assert.ok(!s.sends.includes("MENU")); passed++;
		// Reuse copied data with a valid manifest, even during first-time venv setup.
		const r=subject(path.join(root,"resources"));let downloads=[];
		for(const entry of ["models","embeddings","nrrd"]){
			fs.mkdirSync(path.join(r.home,entry));fs.writeFileSync(path.join(r.home,entry,"marker"),"data");
		}
		fs.writeFileSync(path.join(r.home,"models/chaosdruid.pt"),"model");
		const manifest={models:"v952",embeddings:"v6",nrrd:"v91"};
		fs.writeFileSync(path.join(r.home,"manifest.json"),JSON.stringify(manifest));
		r.context.installRuntimeArchive=async (_win,name)=>downloads.push(name);
		await r.api.downloadResources(r.win,true);assert.deepStrictEqual(downloads,[]); passed++;
		fs.unlinkSync(path.join(r.home,"models/chaosdruid.pt"));
		r.context.installRuntimeArchive=async()=>{throw Error("fixture archive failure");};
		await assert.rejects(r.api.fixMissingDirectories(r.win),/fixture archive failure/);
		assert.deepStrictEqual(JSON.parse(fs.readFileSync(path.join(r.home,"manifest.json"))),manifest); passed++;
		// Real local tar extraction: invalid archives preserve existing resources.
		const a=subject(path.join(root,"archive"));const fixture=path.join(root,"tar-fixture");
		fs.mkdirSync(path.join(fixture,"models"),{recursive:true});
		fs.writeFileSync(path.join(fixture,"models/chaosdruid.pt"),"new model");
		const archive=path.join(root,"fixture.tar.gz");await tar.c({gzip:true,file:archive,cwd:fixture},["models"]);
		a.context.downloadFile=async(_url,target)=>fs.copyFileSync(archive,target);
		fs.mkdirSync(path.join(a.home,"models"));fs.writeFileSync(path.join(a.home,"models/old"),"old model");
		await a.api.installRuntimeArchive(a.win,"models","mock://local");
		assert.strictEqual(fs.readFileSync(path.join(a.home,"models/chaosdruid.pt"),"utf8"),"new model"); passed++;
		a.context.downloadFile=async(_url,target)=>fs.writeFileSync(target,"not an archive");
		await assert.rejects(a.api.installRuntimeArchive(a.win,"models","mock://invalid"));
		assert.strictEqual(fs.readFileSync(path.join(a.home,"models/chaosdruid.pt"),"utf8"),"new model");
		assert.ok(!fs.readdirSync(a.home).some(n=>n.startsWith(".setup-"))); passed++;
		// Fresh setup uses only the new interpreter and venv, not system Python.
		const fresh=subject(path.join(root,"fresh"));let requested=[];
		fresh.context.installRuntimeArchive=async(_win,entry,url)=>{
			requested.push({entry,url}); fs.mkdirSync(fresh.context.pythonPath,{recursive:true});
			fs.writeFileSync(path.join(fresh.context.pythonPath,"python.exe"),"new interpreter");
		};
		assert.strictEqual(await fresh.api.setupPython(fresh.win),true);
		assert.strictEqual(requested[0].entry,"python");
		fresh.context.downloadResources=async()=>true;
		await fresh.api.setupEnvironment(fresh.win);
		assert.strictEqual(fresh.commands.length,1);
		assert.ok(fresh.commands[0].command.startsWith('"'+path.join(fresh.context.pythonPath,"python.exe")+'" -m venv'));
		assert.strictEqual(fresh.installs[0].pythonPath,path.join(fresh.context.envPythonPath,"python.exe"));
		assert.strictEqual(fresh.installs[0].noCache,false);
		assert.ok(fresh.sends.includes("MENU"));passed++;
		// HTTP failures and request failures reject promptly rather than hanging.
		const {EventEmitter}=require("events");
		const http=subject(path.join(root,"http"));
		http.context.https={get:(_url,onResponse)=>{
			const request=new EventEmitter();request.setTimeout=()=>{};
			queueMicrotask(()=>onResponse({statusCode:404,headers:{},resume:()=>{}}));return request;
		}};
		await assert.rejects(http.api.downloadFile("https://fixture.invalid",path.join(http.home,"download"),http.win),/HTTP 404/);
		http.context.https={get:()=>{
			const request=new EventEmitter();request.setTimeout=()=>{};
			queueMicrotask(()=>request.emit("error",Error("fixture connection failure")));return request;
		}};
		await assert.rejects(http.api.downloadFile("https://fixture.invalid",path.join(http.home,"download"),http.win),/connection failure/);
		passed++;
		// A new log window must not discard queued error lines before its page loads.
		const queued=["fixture startup error"], flushed=[];
		let loading=true;
		http.context.logUiQueue=queued;http.context.logUiFlushTimer=null;
		http.context.LOG_UI_CHUNK_LINES=100;
		http.context.logWin={webContents:{isLoadingMainFrame:()=>loading,send:(_event,text)=>flushed.push(text)}};
		http.api.flushLogUiQueue();assert.strictEqual(queued.length,1);assert.deepStrictEqual(flushed,[]);
		loading=false;http.api.flushLogUiQueue();assert.deepStrictEqual(flushed,["fixture startup error"]);passed++;
		console.log("test-runtime-isolation.js: OK ("+passed+" checks; no network/user runtime modified)");
	} finally { helpers.rmDir(root); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
