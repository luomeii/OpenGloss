'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const sourceRoot=process.argv[2]||require('path').resolve(__dirname,'../..');
const defaultBase=process.argv[3]||'http://127.0.0.1:4815';
const source=fs.readFileSync(sourceRoot+'/pae/extension/popup.js','utf8');
async function trial(address,throws=false){
 const calls=[],pending=[];const elements={};
 for(const id of ['pae-toggle','state','eng'])elements[id]={textContent:'',className:'',addEventListener(){}};
 const chrome={storage:{local:{get(defaults,cb){if(throws&&defaults.paeBase)throw Error('synthetic storage unavailable');if(defaults.paeBase)pending.push(cb);else cb(defaults)},set(){}}}};
 vm.runInNewContext(source,{chrome,document:{getElementById:id=>elements[id]},fetch:async url=>{calls.push(url);return {ok:true,status:200}}});
 if(!throws){assert.equal(calls.length,0,'health must wait until configured address is known');assert.equal(pending.length,1);pending[0]({paeBase:address});}
 await Promise.resolve();await Promise.resolve();
 assert.deepStrictEqual(calls,[(address||defaultBase).replace(/\/+$/,'')+'/v1/health']);
 assert.equal(elements.eng.textContent,'running');
}
(async()=>{
 await trial('http://127.0.0.1:54941/');
 await trial('');
 await trial(defaultBase,true);
 process.stdout.write(JSON.stringify({ok:true,scenarios:3,real_browser:false})+'\n');
})().catch(e=>{process.stderr.write(String(e.stack)+'\n');process.exitCode=1});
