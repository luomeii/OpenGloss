'use strict';
const byId=id=>document.getElementById(id);
const actions=['check','update','restart','rollback','stop','export','open-data','uninstall'];
let inFlight=false,privacyDirty=false,managerClosing=false;
function notice(message,error=false){const target=byId('operation-status');target.hidden=false;target.textContent=message;target.classList.toggle('error',error);}
async function api(path,method='GET',body){
 const response=await fetch('/api/'+path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
 let result={};try{result=await response.json()}catch{}
 if(!response.ok)throw Error(result.error==='operation_in_progress'?'另一项操作正在进行，请稍候':result.error==='invalid_settings'?'设置未保存，请检查所选项目':'操作未完成，请重试或查看此安装日志');
 return result;
}
async function refresh(){
 if(managerClosing)return;
 try{
  const state=await api('status');byId('version').textContent='v'+state.version;
  byId('status').textContent=state.message||'本机服务';byId('status-dot').className='status-dot '+(state.engine_running?'ready':'error');
  byId('engine').textContent=state.engine_running?'引擎已运行 · '+state.engine_url:'引擎未运行';byId('panel').href=state.engine_url+'/panel';
  byId('update-status').textContent=state.update_message||'需要时检查 GitHub 正式版本';
  byId('notes').textContent=state.update?'v'+state.update.version+'\n\n'+state.update.notes:'暂无待安装的新版本。';
  const privacy=state.privacy||{};byId('privacy-warning').textContent=privacy.configuration_error?'隐私配置无效：已关闭可读网址保存。请检查后重新保存。':'';
  if(!privacyDirty&&!inFlight){byId('url-storage').value=privacy.url_storage||'full';byId('context-storage').checked=privacy.context_storage!==false;byId('shadow-model').checked=privacy.shadow_model===true;}
  if(!inFlight)byId('autostart').checked=state.autostart===true;
  const link=byId('download-export');link.hidden=!state.export_name;if(state.export_name)link.dataset.name=state.export_name;
  for(const id of [...actions,'save-privacy','autostart'])byId(id).disabled=state.busy||inFlight||(id==='update'&&!state.update)||(id==='rollback'&&!state.can_rollback);
  byId('rollback').title=state.can_rollback?'恢复上一版本及其数据备份':'暂无可回退的上一版本或数据备份';
 }catch{byId('status').textContent='管理服务暂未连接；可从安装目录启动';byId('status-dot').className='status-dot error';}
}
for(const id of ['url-storage','context-storage','shadow-model']){byId(id).addEventListener('input',()=>{privacyDirty=true});byId(id).addEventListener('change',()=>{privacyDirty=true});}
byId('save-privacy').addEventListener('click',async()=>{
 if(inFlight)return;inFlight=true;
 try{await api('privacy','POST',{url_storage:byId('url-storage').value,context_storage:byId('context-storage').checked,shadow_model:byId('shadow-model').checked});privacyDirty=false;notice('隐私设置已保存，正在重启本实例。');}
 catch(error){notice(error.message,true)}finally{inFlight=false;await refresh()}
});
byId('autostart').addEventListener('change',async()=>{
 if(inFlight)return;inFlight=true;
 try{const enabled=byId('autostart').checked;await api('autostart','POST',{enabled});notice(enabled?'已登记本实例开机静默启动。':'已取消本实例开机自启。');}
 catch(error){notice(error.message,true)}finally{inFlight=false;await refresh()}
});
byId('download-export').addEventListener('click',async event=>{
 event.preventDefault();const name=byId('download-export').dataset.name;if(!name)return;
 try{const response=await fetch('/api/export/'+encodeURIComponent(name),{cache:'no-store'});if(!response.ok)throw Error('数据包下载失败，请重新导出');const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download='OpenGloss-data.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);notice('数据包已交给浏览器下载，请私下保存。');}
 catch(error){notice(error.message,true)}
});
for(const action of actions)byId(action).addEventListener('click',async()=>{
 if(inFlight)return;
 if(['update','rollback'].includes(action)&&!confirm(action==='update'?'备份此实例数据并更新？':'恢复上一版本及更新前的数据？'))return;
 let payload={};if(action==='uninstall'){if(!confirm('卸载此实例？默认保留个人数据。'))return;if(byId('remove-data').checked&&!confirm('永久删除此实例的数据库、人格和配置？'))return;payload={confirm:'uninstall-this-instance',remove_data:byId('remove-data').checked};}
 inFlight=true;notice('正在处理，请稍候…');await refresh();
 try{
  const result=await api(action,'POST',payload);
  if(action==='stop'||action==='uninstall'){managerClosing=true;for(const id of [...actions,'save-privacy','autostart'])byId(id).disabled=true;notice(action==='stop'?'此实例已关闭。需要时双击 quick_start.bat 启动。':'卸载已安排，尚未确认完成。结果：'+result.result_file+'；继续入口：'+result.recovery_entry);return;}
  notice(action==='open-data'?'已请求打开此安装的数据目录。':action==='check'?'已开始检查版本，结果显示在版本更新区域。':action==='export'?'正在准备一致性数据包，完成后可点击下载。':'操作已受理，请查看服务状态。');
 }catch(error){notice(error.message,true)}finally{inFlight=false}
 await refresh();
});
refresh();setInterval(refresh,2500);
