import React,{useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import Editor,{DiffEditor,loader} from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import {api,artifact,follow,token,type Task,type Event} from './api';
import './style.css';
import {KnowledgePanel} from './KnowledgePanel';
(self as any).MonacoEnvironment={getWorker:()=>new EditorWorker()};
loader.config({monaco});
const labels:Record<string,string>={queued:'排队中',running:'处理中',waiting_input:'等待补充',succeeded:'处理完成',failed:'处理失败',cancelled:'已取消'};
const starter="def solve(payload):\n    return payload['a'] - payload['b']\n";
const sampleCases='[{"name":"positive","input":{"a":2,"b":3},"expected":5}]';

function App(){
  const [auth,setAuth]=useState(token());const [connected,setConnected]=useState(false);
  const [view,setView]=useState('tasks');const [tasks,setTasks]=useState<Task[]>([]);const [selected,setSelected]=useState<Task|null>(null);
  const [goal,setGoal]=useState('实现 solve(payload)，返回 a 与 b 的和');const [code,setCode]=useState(starter);
  const [mode,setMode]=useState('diagnose');const [cases,setCases]=useState(sampleCases);const [entry,setEntry]=useState(false);
  const [events,setEvents]=useState<Event[]>([]);const [original,setOriginal]=useState('');const [candidate,setCandidate]=useState('');
  const [skills,setSkills]=useState<any[]>([]);const [memories,setMemories]=useState<any[]>([]);
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [streamVersion,setStreamVersion]=useState(0);
  async function refresh(){setTasks(await api('/tasks'));}
  async function connect(){try{sessionStorage.setItem('platform_token',auth);await refresh();setSkills(await api('/skills'));setConnected(true);setError('');}catch(e){setError(String(e));}}
  useEffect(()=>{if(token())void connect();},[]);
  useEffect(()=>{
    if(!selected)return;setEvents([]);setOriginal('');setCandidate('');const control=new AbortController();
    void follow(selected.id,event=>setEvents(current=>[...current,event]),task=>{setSelected(task);void refresh();},control.signal);
    return ()=>control.abort();
  },[selected?.id,streamVersion]);
  useEffect(()=>{
    if(!selected)return;let active=true;
    api('/tasks/'+selected.id+'/artifacts').then(async rows=>{
      if(!rows.length)return;
      const first=await artifact(rows[0].id);const last=await artifact(rows[rows.length-1].id);
      if(active){setOriginal(first);setCandidate(last);}
    }).catch(e=>setError(String(e)));return()=>{active=false;};
  },[selected?.id,selected?.status,events.length]);
  async function submit(){
    setBusy(true);setError('');try{
      const body={goal,code,mode,source_type:'web',entrypoint:entry?'solve':'',cases:mode==='verified_repair'?JSON.parse(cases):[]};
      const response=await api('/tasks',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(body)});
      await refresh();setSelected(await api('/tasks/'+response.task_id));
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function resume(){if(!selected)return;try{await api(`/tasks/${selected.id}/resume`,{method:'POST',body:JSON.stringify({version:selected.version,interrupt_id:selected.result?.interrupt_id,entrypoint:'solve',cases:JSON.parse(cases)})});setSelected(await api('/tasks/'+selected.id));setStreamVersion(v=>v+1);}catch(e){setError(String(e));}}
  async function cancel(){if(!selected)return;try{await api(`/tasks/${selected.id}/cancel`,{method:'POST'});setSelected(await api('/tasks/'+selected.id));}catch(e){setError(String(e));}}
  const plan=selected?.result?.plan||[...events].reverse().find(e=>e.type==='plan.updated')?.data.steps||[];
  return <div className="app"><aside><div className="brand"><span className="logo">⌘</span> CODE ATLAS</div><p className="eyebrow">代码验证与修复</p>
    <nav>{[['tasks','工作台'],['skills','能力目录'],['memories','经验记忆']].map(([id,name])=><button key={id} className={view===id?'active':''} onClick={()=>{setView(id);if(id==='memories')api('/memories').then(setMemories).catch(e=>setError(String(e)));}}>{name}</button>)}</nav>
    <div className="sidebar-bottom"><span className={'dot '+(connected?'online':'')}/>{connected?'本地服务已连接':'等待连接'}<small>单文件 Python · 隔离执行</small></div></aside>
    <main><header><div><p className="eyebrow">AGENT WORKSPACE / 02</p><h1>{view==='tasks'?'让每次修复都有依据。':view==='skills'?'可组合的修复能力':'可追溯的经验记忆'}</h1></div><span className="pill">LOCAL WORKSPACE</span></header>
    {!connected&&<section className="connect"><label>平台访问令牌<input aria-label="平台访问令牌" type="password" value={auth} onChange={e=>setAuth(e.target.value)} placeholder="从本地 .platform.env 获取"/></label><button onClick={connect}>连接服务</button><p>令牌仅保存在当前标签页会话中。</p></section>}
    {error&&<div role="alert" className="error">{error}<button onClick={()=>setError('')}>关闭</button></div>}
    {view==='tasks'&&<><div className="workspace"><section className="panel"><div className="section-title"><h2>01 / 提交代码</h2><span>先定义目标，再验证结果</span></div>
      <label>任务目标<textarea aria-label="任务目标" value={goal} onChange={e=>setGoal(e.target.value)} rows={2}/></label>
      <div className="mode-switch"><button className={mode==='diagnose'?'chosen':''} onClick={()=>setMode('diagnose')}>静态诊断</button><button className={mode==='verified_repair'?'chosen':''} onClick={()=>setMode('verified_repair')}>可信验收与修复</button></div>
      <p className="hint">{mode==='diagnose'?'检查语法和规则；诊断完成不代表功能正确。':'使用你提供的可信用例验收，模型不会收到预期答案。'}</p>
      <div className="editor"><Editor height="260px" language="python" value={code} onChange={value=>setCode(value||'')} options={{minimap:{enabled:false},fontSize:13,scrollBeyondLastLine:false}}/></div>
      {mode==='verified_repair'&&<><label className="checkbox"><input type="checkbox" checked={entry} onChange={e=>setEntry(e.target.checked)}/>代码实现 solve(payload) 入口</label><label>可信用例（JSON）<textarea aria-label="可信用例" value={cases} onChange={e=>setCases(e.target.value)} rows={4}/></label></>}
      <button className="primary full" disabled={!connected||busy} onClick={submit}>{busy?'提交中…':'创建验证任务 →'}</button></section>
      <section className="panel"><div className="section-title"><h2>02 / 任务记录</h2><button onClick={()=>refresh().catch(e=>setError(String(e)))}>刷新</button></div>
      {!tasks.length&&<div className="empty"><strong>还没有任务</strong><p>提交一段代码，查看 Agent 的计划与验证证据。</p></div>}
      <div className="task-list">{tasks.map(task=><button className={'task '+(selected?.id===task.id?'selected':'')} key={task.id} onClick={()=>setSelected(task)}><span>{task.goal}</span><small>{labels[task.status]||task.status}</small></button>)}</div>
      {selected&&<><div className="result-line"><span className="pill">{labels[selected.status]}</span><strong>{selected.result?.result_level==='verified'?'可信用例通过':selected.result?.result_level==='diagnostic_only'?'仅诊断结果':'尚未验证'}</strong><button onClick={cancel} disabled={['succeeded','failed','cancelled'].includes(selected.status)}>取消任务</button></div>
      <div className="stats"><div><strong>{selected.result?.llm_calls??'—'}</strong><span>模型调用</span></div><div><strong>{selected.result?.budget_tokens??'—'}</strong><span>预算扣减 · 非账单</span></div><div><strong>{selected.result?.plan_version??'—'}</strong><span>计划版本</span></div></div>
      {selected.status==='waiting_input'&&<div className="input-needed"><p>{selected.result?.pending_input?.message}</p><textarea aria-label="补充可信用例" value={cases} onChange={e=>setCases(e.target.value)}/><button onClick={resume}>确认 solve 入口并继续</button></div>}
      <ol className="plan">{plan.map((step:any)=><li key={step.step_id}><strong>{step.objective}</strong><small>{step.skill_id}</small></li>)}</ol>
      <details><summary>执行事件与证据（{events.length}）</summary>{events.map(event=><div className="event" key={event.seq}><b>{event.type}</b><pre>{JSON.stringify(event.data,null,2)}</pre></div>)}</details></>}
      </section></div>
      {selected&&<section className="panel diff"><div className="section-title"><h2>03 / 代码版本对比</h2><button disabled={!candidate} onClick={()=>{const url=URL.createObjectURL(new Blob([candidate],{type:'text/plain'}));const a=document.createElement('a');a.href=url;a.download='candidate.py';a.click();URL.revokeObjectURL(url);}}>下载候选代码</button></div><p className="hint">左：原始版本　右：最新候选。代码仍应在沙箱中执行。</p><DiffEditor height="330px" language="python" original={original} modified={candidate} options={{readOnly:true,minimap:{enabled:false},renderSideBySide:true}}/></section>}</>}
    {view==='tasks'&&selected&&<KnowledgePanel taskId={selected.id} version={events.length}/>}
    {view==='skills'&&<div className="cards">{skills.map(skill=><section className="panel" key={skill.id}><span className="pill">v{skill.version}</span><h2>{skill.description}</h2><code>{skill.id}</code><p>{skill.tools.join(' · ')}</p><small>按需加载 · 受控工具权限</small></section>)}</div>}
    {view==='memories'&&<><p className="hint">只收录可信验收通过的经验。记录可删除，检索时按执行环境过滤。</p><div className="cards">{memories.map(memory=><section className="panel" key={memory.id}><span className="pill">{memory.status}</span><p>{memory.text}</p><small>来源任务 {memory.task_id}</small><button onClick={async()=>{try{await api('/memories/'+memory.id,{method:'DELETE'});setMemories(await api('/memories'));}catch(e){setError(String(e));}}}>删除经验</button></section>)}</div>{!memories.length&&<div className="empty">尚无已验证经验</div>}</>}
    <footer>CODE ATLAS <span>计划可检查 · 执行有边界 · 结果有证据</span></footer></main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
