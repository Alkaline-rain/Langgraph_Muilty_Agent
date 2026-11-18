# 领域代码验证与修复 Agent：RAG 升级实施大纲

制定日期：2026-10-20。

状态更新：**2026-10-20 已按本大纲实施核心工程链路；真实外部模型效果、独立标注复核尚未完成。**

本文件保留原始设计与验收要求，不把所有规划项自动视为已完成。当前实现、取舍和运行说明以 [RAG_UPGRADE.md](RAG_UPGRADE.md) 为准；包括原生 vector + profile 维度触发器、项目笔记语料、拒绝超长原子块等具体取舍。

## 1. 目标与明确范围

将当前平台升级为“基于领域文档检索增强的代码验证与修复 Agent 平台”，首个领域为 **LCEL（LangChain Expression Language）**。

主线：导入代码与需求 → 识别相关 API / 错误 → 检索兼容领域文档 → 结合证据生成或修复 → 静态检查与 Docker 可信验收 → 按预算迭代。

复用同级 `Agent_MCP_RAG` 项目的父子结构分块、BM25 与 Dense 混合召回、RRF 融合及 rerank，适配本项目的状态、数据库、模型预算与测试体系。

明确不做：

- 不新增缓存层、Redis、检索结果缓存或消息队列组件；不迁移原项目相关实现。
- 不引入 MCP Client / Server。Skill 是能力组织，MCP 是工具接入协议，两者并非互相替代；本项目工具在同一后端，当前无新增协议层的需求。
- 不重写已有任务 Worker、数据库任务领取或 SSE。保留它们支持现有产品，不扩大后端基础设施范围。
- 不扩展到任意语言、多文件仓库、联网候选代码、在线依赖安装。
- 不把拟实现功能、检索提升或修复率提升提前写成简历成果。

新定位并不意味着“所有错误都必须检索”。简单语法错误和普通 Python 任务允许直接诊断 / 修复；领域 API 使用、版本不兼容和缺乏相关知识时才启用文档 RAG。

## 2. 源码核对与现状

### 2.1 当前项目

| 文件 / 模块 | 现状 | 升级方向 |
|---|---|---|
| `code_assistant/utils/doc_loader.py` | 读取一份本地 `offline_context.md` | 分离强制执行协议和可检索领域知识 |
| `agent_v2/engine.py::tool()` | `validate_lcel` 将固定文本截取后作为证据 | 显式检索 Skill 返回带出处的知识证据 |
| `agent_v2/engine.py::repair()` | 固定注入 LCEL 文本前 1600 字符 | 注入经过预算控制的相关文档片段 |
| `agent_v2/model.py` | 输入目标、代码、观察、经验与 Skill 目录 | 增加文档证据摘要、检索状态与预算 |
| `memory/store.py` | 已验证修复经验的检索，具有可选向量与 RRF | 保留为经验记忆，不充当领域文档库 |
| `skills/registry.py` | 四个内置 Skill，统一 focus 参数 | 增加文档检索 Skill 与专用参数契约 |
| `utils/acceptance.py` / Docker | 独立可信验收 | 保持成功标准，不以引用文档代替测试 |

当前不能称为“已有完整领域文档 RAG”：固定文件读取没有按任务查询的文档召回、排序与父子分块。

### 2.2 已找到的复用来源

源目录：`../Agent_MCP_RAG/Src/augmented/`。以下结论依据本次读取的源码，不仅依据文件注释。

| 来源 | 可迁移内容 | 必须改造 / 不迁移内容 |
|---|---|---|
| `chunking.py` | Markdown section、父子结构、重叠窗口 | 现有按字符切分对 fenced code 不够稳健；增加代码块与 API 上下文保护 |
| `bm25.py` | `rank_bm25` 召回及 allowed IDs 过滤 | 统一中英文、API 标识符分词；校验非正分数筛选策略在小语料的影响 |
| `hybrid_retriever.py` | child ID 融合、父块映射、RRF 公式 | 去除旧 logger / 全局配置依赖；重写数据库适配及版本过滤 |
| `reranker.py` | query-document 对排序和 HTTP 适配 | 独立配置、显式超时、响应验证、预算与降级；不沿用密钥 |
| `db.py` | 文档、父子块和向量分离的数据设计 | 接入现有 SQLAlchemy / Alembic，不整体复制数据库层 |
| `evaluate.py` 与相关测试 | BM25 / Dense / Hybrid / Rerank 分组思路 | 改为代码修复知识查询标注和端到端指标 |

