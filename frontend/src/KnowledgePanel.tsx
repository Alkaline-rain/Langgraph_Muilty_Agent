import React,{useEffect,useState} from 'react';
import {api} from './api';

export function KnowledgePanel({taskId,version}:{taskId:string;version:number}){
  const [runs,setRuns]=useState<any[]>([]);const [error,setError]=useState('');
  useEffect(()=>{let active=true;api(`/tasks/${taskId}/retrievals`).then(rows=>{if(active){setRuns(rows);setError('');}})
    .catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[taskId,version]);
  return <section className="panel"><div className="section-title"><h2>04 / 领域知识证据</h2><span>引用可追溯 · 不代替测试</span></div>
    {error&&<p role="alert">{error}</p>}
    {!runs.length&&<p className="hint">本任务尚未检索领域文档。普通 Python 任务不强制检索。</p>}
    {runs.map(run=><article key={run.id}><p><strong>{run.result.mode}</strong> · {run.result.library_version} · {run.result.duration_ms??'—'} ms</p>
      <p>{run.result.query}</p><small>快照 {run.snapshot_id}</small>
      {run.result.fallback?.length>0&&<p className="hint">降级：{run.result.fallback.join(' / ')}</p>}
      {(run.result.evidence||[]).map((e:any)=><details key={e.id}><summary>[{e.id}] {e.heading||e.title} · {e.score_type}</summary>
        <p>{e.source_uri} · 第 {e.start_line}–{e.end_line} 行</p><small>版本 {e.version_id}</small><pre>{e.text}</pre></details>)}
      <details><summary>检索排序与调用记录</summary><pre>{JSON.stringify({candidates:run.result.candidates,usage:run.result.usage},null,2)}</pre></details>
    </article>)}
  </section>;
}
