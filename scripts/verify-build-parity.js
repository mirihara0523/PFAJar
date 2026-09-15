"use strict";
const fs=require("fs"),path=require("path"),crypto=require("crypto");
const args=process.argv.slice(2),json=args.includes("--json"),pkg=args.find(x=>!x.startsWith("--"));
if(!pkg){console.error("Usage: node scripts/verify-build-parity.js <resources-app> [--json]");process.exit(2)}
const root=path.resolve(__dirname,".."),dst=path.resolve(pkg);
const files=["py/czi_extract.py","py/max.py","py/perf_log.py","js/czi_wizard.js","css/theme.css","main.js"];
const hash=p=>crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
const checks=files.map(file=>{const s=path.join(root,file),d=path.join(dst,file);if(!fs.existsSync(s)||!fs.existsSync(d))return{file,exists:false,match:false};return{file,exists:true,match:hash(s)===hash(d),source:hash(s),packaged:hash(d)}});
const report={package:dst,checks,ok:checks.every(x=>x.exists&&x.match)};
if(json)console.log(JSON.stringify(report,null,2));else{console.log("Mason Jar build parity");for(const x of checks)console.log(`  ${x.exists&&x.match?'PASS':'FAIL'} ${x.file}`);console.log("Status: "+(report.ok?'PASS':'FAIL'))}
process.exit(report.ok?0:1);