原检索器实际流程为：

`child Dense + BM25 → child RRF → parent 回填并聚合 → parent rerank → Top-K parent`。

原实现直接累加同一 parent 下 child 得分，可能偏向块数较多的父块。本次默认采用同一 parent 下最高 child RRF 分数，并保留所有命中 child 引用；累加作为可选实验，不直接当成已证明更好的方案。

迁移采用复制并适配纯算法 / 契约、保留来源说明的方式；新项目不得通过 sys.path 直接依赖同级项目。记录来源文件内容 hash，若文件含第三方许可则保留。

## 3. 两条数据链路

### 3.1 文档入库链路

受控本地 Markdown 文档与来源清单 → 结构化解析 → 父子分块 → child embedding → 事务写入文档版本 / 块 / 向量 → 激活知识库快照 → 建立该快照的 BM25 索引。

首版由 CLI 显式执行入库，不新增后台导入队列、不提供任意 URL 抓取。外部官方资料先核实版本、来源和使用条件，再保存必要片段 / 摘要；不得混入 benchmark 参考答案或隐藏用例。

### 3.2 在线修复链路

```text
intake：校验任务与环境，锁定知识库快照，读取跨任务经验
  ↓
plan：判断是否缺少领域知识
  ↓
execute_skill
  ├─ retrieve_domain_docs
  │    查询构造 → 版本过滤 → BM25 + Dense → RRF
  │    → parent 回填去重 → rerank → 上下文打包
  ├─ diagnose_python / validate_lcel
  ├─ repair_python：角色读取必要知识证据
  └─ verify_cases：独立验收
  ↓
新观察 → 必要时换查询再次检索 / 修复 → 验收或停止
```

保留三个顶层图节点。不把所有角色重新拆成顶层节点，不为 RAG 单独再套一层自治 Agent。

## 4. 知识语料与执行环境约束

### 4.1 首批语料

- `ChatPromptTemplate`：输入变量、消息模板及调用约定。
- Runnable 与 LCEL 的组合、输入输出传递。
- `RunnableLambda`、字典映射及参数形状。
- `StrOutputParser`、模型输出与文本输出的区别。
- `FakeListLLM` 的离线使用、同步 invoke 路径。
- 镜像内已安装版本对应的导入路径和典型错误说明。

建议第一批形成至少 20 个可独立标注的知识 section。数量是建设目标，不是当前已有数量，也不允许为了数量机械重复文档。

当前 Docker 使用离线 Fake 模型；宿主生成模型与候选程序中的模型不是一回事。检索到真实联网客户端示例也不能使候选获得密钥或联网权限。

### 4.2 每份文档的元数据

`domain`、`library`、`supported_versions`、`source_uri`、`title`、`source_revision`、`content_hash`、`license_note`、`ingested_at`、`trust_level`。

source_revision 标识来源版本，content_hash 标识实际内容，二者不混用。文档须明确支持的镜像内库版本；未知兼容性默认不进入正式检索快照。通过构建清单 / 镜像内受控探针读取候选环境依赖，不以宿主 LangChain 版本替代。

执行协议（solve、JSON、禁网、禁止新依赖等）独立保留为始终注入的可信约束，不作为可被检索结果挤掉的普通文档。

## 5. 父子结构分块

### 5.1 规则

