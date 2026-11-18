(()=>{
  const scope=window as unknown as {codeAtlasInstalled?:boolean};
  if(scope.codeAtlasInstalled)return;scope.codeAtlasInstalled=true;
  type Block={code:string;language:string;hash:string};
  let blocks:Block[]=[];let timer:number|undefined;let revision=0;
  function hash(text:string){let h=2166136261;for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,16777619);}return(h>>>0).toString(16);}
  function extract(){
    // ChatGPT adapter narrows to assistant messages; generic fallback covers pre/code pages.
    const preferred=document.querySelectorAll('[data-message-author-role="assistant"] pre');
    const elements=preferred.length?preferred:document.querySelectorAll('pre');
    const fresh:Block[]=[];const seen=new Set<string>();
    for(const element of elements){
      if(!element.getClientRects().length)continue;
      const codeElement=element.querySelector('code')||element;
      const code=codeElement.textContent||'';
      if(!code.trim()||code.length>100000||seen.has(code))continue;
      seen.add(code);const cls=codeElement.className||'';
      fresh.push({code,language:/language-([\w]+)/.exec(cls)?.[1]||'unknown',hash:hash(code)});
      if(fresh.length>=30)break;
    }
    if(JSON.stringify(fresh)!==JSON.stringify(blocks)){blocks=fresh;revision++;}
  }
  new MutationObserver(()=>{clearTimeout(timer);timer=window.setTimeout(extract,600);}).observe(document.documentElement,{childList:true,subtree:true,characterData:true});
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if(sender.id!==chrome.runtime.id||message?.type!=='extract')return;
    extract();const selection=window.getSelection()?.toString()||'';
    const selected=selection.trim()? [{code:selection.slice(0,100000),language:'unknown',hash:hash(selection)}]:[];
    reply({blocks:[...selected,...blocks],revision,url:location.origin+location.pathname});
  });extract();
})();
