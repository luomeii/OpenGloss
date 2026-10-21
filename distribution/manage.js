'use strict';
const token=new URLSearchParams(location.hash.slice(1)).get('token')||'';
history.replaceState(null,'',location.pathname);
const controls=['check','update','restart','rollback','stop'];
let inFlight=false;
async function refresh(){
 try{
  const response=await fetch('/api/status',{headers:{'X-OpenGloss-Token':token},cache:'no-store'});
  if(!response.ok)throw new Error('请从此安装的启动入口重新打开管理页。');
  const state=await response.json();
  document.getElementById('version').textContent='v'+state.version;
  document.getElementById('status').textContent=state.message;
  document.getElementById('engine').textContent=(state.engine_running?'已启动：':'未运行：')+state.engine_url;
  document.getElementById('panel').href=state.engine_url+'/panel';
  document.getElementById('config-path').textContent=state.data_directory+'/config.json';
  document.getElementById('notes').textContent=state.update?('v'+state.update.version+'\n\n'+state.update.notes):'暂无待安装的新版本。';
  for(const id of controls)document.getElementById(id).disabled=state.busy||inFlight||(id==='update'&&!state.update);
 }catch(error){document.getElementById('status').textContent=String(error.message||error);}
}
for(const action of controls)document.getElementById(action).addEventListener('click',async()=>{
 if((action==='update'||action==='rollback')&&!confirm(action==='update'?'备份此实例数据并安装新版本？':'恢复上一版本及更新前的数据备份？更新后新增的数据会保存在回退前的备份中。'))return;
 inFlight=true;
 try{
  const response=await fetch('/api/'+action,{method:'POST',headers:{'X-OpenGloss-Token':token,'Content-Type':'application/json'},body:'{}'});
  const body=await response.json();if(!response.ok)throw new Error(body.error||'操作失败');
  if(action==='stop'){document.getElementById('status').textContent='此实例已请求关闭，可以关闭这个页面。';return;}
 }catch(error){document.getElementById('status').textContent=String(error.message||error);}
 finally{inFlight=false;}
 await refresh();
});
refresh();setInterval(refresh,2500);