- parent：围绕完整 API 主题或小节组织，保留标题层级、签名、参数说明与相关示例。
- child：父块内细粒度检索单位，携带标题路径和 API 标识符，保持 parent_id 映射。
- Markdown 解析应识别 fenced code，代码注释里的 `#` 不得误判为标题。
- 不在常规长度阈值处直接截断函数签名或代码围栏。超长代码按可识别函数 / 逻辑片段拆分，记录原始范围与 continuation；无法安全拆分的块显式超限或拒绝，不静默损坏。
- 保存父子块在规范化文档中的行 / 字符范围；区分原文范围与检索时补加的标题前缀。
- 块 ID 由文档版本、路径、序号 / 内容 hash 稳定派生；重复导入不生成重复块。

初始参数作为待调优配置：parent 约 1200–2400 字符、child 约 300–600 字符、overlap 约 60–100 字符。字符数不声称为精确 Token 数；模型输入预算另行控制。

## 6. 数据库、索引与快照

沿用 PostgreSQL + SQLAlchemy + Alembic，新增独立知识表，不把文档放入 `repair_episodes`。

| 表 | 关键字段 / 约束 |
|---|---|
| `knowledge_documents` | id、source_uri、domain、library、title；规范化来源与 domain 的唯一约束 |
| `knowledge_versions` | id、document_id 外键、content_hash、source_revision、兼容版本、splitter_version、状态；文档与内容 / 分块版本组合唯一 |
| `knowledge_chunks` | id、version_id 外键、kind、parent_id、ordinal、text、heading_path、位置与 token_estimate；parent/child 关系校验 |
| `knowledge_embeddings` | child_id、embedding_profile、vector、状态；child 与 profile 唯一 |
| `knowledge_snapshots` | id、manifest_hash、创建时间与状态 |
| `knowledge_snapshot_members` | snapshot_id、version_id；复合主键和外键 |
| `retrieval_runs` | id、task_id、action_key、query_hash、snapshot_id、模式、降级原因、usage、耗时、候选及最终证据引用 |

首版固定一个知识 embedding profile（provider/model/dimension），采用原生 `vector(d)` 列，不延续经验库 JSON 转 vector 的实现。维度由已确认的模型配置确定，不在未知模型时猜测。切换维度须新建 profile 对应索引存储并迁移 / 重建，不能将不同模型向量混查。

索引计划：

- 文档及版本的唯一约束、chunks 的 version_id 与 parent_id B-tree 索引。
- 检索日志 `(task_id, action_key)` 唯一约束，支持动作重放。
- snapshot_members 的关联索引，支持按锁定快照过滤。
- 首版向量精确检索作为正确性基线；语料规模需要且实测有收益时再增加 HNSW，并验证过滤后召回与延迟，不把 ANN 作为首版必备成果。
- BM25 使用 rank_bm25 在进程中构建检索索引，不引入检索结果缓存。索引的生命周期与快照绑定，不允许无限累积历史快照对象。

入库先解析、计算向量、校验完整性，再在事务内发布可用快照；失败不得让半份新文档覆盖旧可用版本。新任务选择新快照，运行中 / 恢复中的任务继续使用原快照。首版不物理删除被任务引用的版本，可停用未来检索。

SQLite 开发模式支持结构与 BM25 测试，Dense 明确返回不可用或降级，不伪造向量检索成功。

## 7. 混合检索与重排

### 7.1 查询构造

依据任务目标、代码中静态提取的 API / 导入名、最近净化的错误类型与位置构造 query。补充结构化的错误摘要前须修改验证反馈适配，不把候选 stdout、可信 input / expected 或原始凭据直接拼入 query。

首版采用确定性构造，允许 Planner 提供有长度限制的 focus；不额外引入查询改写模型。查询变化与失败观察绑定，避免只换同义词刷检索次数。

### 7.2 召回与融合

1. 两路使用相同 domain、library version、snapshot、权限过滤范围。
2. BM25 面向 child；分词保留 `RunnableLambda`、`langchain_core`、`invoke` 等完整标识符及必要拆分，固定中英文处理版本。
3. Dense 面向同一批 child，以余弦距离排序；检查维度、有限数值及 profile 一致性。
4. 两路初始各取 Top-20；候选按 child_id 去重，以 RRF `Σ 1/(60+rank)` 融合，不直接相加原始分数。
5. 回填 parent，默认用最高 child RRF 分数排序，并保留命中 child 的定位和两路 rank。
6. 对最多 10 个 parent 做 rerank，最终初始取 Top-3，所有参数可配置并通过验证。

