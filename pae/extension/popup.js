'use strict';
const DEFAULT_BASE='http://127.0.0.1:4815';
const ui=id=>document.getElementById(id);
let enabled=true,initialized=false;
function paint(){ui('pae-toggle').textContent=enabled?'标注已开启':'标注已关闭';ui('pae-toggle').setAttribute('aria-pressed',String(enabled));}
async function status(){
 try{
  const response=await fetch(DEFAULT_BASE+'/v1/health',{credentials:'omit',redirect:'error',cache:'no-store'});
  const body=await response.json();if(!response.ok||!body.ok)throw Error('not_ready');
  ui('eng').textContent='本机服务已就绪';ui('eng').dataset.state='ready';
 }catch{ui('eng').textContent='服务未就绪：双击安装目录的 quick_start.bat';ui('eng').dataset.state='error';}
}
ui('pae-toggle').addEventListener('click',event=>{
 if(!event.isTrusted||!initialized)return;
 chrome.storage.local.set({paeEnabled:!enabled},()=>{if(chrome.runtime.lastError){ui('eng').textContent='开关未保存，请重试';return;}enabled=!enabled;paint();});
});
ui('open-sidebar').addEventListener('click',event=>{
 if(!event.isTrusted)return;
 chrome.runtime.sendMessage({type:'PAE_OPEN_SIDEBAR'},result=>{
  if(chrome.runtime.lastError||!result?.ok)ui('eng').textContent=result?.error||'请刷新普通英文网页，再打开侧栏';
  else window.close?.();
 });
});
ui('retry').addEventListener('click',event=>{if(event.isTrusted)status();});
ui('open-persona').href=DEFAULT_BASE+'/panel';
const manager=new URL(DEFAULT_BASE);manager.port=String(Number(manager.port)+1);ui('open-manager').href=manager.origin+'/manage';
chrome.storage.local.get({paeEnabled:true},settings=>{if(chrome.runtime.lastError){ui('eng').textContent='设置读取失败，请重试';return;}enabled=settings.paeEnabled!==false;initialized=true;paint();status();});
