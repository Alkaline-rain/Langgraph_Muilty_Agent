# 领域 RAG：实现、启动和验证

日期：2026-10-20。此文描述实际代码；原设计见 [升级大纲](领域代码修复RAG升级大纲.md)。

## 已实现的链路

本地 Markdown 入库 → 代码围栏感知的父子分块 → 不可变知识快照 → child BM25 / Dense → RRF → parent 回填去重 → 可选 rerank → 有预算的来源证据 → 修复角色 → Docker 可信验收。

顶层 LangGraph 仍是 intake / plan / execute_skill。新增 `retrieve_domain_docs` Skill，由 Planner 在需要领域知识时选择。固定执行协议始终提供给 Coder，旧的 LCEL 固定文本不再注入 v2 修复上下文；旧版 CLI 基线保留。

知识文档表和 `repair_episodes` 经验表分离。`knowledge_refs` 表示本任务获得的领域文档，`memories` 表示历史任务经验；都不能替代可信测试。没有新增缓存服务、消息队列或 MCP。

## 本机初始化

项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose -f deploy/compose.yaml up -d --wait
.\.venv\Scripts\python.exe -m backend.setup
# 默认只预览，不修改数据库、不调用 embedding
.\.venv\Scripts\python.exe -m code_assistant.knowledge.ingest
# 发布本地 BM25 知识快照
.\.venv\Scripts\python.exe -m code_assistant.knowledge.ingest --apply
powershell -File scripts/start_platform.ps1
```

本次已经在本机完成新迁移及本地笔记入库。不要直接调用 metadata.create_all 代替 PostgreSQL 迁移，否则会遗漏向量维度约束触发器。SQLite 的 initialize 仅用于开发 / 测试。

首批语料：`knowledge_sources/lcel/lcel.md`，20 个主题 section 加引言，共 21 个 parent / child 块。来源是项目维护的离线笔记与本地接口契约验证，**不是完整官方文档库**。精确兼容版本目前为镜像内 `langchain-core==1.6.6`；任务执行时通过受限固定命令探测候选镜像版本，不能用宿主 SDK 版本替代。

更新文档后重新入库会创建新快照；同内容与配置重复导入不重复写入。运行中的任务锁定旧快照，历史版本保留。源文件必须处于 manifest 所在目录内。

## 外部 embedding / rerank：可选且默认关闭

在本地 `.platform.env` 配置以下独立参数，不要提交密钥：

```dotenv
KNOWLEDGE_EMBEDDING_URL=<完整 embeddings 端点>
KNOWLEDGE_EMBEDDING_KEY=<服务密钥>
KNOWLEDGE_EMBEDDING_MODEL=<已确认可用的模型>
KNOWLEDGE_EMBEDDING_DIMENSIONS=<该模型实际输出维度>
KNOWLEDGE_RERANK_URL=<完整 rerank 端点>
KNOWLEDGE_RERANK_KEY=<服务密钥>
KNOWLEDGE_RERANK_MODEL=<已确认可用的模型>
```

外部向量入库显式增加 `--allow-external --max-calls 200`。每个 child 一次 embedding，执行前显示上限；返回失败不会发布半份快照。配置维度必须与实际输出一致。

在线请求的 knowledge 字段：

```json
{
  "mode": "rerank",
  "max_retrievals": 2,
  "context_chars": 6000,
  "allow_external": false
}
```

默认 `false`，即使机器上配置了服务也不自动调用。需要真实 Dense / rerank 时，调用方必须显式设为 true；此开关也控制任务内已有经验 embedding 的外部调用。知识库必须先使用匹配的 embedding profile 入库，否则返回 profile mismatch 并降级。

embedding 输入为文档片段或净化查询，rerank 输入为查询及候选父块。它们独立于宿主生成模型，可能产生额外费用。每轮知识检索最多一次 embedding、一次 rerank；默认最多两轮，经验 embedding 每次 MemoryStore 生命周期最多两次。服务返回 usage 才记实测值，否则 null；未将不同服务的 Token 合并成虚假的货币账单。

HTTP 请求无自动重试，有超时与响应大小限制。Dense 不可用降级 BM25，rerank 失败降级当前召回排序，事件与报告保留实际 mode/fallback。没有足够外部配置时，不应把输出称作已运行完整混合检索。

## 数据模型与设计取舍

- SQLAlchemy + Alembic 新增 documents / versions / chunks / profiles / embeddings / snapshots / snapshot_members / active / retrieval_runs。
- 文档标题、来源和归属在 document 层，版本记录内容 hash、支持版本、分块器版本；block 保存原始规范化行号与 hash。
- 大纲建议固定 `vector(d)` 列。本实现改用 **原生 pgvector 的 vector 列 + profile 维度 + 数据库触发器**：一张表兼容多个隔离 profile，插入 / 更新必须满足该 profile 维度。查询按 profile 和快照过滤；不是旧经验库的 JSON 转 vector。暂未建立 ANN 索引。
- 未建立 GIN 全文索引；文档稀疏召回使用 `rank_bm25`。进程内临时构建索引，未增加检索结果缓存。小型本地知识库适用，大规模性能未验证。
- 父块以最高 child RRF 分数聚合，避免长文档因 child 数量多而获额外优势；语义检索与重排收益仍需实验。
- 超长不可安全拆分的原子块明确拒绝，尚未实现任意长函数的 AST 分段；不会静默截断围栏。
- Coder 上下文和前端保存的是实际注入的证据引用。**尚未要求模型输出并核对其“实际使用引用列表”**，有引用不证明模型正确利用了证据。
- 检索动作与快照选择通过 action journal 保护恢复；同一任务已完成动作可复用，未知外部动作仍停止自动重试。该日志不是通用检索缓存。
- 文档检索查询使用目标、API 名、错误类型和 focus，不传可信答案、候选 stdout；未加入原始 traceback 自动抽取。错误位置反馈仍可增强。
- 经验 embedding 现在使用同一外部调用记录格式，单独记录在 retrieval_runs 的 memory_embedding 条目中；日志写入失败不改变已完成任务，不承诺无损计费审计。

## 界面

工作台新增“领域知识证据”，展示查询、实际检索模式、兼容版本、来源位置、片段、候选和 usage。刷新后按任务读取数据库记录。插件显示最终知识引用摘要和工作台链接。

API：

- `GET /api/v1/knowledge`：当前各领域快照。
- `GET /api/v1/tasks/{task_id}/retrievals`：任务检索证据；使用原有令牌与任务归属校验。

不提供任意网页抓取 / 上传界面；更新文档使用受控本地 CLI。

## 评测入口

### 无外部费用的检索评测

```powershell
.\.venv\Scripts\python.exe -m code_assistant.knowledge.evaluation --mode bm25 --split test
```

`benchmarks/rag/queries.json` 有 30 条种子查询，按主题族拆 dev/test；标注由项目构建，尚未独立人工复核，包含 2 条无答案查询。报告分开记录有答案的 Recall@3 / MRR / nDCG 和无答案误召回率。现阶段题目接近知识标题，成绩不能外推到真实用户查询。

选择 dense / hybrid / rerank 并显式允许外部调用后，才能对比真实模型检索质量；退化运行必须看实际模式，不可按请求模式冒充结果。

### 修复对照预算预览

```powershell
.\.venv\Scripts\python.exe -m code_assistant.knowledge.repair_evaluation --limit 2 --variants fixed bm25
```

默认仅打印计划调用上限。`--execute-live` 才调用生成模型，`--allow-external-retrieval` 单独允许检索服务。没有执行这些付费实验。

现有 6 个 LCEL 错误初始代码任务与独立保留用例；参考实现只用于校验评测集，不注入知识库或模型。普通功能测试不能证明实现内部一定采用 LCEL。

对照模式：none / fixed / bm25 / dense / hybrid / rerank / child_only。所有组共享 v2 控制器、角色和工具，关闭经验记忆，使用相同初始错误代码及生成预算。为隔离上下文影响，该评测每任务预检索一次，不允许迭代重新检索；在线产品仍允许受限动态检索。child_only 使用相同 parent 排序后只注入命中子块，**不是 child 级 rerank 对照**。

每次实验保存 manifest、逐题 runs 和分组 summary；降级、未知 usage 与失败不隐藏。当前尚未实现显著性分析和独立总货币费用预算，只提供分服务调用上限及用量记录。

## 验证方法

```powershell
$env:RUN_PLATFORM_INTEGRATION = '1'
.\.venv\Scripts\python.exe scripts/run_tests.py --docker
npm.cmd --prefix frontend run build
npm.cmd --prefix extension run build
npm.cmd --prefix frontend run test:e2e
```

真实集成测试会使用本地 PostgreSQL 和 Docker；生成模型、embedding、rerank 使用确定性替身或被禁用。向量 SQL 和约束使用实际 pgvector，不能将替身向量测试等同于 embedding 质量验收。

首轮真实容器测试纠正了一个错误假设：runner 会把 print 重定向到 stderr，单纯打印并不会破坏 JSON 输出。因此负例已改成“只 print 不 return”，对应返回 null。失败报告保留，最终结果见验证记录。

## 后续仍需完成的效果工作

独立审核文档和检索标注；扩充官方版本材料及复杂任务；校准无关查询拒绝策略；明确预算后运行真实 embedding / rerank 与修复消融。通过工程测试不表示 RAG 已提高修复成功率，暂不在简历填写提升百分比。

## 本次实际结果

完整 Python 回归 84 项通过、0 跳过，详见 [报告](../test_results/20261020T080253Z-397946f3/report.json)。后续评测汇总代码的定向回归 9 项通过。原三项浏览器测试通过，新增 RAG 证据与刷新恢复测试修正输入框标签后通过；前端和插件构建通过。

本地 BM25 测试报告：[report.json](../benchmark_results/rag-retrieval-20261020T080856205562Z/report.json)。15 条测试查询中 14 条有答案查询 Recall@3 / MRR / nDCG 均为 1.0，但唯一的无答案查询仍返回不相关片段。该小型种子集不足以支持效果提升结论，保留这项负面结果，不据此调参后复用测试集宣称泛化。

[工作台证据截图](../test_results/rag-workbench.png) 使用固定模型响应，显示实际本地检索和持久化证据，不是外部模型质量演示。