### 7.3 Rerank 与故障降级

沿用原项目“对 query 与 parent 文本对重排”的思路。支持一个可配置的 rerank adapter；不预先承诺具体服务商、最新模型名称或价格。

- 校验返回 index 的范围、唯一性、score 的有限性和结果数量。
- 控制单文档输入长度、候选数、HTTP 超时、输出格式和请求预算。
- 不依赖 HTTP 客户端隐藏重试；明确记录外部请求及未知 usage。
- rerank 失败：返回 parent 级 RRF 结果并标注 fallback。
- Dense 不可用：降级 BM25，并在结果中记录实际模式。
- 无兼容文档：返回 `no_compatible_evidence`；未检索到不等于已证明无相关知识。
- 上述依赖失败不改变工具执行权限，也不触发候选代码联网。

不把不同模型的 rerank 分数当成统一置信概率。低相关阈值需在开发标注集校准，未校准前不得声称“分数大于某值即可证明相关”。

## 8. 接入 Skill、图状态与模型上下文

### 8.1 新能力

新增 `retrieve_domain_docs` Skill，授权 `search_domain_docs` 工具。`validate_lcel` 保留静态检查职责，移除其固定文本假装检索的路径，并允许引用已取得的知识证据。

新增专用 Pydantic 参数，例如 query/focus、domain、limit；registry 按 Skill 选择 schema。snapshot、可访问文档、环境版本和最大预算由服务端决定，不允许模型传参扩大范围。

### 8.2 状态字段

在 AgentState 中增加：

- `knowledge_snapshot_id`、`knowledge_profile`：本任务锁定的版本身份。
- `knowledge_refs`：parent/child/version/hash/位置/检索 run ID 和有界片段。
- `retrieval_count`、`max_retrievals`、`retrieval_history`：检索次数及摘要。
- `retrieval_usage`：embedding / rerank 各自的调用账本。

数据库保存完整检索记录；checkpoint 仅保存恢复所需的引用和有界上下文，不放整库内容。与现有 `memories`、`repair_notes` 明确分开：

| 数据 | 来源 | 用途 |
|---|---|---|
| knowledge_refs | 文档库 | API / 框架事实依据 |
| memories | 验证过的历史任务 | 修复经验参考 |
| observations | 当前工具执行 | 当前错误与验收证据 |

### 8.3 循环与上下文

- 不强制每轮检索；在缺少领域证据或新错误需要其他 API 知识时再检索，初始最多 2 次 / 任务。
- 现有重复动作签名 `skill + code_hash` 不适合检索。检索动作使用 query_hash + snapshot + filters + mode；保持独立总次数上限，语义漂移不能绕过上限。
- `repair()` 中固定 LCEL 文本改为“固定执行协议 + 选中知识证据”；Planner 看证据摘要，Reviewer / Coder 按职责读取必要片段。
- 上下文按完整证据块装配，先去重、再裁选，超限时丢弃低排名块或选择有来源定位的片段，不能直接把代码示例截断在中间。
- 各证据带 `[K1]` 等引用标识。模型可声明使用的 evidence IDs，程序检查 ID 确实存在；引用存在不证明语义使用正确。
- 引用、文档和经验都不是系统权限指令；不允许文档中的“忽略限制”等内容改变工具授权。
- 验证成功仍由当前代码对应的可信用例决定，检索分数和模型审查不能替代。

### 8.4 持久化、预算与费用

检索动作复用现有 action journal，保存完成结果以便 checkpoint 重放；这是恢复记录，不新增通用缓存。外部调用结果未知时沿用停止策略，不盲目重试收费动作。

建立独立 embedding / rerank 账本，记录模型、请求次数、输入估计、provider usage（若有）、耗时和降级。给入库设置整批限制，给在线任务设置每类调用限制；达到预算时降级或明确停止。

