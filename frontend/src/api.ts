export type Task = {id:string;goal:string;mode:string;status:string;version:number;result:Record<string,any>|null};
export type Event = {seq:number;type:string;data:Record<string,any>};
export function token(){return sessionStorage.getItem('platform_token')||'';}
export async function api(path:string,options:RequestInit={}){
  const response=await fetch('/api/v1'+path,{...options,headers:{'Content-Type':'application/json',Authorization:`Bearer ${token()}`,...options.headers}});
  if(!response.ok){const error=await response.json().catch(()=>({detail:'服务不可用'}));throw new Error(typeof error.detail==='string'?error.detail:JSON.stringify(error.detail));}
  return response.json();
}
export async function artifact(id:string){
  const response=await fetch('/api/v1/artifacts/'+id,{headers:{Authorization:`Bearer ${token()}`}});
  if(!response.ok)throw new Error('代码读取失败');return response.text();
}
export async function follow(id:string,onEvent:(event:Event)=>void,onStatus:(task:Task)=>void,signal:AbortSignal){
  let cursor=0;
  while(!signal.aborted){
    try{
      const response=await fetch(`/api/v1/tasks/${id}/events?after_seq=${cursor}`,{headers:{Authorization:`Bearer ${token()}`},signal});
      if(!response.ok||!response.body)throw new Error('事件连接失败');
      const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
      while(!signal.aborted){
        const {value,done}=await reader.read();if(done)break;
        buffer+=decoder.decode(value,{stream:true});let boundary;
        while((boundary=buffer.indexOf('\n\n'))>=0){
          const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
          const data=block.split('\n').find(line=>line.startsWith('data: '));
          if(data){const event=JSON.parse(data.slice(6)) as Event;if(event.seq>cursor){cursor=event.seq;onEvent(event);}}
        }
      }
      const state=await api('/tasks/'+id);if(signal.aborted)return;onStatus(state);
      if(['succeeded','failed','cancelled','waiting_input'].includes(state.status))return;
    }catch(error){if(signal.aborted)return;}
    await new Promise(resolve=>setTimeout(resolve,1500));
  }
}
