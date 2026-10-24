'use strict';
const token=new URLSearchParams(location.hash.slice(1)).get('token')||'';
history.replaceState(null,'',location.pathname);
const actions=['check','update','restart','rollback','stop','export','open-data','uninstall'];
let inFlight=false;
let managerClosing=false;
let privacyDirty=false;
const byId=id=>document.getElementById(id);
for(const id of ['url-storage','context-storage','shadow-model']){
 byId(id).addEventListener('input',()=>{privacyDirty=true});
 byId(id).addEventListener('change',()=>{privacyDirty=true});
}
async function api(path,method='GET',body){
 const response=await fetch('/api/'+path,{method,headers:{'X-OpenGloss-Token':token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store'});
 let result={};try{result=await response.json()}catch{}
 if(!response.ok)throw new Error(result.error||'操作失败');
 return result;
}
async function refresh(){
 if(managerClosing)return;
 try{
  const state=await api('status');
  byId('version').textContent='v'+state.version;
  byId('status').textContent=state.message;
  byId('engine').textContent=(state.engine_running?'已启动：':'未运行：')+state.engine_url;
  byId('engine-token').textContent=state.engine_token;
  byId('panel').href=state.engine_url+'/panel#token='+encodeURIComponent(state.engine_token);
  byId('update-status').textContent=state.update_message||'暂无更新状态';
  byId('notes').textContent=state.update?('v'+state.update.version+'\n\n'+state.update.notes):'暂无待安装的新版本。';
  const privacy=state.privacy||{};
  byId('privacy-warning').textContent=privacy.configuration_error?'隐私配置无效：已关闭可读网址保存。请检查配置，或确认后重新保存。':'';
  if(!privacyDirty&&!inFlight){
   byId('url-storage').value=privacy.url_storage||'full';
   byId('context-storage').checked=privacy.context_storage!==false;
   byId('shadow-model').checked=privacy.shadow_model===true;
  }
  byId('autostart').checked=state.autostart===true;
  const link=byId('download-export');
  link.hidden=!state.export_name;
  if(state.export_name)link.dataset.name=state.export_name;
  for(const id of actions)byId(id).disabled=state.busy||inFlight||(id==='update'&&!state.update);
 }catch(error){byId('status').textContent=String(error.message||error)}
}
byId('copy-token').addEventListener('click',async()=>{
 try{await navigator.clipboard.writeText(byId('engine-token').textContent);byId('status').textContent='访问口令已复制。'}
 catch{byId('status').textContent='复制失败，请手动选择口令。'}
});
byId('download-export').addEventListener('click',async event=>{
 event.preventDefault();
 const name=byId('download-export').dataset.name;
 if(!name)return;
 try{
  const response=await fetch('/api/export/'+encodeURIComponent(name),{headers:{'X-OpenGloss-Token':token},cache:'no-store'});
  if(!response.ok)throw new Error('数据包下载失败');
  const url=URL.createObjectURL(await response.blob());
  const link=document.createElement('a');link.href=url;link.download='OpenGloss-data.zip';link.click();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
 }catch(error){byId('status').textContent=String(error.message||error)}
});
byId('save-privacy').addEventListener('click',async()=>{
 inFlight=true;
 try{await api('privacy','POST',{url_storage:byId('url-storage').value,context_storage:byId('context-storage').checked,shadow_model:byId('shadow-model').checked});privacyDirty=false;byId('status').textContent='设置已保存，正在重启引擎。'}
 catch(error){byId('status').textContent=String(error.message||error)}
 finally{inFlight=false;await refresh()}
});
byId('autostart').addEventListener('change',async()=>{
 inFlight=true;
 try{await api('autostart','POST',{enabled:byId('autostart').checked})}
 catch(error){byId('status').textContent=String(error.message||error)}
 finally{inFlight=false;await refresh()}
});
for(const action of actions)byId(action).addEventListener('click',async()=>{
 if((action==='update'||action==='rollback')&&!confirm(action==='update'?'备份此实例数据并安装新版本？':'恢复上一版本及更新前的数据备份？'))return;
 let payload={};
 if(action==='uninstall'){
  if(!confirm('确定卸载此安装实例？默认保留个人数据。'))return;
  if(byId('remove-data').checked&&!confirm('再次确认：永久删除此实例的个人数据库、配置、人格和密钥？'))return;
  payload={confirm:'uninstall-this-instance',remove_data:byId('remove-data').checked};
 }
 inFlight=true;
 try{
  const result=await api(action,'POST',payload);
  if(action==='stop'||action==='uninstall'){managerClosing=true;for(const id of actions)byId(id).disabled=true;byId('status').textContent=action==='stop'?'此实例已请求关闭。':'卸载已安排，尚未确认完成。结果：'+result.result_file+'；中断后继续入口：'+result.recovery_entry;return}
 }catch(error){byId('status').textContent=String(error.message||error)}
 finally{inFlight=false}
 await refresh();
});
refresh();setInterval(refresh,2500);