账本纳入现有经验 embedding 的调用路径，避免引入两套无法汇总的隐形费用。不同服务 Token 和计价不能直接冒充统一实测金额，未知值保存 null，价格仅在有明确版本的配置时计算估算。

真实模型、embedding 和 rerank 的外部数据发送与费用测试单独列明目的地、内容与预算；本次大纲不授权实际执行付费实验。

## 9. 后端与前端的最小变化

后端复用当前服务与 Worker，增加知识库只读列表 / 状态接口和任务检索记录详情。导入与激活由本地 CLI 管理，首版不新增复杂上传后台。

工作台增加“知识证据”区域，显示：

- query、实际检索模式、快照与库版本；
- 来源标题、parent / child 命中位置与片段；
- BM25 / Dense rank、RRF / rerank 分数的类型标识；
- 实际注入上下文的证据、降级原因、调用数与耗时。

插件只展示简短证据摘要与跳转工作台入口，保留当前提交 / 恢复流程。不把原始数据库字段或冗长检索轨迹强塞进主要操作界面。

事件建议：`retrieval.started`、`retrieval.completed`、`retrieval.degraded`、`knowledge.context_selected`；序号与 SSE 继续复用现有机制。

## 10. 建议新增与修改目录

```text
code_assistant/knowledge/
  contracts.py          # 文档、块、查询、证据契约
  chunking.py           # 代码感知的父子分块
  ingest.py             # 本地清单入库与快照激活 CLI
  repository.py         # 知识表访问、版本过滤、向量查询
  bm25.py               # API 友好分词与稀疏召回
  embeddings.py         # 显式配置的向量服务适配
  reranker.py           # 重排适配与响应校验
  retriever.py          # 两路召回、RRF、parent 聚合、rerank
  context.py            # 有界证据装配与引用
  query.py              # 从任务、API、净化观察构造查询
  evaluation.py         # 固定标注集的检索指标
code_assistant/skills/builtin/retrieve_domain_docs/SKILL.md
knowledge_sources/lcel/ # 受控文档及来源 manifest，不存密钥
benchmarks/rag/         # queries、证据标注、修复任务与拆分 manifest
tests/test_knowledge_*.py
migrations/versions/002_knowledge_*.py
```

现有修改点：contracts、SkillRegistry、AgentState / engine / model、TaskService、doc_loader、模型预算层、API、工作台、benchmark_v2。旧 CLI 基线保留固定知识路径，作为可重复对照，不悄悄让所有 baseline 获得新检索能力。

## 11. 测试与效果评测

### 11.1 工程测试

- 分块：嵌套标题、代码内注释 / 标题字符、长代码、重复内容、中文、边界映射和稳定 ID。
- 入库：重复导入、向量数量 / 维度不符、失败回滚、旧快照仍可读、新快照激活、版本过滤。
- 检索：BM25 标识符匹配、RRF 手算、parent 去重、非正 / 非有限分数、空库、两路候选范围一致。
- 重排：乱序、重复 / 越界 index、空响应、超时、部分结果以及 fallback 标记。
- 上下文：完整块预算、引用能反查、来源失效处理、不泄漏 expected。
- 图：检索 → 修复 → 验收、新错误可换查询、重复检索受限、恢复不重复已完成请求。
- 真实 PostgreSQL：vector 精确查询、维度隔离、关联约束与快照一致性。
- UI / 插件：证据展示和重连不会改变任务状态或丢失原有功能。

原有工程测试保留。固定模型替身只用于验证机制，不作为召回质量或修复率提升证据。

### 11.2 检索评测

先构建不少于 30 条人工审核查询作为起始集，覆盖 API 名称查询、自然语言需求、错误描述、版本冲突和无相关文档。开发集调参，测试集固定；按主题 / 文档族拆分，避免同一问题改写跨集合。

标注 relevant parent、必要 child span 和版本条件；多段都合理时标注多个相关项。完整引用恢复率与 child 命中率分别报告。

对照：BM25 only、Dense only、Hybrid RRF、Hybrid RRF + parent rerank。

