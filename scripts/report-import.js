"use strict";
// Usage: node scripts/report-import.js <result-dir> <perf-log> [--json]
const fs=require("fs"),path=require("path"),cp=require("child_process"),a=process.argv.slice(2),json=a.includes("--json"),args=a.filter(x=>x!=="--json");
if(args.length<2){console.error("Usage: node scripts/report-import.js <result-dir> <perf-log> [--json]");process.exit(2)}
const root=path.resolve(args[0]),log=path.resolve(args[1]),dir=path.dirname(__filename);
function run(script,argv){const r=cp.spawnSync(process.execPath,[path.join(dir,script),...argv,"--json"],{encoding:"utf8"});try{return{code:r.status,data:JSON.parse(r.stdout)}}catch(_){return{code:r.status,error:(r.stderr||r.stdout||"").trim()}}}
const result=run("verify-import-result.js",[root]),perf=run("verify-perf-regression.js",[log]);
const report={result,perf,ok:result.code===0&&perf.code===0};
if(json)console.log(JSON.stringify(report,null,2));else{console.log("Mason Jar import report");console.log(`Result: ${result.data?.ok?'PASS':'FAIL'} | TIFF ${result.data?.tiffs??'-'} | dotted ${result.data?.dottedFilenames??'-'}`);console.log(`Performance: ${perf.data?.ok?'PASS':'FAIL'} | max RSS ${perf.data?.maxRssMiB??'-'}MiB`);console.log("Status: "+(report.ok?'PASS':'FAIL'))}process.exit(report.ok?0:1);
