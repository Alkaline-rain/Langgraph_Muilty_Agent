chrome.sidePanel.setPanelBehavior({openPanelOnActionClick:true}).catch(()=>{});
chrome.runtime.onInstalled.addListener(()=>{
  chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
  chrome.storage.session.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
});
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  // Only extension-owned pages may request privileged fetches.
  if(sender.id!==chrome.runtime.id||!sender.url?.startsWith(chrome.runtime.getURL('')))return;
  if(message?.type!=='api'||typeof message.path!=='string')return;
  const method=message.method||'GET';
  const allowed= /^\/api\/v1\/(tasks(?:\/[a-zA-Z0-9-]+(?:\/(?:cancel|resume|artifacts))?)?|artifacts\/[a-zA-Z0-9-]+)$/.test(message.path);
  if(!allowed||!['GET','POST'].includes(method)){sendResponse({error:'不允许的请求'});return;}
  void (async()=>{
    try{
      const settings=await chrome.storage.local.get(['endpoint']);const session=await chrome.storage.session.get(['token']);
      const endpoint=String(settings.endpoint||'http://127.0.0.1:8000');
      if(!/^http:\/\/(127\.0\.0\.1|localhost):8000$/.test(endpoint))throw new Error('仅允许本地后端');
      const response=await fetch(endpoint+message.path,{method,headers:{'Content-Type':'application/json',Authorization:'Bearer '+(session.token||''),...(message.key?{'Idempotency-Key':String(message.key).slice(0,128)}:{})},body:method==='POST'?JSON.stringify(message.body||{}):undefined,signal:AbortSignal.timeout(15000)});
      const text=await response.text();
      if(!response.ok)throw new Error(text.slice(0,500));
      sendResponse({data:message.path.startsWith('/api/v1/artifacts/')?text:JSON.parse(text)});
    }catch(e){sendResponse({error:String(e)});}
  })();return true;
});
