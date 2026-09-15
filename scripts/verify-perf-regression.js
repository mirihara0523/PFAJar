"use strict";
// Usage: node scripts/verify-perf-regression.js <log> [--max-rss-mib N] [--json]
const fs=require("fs"),path=require("path"),a=process.argv.slice(2),json=a.includes("--json"),file=a.find(x=>!x.startsWith("--"));
const oi=a.indexOf("--max-rss-mib"),limit=oi>=0?Number(a[oi+1]):4096;
if(!file){console.error("Usage: node scripts/verify-perf-regression.js <log> [--max-rss-mib N] [--json]");process.exit(2)}
const p=path.resolve(file);if(!fs.existsSync(p)){console.error("Log not found: "+p);process.exit(2)}
const text=fs.readFileSync(p,"utf8"),rss=[...text.matchAll(/memory\s+czi\.after_z_stack\s+python_peak=[\d.]+MiB\s+rss=([\d.]+)MiB/gi)].map(x=>+x[1]);
const required=["czi_extract.read","czi_extract.write","czi_extract.preview","czi_extract.max","czi_extract.py.total"],checks={noStacking:!/Stacking\s+\d+\s+planes/i.test(text),rssUnderLimit:rss.length>0&&Math.max(...rss)<=limit,requiredPhases:required.every(x=>text.includes(x)),noFailures:!/ERROR|MAX_PARTIAL_FAILURE|Traceback|failed/i.test(text)};
const report={log:p,maxRssMiB:rss.length?Math.max(...rss):null,rssSamples:rss.length,limitMiB:limit,checks,ok:Object.values(checks).every(Boolean)};
if(json)console.log(JSON.stringify(report,null,2));else{console.log("Mason Jar performance regression");console.log("Log: "+p);console.log(`Max after_z_stack RSS: ${report.maxRssMiB??"unavailable"}MiB (limit ${limit}MiB)`);for(const[k,v]of Object.entries(checks))console.log(`  ${v?'PASS':'FAIL'} ${k}`);console.log("Status: "+(report.ok?'PASS':'FAIL'))}process.exit(report.ok?0:1);
