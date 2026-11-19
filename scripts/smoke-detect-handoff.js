"use strict";
const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),temporary=fs.mkdtempSync(path.join(root,'../test-temp/detect-handoff-'));
app.setPath('userData',path.join(temporary,'profile'));app.setPath('crashDumps',path.join(temporary,'crash'));
const input=path.join(temporary,'starters'),output=path.join(temporary,'predictions');fs.mkdirSync(input);fs.mkdirSync(output);fs.writeFileSync(path.join(input,'dotted.section.tif'),'fixture');
const preload=path.join(temporary,'preload.js');
fs.writeFileSync(preload,`
 const p=require(${JSON.stringify(path.join(root,'js','project'))});p.tryRestoreActiveProject=()=>false;p.isActive=()=>false;
 const w=require(${JSON.stringify(path.join(root,'js','workspace'))});w.applyPreset=()=>{};w.bindPathPicker=()=>{};
 require(${JSON.stringify(path.join(root,'js','pipeline_gate'))}).assertPipelineAccess=()=>true;
 require(${JSON.stringify(path.join(root,'js','project_index_busy'))}).populatePage=fn=>Promise.resolve().then(fn);
`);
let viewer,args=null;ipcMain.on('runDetection',(_event,data)=>{args=data;});
async function waitFor(expression){for(let i=0;i<100;i++){if(await viewer.webContents.executeJavaScript(expression))return;await new Promise(resolve=>setTimeout(resolve,40));}throw new Error('Timed out: '+expression);}
app.whenReady().then(async()=>{
 viewer=new BrowserWindow({show:false,webPreferences:{nodeIntegration:true,contextIsolation:false,preload}});
 viewer.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
 const settings={version:1,indir:input,outdir:output,model:path.join(temporary,'custom.pt'),bundleRoot:'',signalBranch:'starters',method:'custom',tile:640,confidence:1,area:0,eccentricity:0,intensity:70};
 await viewer.loadFile(path.join(root,'pages/detect_wizard.html'),{query:{viewerSettings:JSON.stringify(settings)}});
 await waitFor("document.getElementById('viewerTransferNotice').textContent.startsWith('Settings copied')");
 const form=await viewer.webContents.executeJavaScript("Object.fromEntries(['indir','outdir','model','tile','confidence','area','eccentricity','intensityMin'].map(id=>[id,document.getElementById(id).value]))");
 assert.strictEqual(form.indir,input);assert.strictEqual(form.outdir,output);assert.strictEqual(form.model,settings.model);assert.strictEqual(form.confidence,'1');assert.strictEqual(form.area,'0');assert.strictEqual(form.eccentricity,'0');assert.strictEqual(form.intensityMin,'70');assert.strictEqual(args,null,'must not auto-run');
 assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('multichannel').checked"),false);
 await viewer.webContents.executeJavaScript("document.getElementById('step1Next').click()");
 await waitFor("document.getElementById('processMessage').textContent.includes('Run folder') || !document.getElementById('step2').classList.contains('d-none')");
 assert.ok(args);assert.strictEqual(args[0],input);assert.strictEqual(args[2],1);assert.strictEqual(args[3],640);assert.strictEqual(args[4],settings.model);assert.strictEqual(args[5],false);assert.strictEqual(args[7],0);assert.strictEqual(args[8],0);assert.strictEqual(args[11],70);
 assert.ok(path.resolve(args[1]).startsWith(path.resolve(output)),'new result must stay under selected output base');

 // Project defaults and dataset initialization must not clobber the transfer.
 fs.appendFileSync(preload, `
 const fixtureRoot=${JSON.stringify(temporary)};
 p.isActive=()=>true;p.getBundleRoot=()=>fixtureRoot;p.getProject=()=>({processing:{},settings:{}});
 w.applyPreset=()=>{document.getElementById('indir').value='preset-input';document.getElementById('outdir').value='preset-output';};
 require(${JSON.stringify(path.join(root,'js','max_datasets'))}).listDatasetsForBranch=()=>[{rel:'starters/run',abs:${JSON.stringify(input)},label:'fixture dataset'}];
 require(${JSON.stringify(path.join(root,'js','max_dataset_picker'))}).wireMaxDatasetPicker=opts=>{
   const branch=document.getElementById(opts.branchSelectId),dataset=document.getElementById(opts.datasetSelectId);
   for(const name of ['somata','starters']){const option=document.createElement('option');option.value=name;option.textContent=name;branch.appendChild(option);}
   const refresh=()=>{dataset.innerHTML='<option value="starters/run">fixture dataset</option>';opts.indirInput.value='picker-default';};refresh();return{refresh};
 };
 `);
 args=null;
 await viewer.loadFile(path.join(root,'pages/detect_wizard.html'),{query:{viewerSettings:JSON.stringify({...settings,bundleRoot:temporary})}});
 await waitFor("document.getElementById('viewerTransferNotice').textContent.startsWith('Settings copied')");
 assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('indir').value"),input);
 assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('outdir').value"),output);
 assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('detectSignalBranch').value"),'starters');
 assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('detectMaxDataset').value"),'starters/run');
 assert.strictEqual(args,null);
 await viewer.loadFile(path.join(root,'pages/detect_wizard.html'),{query:{viewerSettings:JSON.stringify({...settings,bundleRoot:path.join(temporary,'other-project')})}});
 await waitFor("document.getElementById('viewerTransferNotice').classList.contains('alert-danger')");
 assert.strictEqual(await viewer.webContents.executeJavaScript("document.getElementById('step1Next').disabled"),true);
 assert.strictEqual(args,null);
 console.log('Detection wizard handoff: exact folder/model/filter values, zero/end-point preservation, no auto-run, actual Run IPC arguments and new output passed.');viewer.destroy();app.exit(0);
}).catch(error=>{console.error(error);app.exit(1)});
