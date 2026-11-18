const field=(id:string)=>document.getElementById(id) as HTMLInputElement;
const display=(id:string,text:string)=>{document.getElementById(id)!.textContent=text;};
let blocks:{code:string;language:string;hash:string}[]=[];let sourceUrl='';let taskId='';let task:any;
async function api(path:string,method='GET',body?:unknown,key?:string){
  const result=await chrome.runtime.sendMessage({type:'api',path,method,body,key});
  if(!result)throw new Error('插件后台未响应，请重新加载扩展');if(result.error)throw new Error(result.error);return result.data;
}
async function guarded(fn:()=>Promise<void>){try{display('error','');await fn();}catch(e){display('error',String(e));}}
document.getElementById('save')!.onclick=()=>guarded(async()=>{
  await chrome.storage.local.set({endpoint:field('endpoint').value});await chrome.storage.session.set({token:field('token').value});display('status','连接信息已保存');
});
document.getElementById('scan')!.onclick=()=>guarded(async()=>{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});if(!tab?.id)throw new Error('没有活动标签页');
  await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']});
  const result=await chrome.tabs.sendMessage(tab.id,{type:'extract'});blocks=result.blocks;sourceUrl=result.url;
  const select=field('blocks') as unknown as HTMLSelectElement;select.replaceChildren();
  blocks.forEach((block,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=`代码 ${index+1} · ${block.language} · ${block.code.length} 字符`;select.append(option);});
  field('code').value=blocks[0]?.code||'';display('source',sourceUrl);if(!blocks.length)display('error','没有找到可见代码块，请选中文本后重试或直接粘贴。');
});
field('blocks').onchange=()=>{field('code').value=blocks[Number(field('blocks').value)]?.code||'';};
document.getElementById('submit')!.onclick=()=>guarded(async()=>{
  const button=document.getElementById('submit') as HTMLButtonElement;button.disabled=true;
  try{
    const mode=field('mode').value;const body={goal:field('goal').value,code:field('code').value,mode,source_type:'extension',source_url:sourceUrl,entrypoint:field('entry').checked?'solve':'',cases:mode==='verified_repair'?JSON.parse(field('cases').value||'[]'):[]};
    const result=await api('/api/v1/tasks','POST',body,crypto.randomUUID());taskId=result.task_id;
    await chrome.storage.local.set({taskId});await refresh();
  }finally{button.disabled=false;}
});
async function refresh(){
  if(!taskId)return;task=await api('/api/v1/tasks/'+taskId);
  const labels:Record<string,string>={queued:'排队中',running:'处理中',waiting_input:'等待补充',succeeded:'处理完成',failed:'失败',cancelled:'已取消'};
  const level=task.result?.result_level;display('status',(labels[task.status]||task.status)+' · '+(level==='verified'?'可信用例通过':level==='diagnostic_only'?'仅诊断结果':'尚未验证'));
  display('result',JSON.stringify(task.result||{},null,2));
  let knowledge=document.getElementById('knowledge-evidence');
  if(!knowledge){knowledge=document.createElement('div');knowledge.id='knowledge-evidence';document.getElementById('result')!.after(knowledge);}
  knowledge.replaceChildren();
  const refs=task.result?.knowledge_refs||[];
  if(refs.length){
    const summary=document.createElement('p');summary.textContent='领域证据：'+refs.map((r:any)=>`${r.heading||r.title}（${r.start_line}–${r.end_line} 行）`).join('；');
    const link=document.createElement('a');link.href='http://127.0.0.1:5173';link.target='_blank';link.rel='noopener';link.textContent='打开工作台查看知识来源';
    knowledge.append(summary,link);
  }
  const artifacts=await api(`/api/v1/tasks/${taskId}/artifacts`);
  if(artifacts.length)field('candidate').value=await api('/api/v1/artifacts/'+artifacts[artifacts.length-1].id);
}
document.getElementById('refresh')!.onclick=()=>guarded(refresh);
document.getElementById('cancel')!.onclick=()=>guarded(async()=>{if(taskId){await api(`/api/v1/tasks/${taskId}/cancel`,'POST');await refresh();}});
document.getElementById('resume')!.onclick=()=>guarded(async()=>{if(task?.status!=='waiting_input')throw new Error('任务未在等待补充');await api(`/api/v1/tasks/${taskId}/resume`,'POST',{version:task.version,interrupt_id:task.result.interrupt_id,entrypoint:'solve',cases:JSON.parse(field('cases').value||'[]')});await refresh();});
document.getElementById('copy')!.onclick=()=>guarded(async()=>{await navigator.clipboard.writeText(field('candidate').value);});
document.getElementById('download')!.onclick=()=>{const url=URL.createObjectURL(new Blob([field('candidate').value],{type:'text/plain'}));const link=document.createElement('a');link.href=url;link.download='candidate.py';link.click();URL.revokeObjectURL(url);};
void guarded(async()=>{const saved=await chrome.storage.local.get(['taskId','endpoint']);const session=await chrome.storage.session.get('token');taskId=String(saved.taskId||'');field('endpoint').value=String(saved.endpoint||'http://127.0.0.1:8000');field('token').value=String(session.token||'');await refresh();});
setInterval(()=>{if(taskId&&!['succeeded','failed','cancelled','waiting_input'].includes(task?.status))void guarded(refresh);},2500);
