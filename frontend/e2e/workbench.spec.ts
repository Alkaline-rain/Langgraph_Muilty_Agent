import {test,expect,chromium} from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs/promises';
const token='e2e-fixed-fixture-token';

test('workbench submits code, streams real API events and shows Docker repair evidence',async({page})=>{
  await page.goto('/');
  await page.getByLabel('平台访问令牌').fill(token);
  await page.getByRole('button',{name:'连接服务',exact:true}).click();
  await page.getByRole('button',{name:'可信验收与修复',exact:true}).click();
  await page.getByLabel('代码实现 solve(payload) 入口').check();
  await page.getByRole('button',{name:'创建验证任务 →'}).click();
  await expect(page.getByText('可信用例通过',{exact:true})).toBeVisible({timeout:60000});
  await expect(page.getByRole('button',{name:'下载候选代码'})).toBeEnabled();
  await page.getByText(/执行事件与证据/).click();
  await expect(page.getByText('plan.updated',{exact:true}).first()).toBeVisible();
  await page.screenshot({path:'../test_results/platform-workbench.png',fullPage:true});
  await page.getByRole('button',{name:'能力目录',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Python 静态诊断'})).toBeVisible();
});

test('RAG workbench shows persisted source evidence after reload',async({page})=>{
  await page.goto('/');
  await page.getByLabel('平台访问令牌').fill(token);
  await page.getByRole('button',{name:'连接服务',exact:true}).click();
  await page.getByLabel('任务目标',{exact:true}).fill('[RAG] RunnableLambda invoke，返回 a 与 b 之和');
  await page.getByRole('button',{name:'可信验收与修复',exact:true}).click();
  await page.getByLabel('代码实现 solve(payload) 入口').check();
  await page.getByRole('button',{name:'创建验证任务 →'}).click();
  await expect(page.getByText('可信用例通过',{exact:true})).toBeVisible({timeout:60000});
  await expect(page.getByRole('heading',{name:'04 / 领域知识证据'})).toBeVisible();
  await expect(page.locator('summary').filter({hasText:'K-'}).first()).toBeVisible();
  await page.reload();
  await page.getByRole('button',{name:/\[RAG\] RunnableLambda invoke/}).first().click();
  await expect(page.locator('summary').filter({hasText:'K-'}).first()).toBeVisible();
  await page.screenshot({path:'../test_results/rag-workbench.png',fullPage:true});
});

test('content extraction adapter handles assistant blocks, dynamic DOM and no duplicate snapshots',async({page})=>{
  await page.goto('/');
  await page.setContent('<main><div data-message-author-role="assistant"><pre><code class="language-python">def solve(payload): return 1</code></pre></div><pre>not assistant</pre></main>');
  await page.evaluate(()=>{(window as any).chrome={runtime:{id:'fixture',onMessage:{addListener:(fn:any)=>{(window as any).extract=fn;}}}};});
  await page.addScriptTag({path:path.resolve('../extension/dist/content.js')});
  const extract=()=>page.evaluate(()=>new Promise<any>(resolve=>(window as any).extract({type:'extract'},{id:'fixture'},resolve)));
  const first=await extract();expect(first.blocks).toHaveLength(1);expect(first.blocks[0].language).toBe('python');
  const same=await extract();expect(same.revision).toBe(first.revision);
  await page.locator('code').evaluate(node=>{node.textContent='def solve(payload): return 2';});
  const changed=await extract();expect(changed.revision).toBeGreaterThan(first.revision);
});

test('real MV3 panel sends selected snapshot to backend and survives reopening',async()=>{
  const extension=path.resolve('../extension/dist');
  const profile=await fs.mkdtemp(path.resolve('../.tmp/extension-e2e-'));
  const context=await chromium.launchPersistentContext(profile,{channel:'msedge',headless:true,ignoreDefaultArgs:['--disable-extensions'],args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
  try{
    let [worker]=context.serviceWorkers();if(!worker)worker=await context.waitForEvent('serviceworker');
    const id=new URL(worker.url()).host;const panel=await context.newPage();
    await panel.goto(`chrome-extension://${id}/panel.html`);
    await panel.locator('summary').click();await panel.locator('#token').fill(token);await panel.locator('#save').click();
    await panel.locator('#code').fill("def solve(payload):\n    return payload['a'] - payload['b']");
    await panel.locator('#goal').fill('返回 a 与 b 之和');await panel.locator('#mode').selectOption('verified_repair');
    await panel.locator('#entry').check();await panel.locator('#cases').fill('[{"name":"add","input":{"a":2,"b":3},"expected":5}]');
    await panel.locator('#submit').click();await expect(panel.locator('#status')).toContainText('可信用例通过',{timeout:60000});
    await panel.reload();await expect(panel.locator('#status')).toContainText('可信用例通过');
    await expect(panel.locator('#candidate')).toHaveValue(/\+/);
    await panel.screenshot({path:'../test_results/platform-extension.png',fullPage:true});
  }finally{await context.close();}
});