指标：Recall@K、MRR、分级相关性 nDCG@K、版本不匹配率、上下文证据覆盖、检索延迟与实际外部调用量。无答案查询单独评估错误取证 / 拒绝行为，不塞进普通 Recall 的分母。

### 11.3 端到端修复评测

旧 24 题保留为回归，不用简单加法任务证明领域 RAG 收益。新增一批 LCEL 错误初始代码，围绕变量不匹配、Runnable 输入形状、解析器使用、导入 / 版本差异等设计。

先固定新版 Agent 架构、生成模型、初始错误代码、可信测试与预算，再只切换文档上下文：

1. 无领域知识（仍保留执行协议）。
2. 原固定 LCEL 说明。
3. BM25。
4. Dense。
5. Hybrid RRF。
6. Hybrid RRF + rerank + parent 上下文。

最后一组不能单独证明父子分块的收益；再单独比较相同召回结果下 child-only 上下文与 parent 回填。检索实验默认关闭经验记忆，后续另做冻结记忆实验，避免混淆文档 RAG 与经验收益。

报告最终保留用例通过率、首次失败后修复率、调用数、Token / 未知 usage、总耗时、知识引用与实际修改对应情况、失败类型。统一生成预算之外另报检索开销，并增加总成本受限比较，不能让检索组隐性多花资源。

至少重复多次并做逐题配对分析；报告样本数量、方差 / 置信区间和失败案例。保留用例不回流，参考补丁不进知识库，测试答案不进入检索 query。未得到正向结果时如实记录，不预设“提升 X%”目标。

## 12. 分阶段实施与验收

### R0：固定基线与契约

- [ ] 记录当前源码、原说明文件、镜像与依赖版本。
- [ ] 明确知识库 / 经验库边界，新增参数与证据 schema。
- [ ] 冻结开发 / 测试划分和对照模式。

验收：旧图、API、插件仍可运行；新方案尚未改变 baseline。

### R1：语料、父子分块与持久化

- [ ] 建立首批版本可核实的 LCEL 文档和 manifest。
- [ ] 迁移并增强分块器；增加知识表迁移和快照。
- [ ] 完成本地导入、幂等、事务与版本回归。

验收：每个 child 能定位到 parent 与来源，失败导入不破坏当前知识库。

### R2：混合召回、RRF 与重排

- [ ] 实现 BM25 / Dense / Hybrid / Rerank 四种模式。
- [ ] 实现 parent 去重、降级、上下文预算与服务账本。
- [ ] 完成检索单测和真实 PostgreSQL 测试。

验收：可追踪每一阶段候选和最终证据；外部服务缺失时明确降级。

### R3：接入 Agent loop

- [ ] 新增检索 Skill、状态引用与检索事件。
- [ ] 替换 v2 固定文档注入，保留独立执行协议。
- [ ] 实现检索预算、重复检测、动作日志与恢复。

验收：固定模型 + 真实检索存储 + Docker 跑通“领域错误 → 检索 → 修复 → 验收”，不修改原可信答案边界。

### R4：可视化与真实评测

- [ ] 工作台展示知识证据，插件展示摘要。
- [ ] 完成检索与端到端对照入口、报告及测试。
- [ ] 经明确外部调用预算确认后运行真实实验，分析无收益 / 失败案例。
- [ ] 根据实际证据更新 README、架构说明和简历项目经历。

验收：工程链路完成和真实效果完成分开标注；没有真实实验不得宣称成功率、检索质量或成本改善。

## 13. 完成后的项目表述边界

预期主线可概括为：领域知识 RAG → Planner / Skill 编排 → 多角色修复 → Docker 可信验收。

技术栈围绕 Python、LangGraph、LangChain、Pydantic、BM25、Embedding、pgvector、RRF、Reranker、Docker 展开。FastAPI / 前端作为交互入口保留；本轮不增加缓存、消息队列和 MCP，以免与新增后端项目重复。

父子分块、混合检索和重排属于拟落地能力；收益只能引用后续真实报告。项目仍是规划器主导的多角色协作系统，不因加入 RAG 自动变成多个独立自治 Agent。
