"use strict";
// Validate an import result directory without modifying it.
// Usage: node scripts/verify-import-result.js <dir> [--expected-tiffs N] [--expected-dotted N] [--min-bytes N] [--json]
const fs=require("fs"),path=require("path"),args=process.argv.slice(2),json=args.includes("--json"),root=args[0];
function opt(name,def){const i=args.indexOf(name);return i<0?def:Number(args[i+1])}
if(!root||root==='--help'||root==='-h'){console.error("Usage: node scripts/verify-import-result.js <dir> [--expected-tiffs N] [--expected-dotted N] [--min-bytes N] [--json]");process.exit(root?0:2)}
const dir=path.resolve(root);if(!fs.existsSync(dir)||!fs.statSync(dir).isDirectory()){console.error("Result directory not found: "+dir);process.exit(2)}
const files=[];function walk(p){for(const e of fs.readdirSync(p,{withFileTypes:true})){const q=path.join(p,e.name);e.isDirectory()?walk(q):files.push(q)}}walk(dir);
const tiffs=files.filter(f=>/\.tiff?$/i.test(f)),names=tiffs.map(f=>path.basename(f)),dotted=names.filter(n=>n.slice(0,-5).includes('.')),sizes=tiffs.map(f=>fs.statSync(f).size),zero=tiffs.filter(f=>fs.statSync(f).size===0),minBytes=opt('--min-bytes',1),manifest=files.filter(f=>/manifest|result/i.test(path.basename(f))&&/\.json$/i.test(f));
const expectedTiffs=opt('--expected-tiffs',null),expectedDotted=opt('--expected-dotted',null),checks={hasTiffs:tiffs.length>0,noZeroByteTiffs:zero.length===0,minSize:sizes.every(n=>n>=minBytes),expectedTiffs:expectedTiffs==null||tiffs.length===expectedTiffs,expectedDotted:expectedDotted==null||dotted.length===expectedDotted};
const report={directory:dir,files:files.length,tiffs:tiffs.length,zeroByteTiffs:zero.length,dottedFilenames:dotted.length,manifests:manifest.length,minTiffBytes:minBytes,expectedTiffs,expectedDotted,checks,ok:Object.values(checks).every(Boolean)};
if(json)console.log(JSON.stringify(report,null,2));else{console.log("Import result verification\nDirectory: "+dir);console.log(`Files: ${files.length} | TIFF: ${tiffs.length} | Zero-byte TIFF: ${zero.length}`);console.log(`Dotted TIFF names: ${dotted.length} | JSON manifests: ${manifest.length}`);for(const[k,v]of Object.entries(checks))console.log(`  ${v?'PASS':'FAIL'} ${k}`);console.log("Status: "+(report.ok?'PASS':'FAIL'))}process.exit(report.ok?0:1);
