# 代码优化与 Agent 平台升级大纲

制定日期：2026-10-11。状态更新：2026-10-13，**M0–M6 工程实现与集成验证已落地，M7 真实模型效果实验待授权**。

本文作为后续修改代码的实施依据。目标是在现有可信执行内核上增加规划、Skill、跨任务记忆和完整产品入口；各阶段必须有可演示行为与验收证据，不能仅增加依赖或提示词。本文保留原始设计要求；实际实现、设计取舍与测试证据以 [升级交付记录](UPGRADE_STATUS.md) 为准。

## 1. 项目定位与范围

建议升级方向：**面向 LLM 生成代码的可规划验证与修复 Agent 平台**。

用户在 LLM 对话页面选择代码 → 插件提取并展示待提交内容 → 后端建立任务 → Agent 分析目标、检索经验、制定计划、选择 Skill 并调用工具 → 隔离验证与修复 → 插件及 Web 工作台展示结果、代码差异和证据。

首版继续限制为单文件 Python / 离线 LCEL，保留现有 CLI 和四策略评测。多文件工程、任意语言、在线依赖安装、自动操作对话网站发送消息不进入首版。

“更像 Agent”的验收标准：能够基于目标和观察选择不同动作，使用工具结果更新计划；信息不足时请求补充；明确记录选择的 Skill、计划变更与停止原因。增加 Planner 提示词后仍按固定链条执行，不算完成这一目标。

## 2. 现状与必须保留的基础

当前已有：三类模型角色、四节点状态图、Pydantic 输出、AST 检查、Docker 执行、可信 JSON 用例、错误指纹、有限历史、预算和文件轨迹。已有 55 项通过的回归记录及真实 Docker 验证；真实模型效果实验尚未完成。

主要入口：

| 当前文件 | 后续处理 |
|---|---|
| `code_assistant/main.py` | 提取可复用服务接口；保留旧 CLI 行为与 legacy 图 |
| `graph/state.py`、`graph/nodes.py`、`graph/edges.py` | 保留旧图；另建 agent_v2 状态与路由，避免直接破坏基线 |
| `agents/` | 复用生成、审查、反思；新增 Planner 与受约束动作选择 |
| `schemas/models.py` | 复用执行模型；新增任务、计划、动作、Skill、记忆、事件契约 |
| `utils/static_validation.py`、`acceptance.py` | 作为工具适配底层；成功标准不交给模型修改 |
| `utils/docker_sandbox.py` | 保留隔离边界，扩展取消、动作 ID 与资源配额接口 |
| `utils/llm_runtime.py`、`repair_policy.py` | 扩展预算覆盖规划、检索嵌入及记忆提炼调用 |
| `utils/run_store.py`、`trace.py` | 保留文件导出；增加数据库事件与产物索引 |
| `benchmark.py`、`benchmarks/`、`tests/` | 保留原实验，增加规划/Skill/记忆的消融与集成测试 |

约束：不得改成宿主执行候选代码；预期答案不进入模型或候选容器；不得将模型自生成测试视作独立可信验收；环境不可用仍快速失败。

## 3. 技术选型与引入顺序

| 领域 | 默认方案 | 引入原因与时机 |
|---|---|---|
| Agent 编排 | LangGraph + LangChain + Pydantic | 复用现有代码，增加规划、工具动作与持久化 |
| 后端 API | FastAPI + Uvicorn | 复用 Pydantic 契约，提供 OpenAPI 与异步事件接口；本方案不同时维护 Flask |
| 持久化 | PostgreSQL + SQLAlchemy + Alembic | 保存任务、动作、记忆、事件；用迁移管理 schema |
| 状态恢复 | LangGraph PostgreSQL checkpointer | 保存线程内执行位置，与业务记录和长期记忆分开 |
| 任务执行 | 独立 Worker，第一版使用 PostgreSQL 任务表领取任务 | 与 API 生命周期解耦，先控制单 Worker 并发与恢复复杂度 |
| 语义记忆 | PostgreSQL + pgvector + 可配置 embedding 适配器 | 先结构过滤/全文检索，再增加向量召回，不单独引入另一套向量数据库 |
| Web 工作台 | React + TypeScript + Vite + Monaco Editor | 展示代码、diff、计划、事件和运行证据 |
| 浏览器插件 | Chrome/Edge Manifest V3 + TypeScript | 用户主动提取代码，side panel 展示任务结果 |
| 事件传输 | REST + SSE | 创建/取消/补充走 REST，进度走单向流；首版不同时维护 WebSocket |
| 执行环境 | 复用 Docker 沙箱；Compose 启动 API/数据库等服务 | 管理开发环境；容器 Worker 访问 Docker 另行限制权限 |
| 测试 | 既有 unittest + API 集成测试 + 前端/插件关键链路 Playwright | 按模块验证，不为更换框架重写全部既有测试 |
| 后续可选 | Redis + Celery、OpenTelemetry | 任务规模或观测需求明确后再引入，首版不堆叠两套队列 |

使用 FastAPI 的 BackgroundTasks 不能替代需要恢复、取消和资源管理的任务 Worker；官方也建议对复杂后台工作考虑独立任务方案。[FastAPI 文档](https://fastapi.tiangolo.com/tutorial/background-tasks/)

版本策略：以仓库锁定的 Python 3.12 与 LangGraph 依赖为基线，实施时验证 checkpointer、数据库驱动和模型结构化调用兼容性，再锁定 Python/Node 依赖。不得直接套用最新文档 API 后跳过兼容测试。

## 4. 整体架构与两条任务路径

```text
LLM 页面 → Content Script → 扩展 Service Worker / Side Panel
                                      │
React 工作台 ──────────────────────────┤ REST / SSE
                                      ↓
FastAPI → PostgreSQL 任务/事件/动作表 → 独立 Worker
                                      ↓
            LangGraph Agent：理解目标 → 召回记忆 → 规划
                                      ↓
                 动作选择 → 策略校验 → Skill/Tool 执行
                                      ↓
               Docker/可信比较 → 观察 → 重规划或结束
                                      ↓
             状态 checkpoint + 代码产物 + 经验候选
```

### 4.1 代码诊断模式 `diagnose`

适用于只有页面代码、缺少可信预期或入口约定的场景。支持静态诊断；执行必须先有明确的受支持入口和输入协议。缺协议时报告 `needs_input`，不擅自执行任意脚本。

可以生成测试建议和修复候选，但报告必须标注 `unverified` / `diagnostic_only`。即使样例运行无异常，也不能显示“功能验证通过”。

### 4.2 可信验收修复模式 `verified_repair`

用户明确任务目标、`solve(payload)` 输入输出约定及可信用例，或选择平台预置任务。先验收导入代码，再决定是否需要修复，而不是默认重新生成覆盖原代码。通过可信验收才标注 `verified`，同时说明用例覆盖范围。

来自页面的函数可能没有 `solve`：保留原始代码和哈希；优先请求入口映射/输入说明。适配器生成一份独立候选，展示转换 diff，不能静默改写输入输出语义。首版不支持的脚本、语言、多文件依赖返回清晰原因。

### 4.3 入口统一

任务来源 `source_type = cli | web | extension`；执行引擎 `engine = legacy | agent_v2`；任务模式与来源独立。浏览器提交已有代码，CLI 仍可从任务要求生成代码。

页面消息、代码注释、检索记忆全部作为不可信内容，不能覆盖执行权限、预算或可信用例。可信答案使用内部存储引用，Planner/模型状态只接收净化后的任务视图。

## 5. Agent 规划与执行模块

### 5.1 状态与动作契约

新增 `AgentTaskState`：`task_id`、`thread_id`、`goal`、`mode`、`plan_version`、`plan_steps`、`current_step`、`observations`、`selected_skills`、`memory_refs`、`artifact_refs`、`budget`、`pending_input`、`termination_reason`。

计划步骤至少包含 `step_id / objective / skill_id / dependencies / success_criteria / status`。第一版允许表达依赖但串行执行，暂不做并行子任务调度。

模型返回 `AgentAction`：

- `type`：`call_skill | revise_plan | request_input | finish`。
- `skill_id`、符合该 Skill schema 的 `arguments`、引用的 `observation_ids`。
- 简短决策依据 `decision_summary`，用于解释选了什么动作；不要求模型输出隐藏思维链。

确定性控制器校验动作类型、工具权限、参数、依赖、预算、步数和重复动作，再分发执行。模型不能自行扩大工具集合、修改预算或把失败改成成功。

### 5.2 推荐循环

`intake → retrieve_memory → plan → select_action → validate_action → execute_skill → observe → select_action/replan/finish`。

触发重规划：假设不成立、相同错误重复、当前 Skill 不适用、预算不足以完成原计划。重规划保留旧版本与原因，并受独立次数上限约束。

信息不足用 LangGraph interrupt 暂停，用户补充后以同一 thread 恢复。恢复会重执行中断节点的部分代码，因此中断前不能放无保护的模型调用、容器创建等副作用。[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

### 5.3 验收

- 同一入口面对语法错误、逻辑错误、缺少用例，产生不同的工具路径。
- 工具观察能触发计划版本变化；失效 Skill/越权参数被控制器拒绝。
- `finish` 必须通过确定性终止校验；诊断任务和验证任务使用不同结果标签。
- 规划、审查、反思共用总调用预算，另设最大动作数、重规划次数与运行时限。

## 6. Skill 与 Tool 系统

本文 Skill 指项目运行时的可复用能力包，不是给开发助手安装插件。Tool 是原子操作，Skill 是任务适用说明、步骤建议、输入输出约定与允许工具的组合。

### 6.1 目录与注册

```text
code_assistant/skills/
  registry.py
  loader.py
  executor.py
  builtin/
    diagnose_python/SKILL.md
    repair_python/SKILL.md
    validate_lcel/SKILL.md
    verify_cases/SKILL.md
```

每个包的受控 metadata：`id / version / description / input_schema / output_schema / allowed_tools / applicable_errors / resource_limits`；SKILL.md 放策略说明与例子。首版仅加载仓库维护的内置包，registry 将 ID 映射到已注册实现，不执行网页或模型生成的任意插件 Python 文件。

渐进加载：先向 Planner 提供目录摘要，选中后才加载完整说明和必要参考资料；记录 Skill 版本/内容哈希以复现实验。

### 6.2 首批能力

| Skill | 职责 | 可用工具 |
|---|---|---|
| diagnose_python | 静态诊断与问题定位 | `static_check`、`inspect_artifact` |
| repair_python | 基于证据生成修复候选 | `generate_candidate`、`diff_versions` |
| validate_lcel | 查询本地兼容说明，验证离线 LCEL | `search_local_docs`、`static_check`、`run_sandbox` |
| verify_cases | 用受控用例执行与比较 | `evaluate_trusted_cases` |

公共工具另提供 `search_repair_memory`；长期记忆写入经过验证门禁，不允许 Agent 任意写成“成功经验”。`evaluate_trusted_cases` 从内部引用取答案，输出净化后的观察，不能把预期明细返给模型。

工具结果统一为 `ToolObservation`：状态、错误类型、证据引用、摘要、耗时、资源消耗、可重试属性。现有 Docker/acceptance 返回值通过适配层转换，不复制两套执行逻辑。

验收：同一 Skill 能用于 CLI 和 API；未知 Skill、schema 不匹配、越权工具调用均拒绝；失败具有结构化原因。无需用户上传任意 Skill 文件即可演示完整循环。

## 7. 更强的记忆：三层分工

### 7.1 工作记忆与 checkpoint

当前任务保存计划、观察、近期失败和产物引用；大段代码放产物存储，不无限堆入上下文。使用持久化 checkpointer 支持进程重启后的状态恢复；保留既有文件快照供人阅读。

Checkpoint 面向单线程状态，长期记忆面向跨任务经验，两者不能混为一张聊天历史表。[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

### 7.2 情景记忆：跨任务修复经验

`RepairEpisode` 保存：任务标签、错误摘要/指纹、根因假设、修复摘要、必要最小代码片段、验证范围、来源 run_id、镜像/依赖与 Skill 版本、创建时间、namespace。

区分 `candidate / verified / rejected / expired`。通过独立可信用例的结果才进入 verified；仅模型自评、仅自生成测试通过或纯诊断结果不能写成已验证经验。

### 7.3 程序性知识与检索

Skill 是人工审核的程序性知识；任务完成后只能提出 Skill 改进建议，不能自动改写启用中的规则。先按 namespace、语言、库版本、错误类型过滤，再全文/指纹匹配与向量检索融合排序，最后按 Top-K 和上下文预算注入。

向量模型、维度、版本写入 metadata。更换 embedding 模型需要重建对应索引，不能混用空间；embedding 服务不可用时降级到确定性检索并记录原因。是否本地部署小型中文/多语言 embedding 模型，在实施阶段按设备条件确定；维持适配接口。

### 7.4 防污染与效果验收

- 默认本地单用户 namespace，数据模型预留 owner；跨用户共享默认关闭。
- 不存凭据、完整对话或可信预期答案；经验保留出处、支持查看和删除。
- 长期记忆是参考证据，不能覆盖当前任务要求或绕过工具权限。
- 评测按实验划分记忆训练/使用范围；同题、同源变体和保留答案不得通过记忆回流。
- 比较无记忆、规则检索、混合检索的成功率/成本；记录实际检索 ID，检查误召回和过期经验。

## 8. 后端服务、Worker 与恢复

### 8.1 业务分层

API 负责认证、输入校验和事件读取；TaskService 负责创建/取消/补充；Worker 负责运行图；Repositories 负责业务持久化；工具层负责执行。HTTP 处理函数不直接跑长时间 LLM/Docker 循环。

任务状态：`queued → running → waiting_input / succeeded / failed / cancelled`；恢复中使用 `recovering`。另设结果等级 `verified / unverified / diagnostic_only`，避免“任务处理成功”被误解为“代码正确”。

### 8.2 API 草案

| 接口 | 作用 |
|---|---|
| `POST /api/v1/tasks` | 提交任务，支持 Idempotency-Key，返回 202 与 task_id |
| `GET /api/v1/tasks/{id}` | 读取状态、预算和结果摘要 |
| `GET /api/v1/tasks/{id}/events` | SSE 事件，支持 Last-Event-ID / after_seq 重连 |
| `POST /api/v1/tasks/{id}/cancel` | 幂等取消 |
| `POST /api/v1/tasks/{id}/resume` | 提交所需信息，携带 interrupt_id 和状态版本 |
| `GET /api/v1/tasks/{id}/artifacts` | 获取代码版本与报告的受控索引 |
| `GET /api/v1/artifacts/{id}` | 按产物 ID 下载，不接收任意宿主路径 |
| `GET /api/v1/skills` | 列出可用 Skill 摘要与版本 |
| `GET /api/v1/memories`、`DELETE /api/v1/memories/{id}` | 查询/删除当前 namespace 的经验 |
| `GET /health/live`、`GET /health/ready` | 区分 API 存活与 DB/Worker/Docker 就绪 |

请求模型至少包含：来源元数据、目标、模式、原始代码/语言、入口协议、可信用例引用或人工输入、预算选项。页面中的任意文本不能直接映射为服务配置或工具参数。

### 8.3 数据模型

主要表：`tasks`、`task_inputs`、`plans`、`action_runs`、`task_events`、`artifacts`、`repair_episodes`、`skill_versions`；checkpoint 使用对应后端独立管理。

任务和事件保存 schema_version；事件有 `(task_id, seq)` 唯一约束。可信用例与模型可见任务视图分开存取。文件记录 hash、大小、类型，存储根目录固定，防止路径穿越。

### 8.4 Worker 可靠性

数据库事务领取任务（例如行锁与 SKIP LOCKED），附 owner、lease、heartbeat 和 fencing/version；丢失租约的 Worker 不能继续提交结果。同一 thread 只允许一个有效执行者。

动作 ID 使用任务/计划版本/步骤/尝试编号；动作表记录开始、完成、外部资源和结果。恢复前检查动作与 checkpoint，处理“动作完成但快照未写入”的窗口。无法确认完成的模型请求标为 unknown，不能宣称 exactly-once 或零重复计费。

取消：入队前/执行前检查，节点间检查；执行中的容器按动作标识终止并确认清理；进行中的模型请求按客户端能力中止或等待超时，不能假设取消会退费。所有路径都记录终态。

### 8.5 部署与访问边界

首版默认绑定 localhost；Web 使用开发代理或同源服务，扩展使用配置过的本地端点。至少有本地访问令牌/配对机制，不能认为 localhost 或 CORS 就是认证。限制 Origin、请求大小、提交速率、并发任务与全局容器数。

候选容器绝不挂载 Docker socket。若可信 Worker 通过宿主 Docker socket 管理容器，必须明确该 Worker 具有高权限；API 不暴露 Docker 参数或任意挂载。Windows 开发可保留宿主 Worker，数据库等通过 Compose 启动；不提供“全容器化”但实际无法管理沙箱的虚假启动脚本。

## 9. 浏览器插件

### 9.1 首版交互

用户点击插件 → 扫描当前可见代码块 → 选择并预览 → 补充任务目标/入口/用例 → 提交 → 展示进度 → 查看诊断、修复 diff 和报告 → 复制或下载。

默认不自动提交页面内容，不抓取全量聊天记录，不自动把结果发送回 LLM 对话。需要“回填”时首版提供复制按钮。

### 9.2 组件与站点适配

- Content Script：提取 DOM 中代码块、语言、局部上下文与页面来源；不读取 Cookie、网站 token 或内部 API。
- SiteAdapter：`matches / extractCodeBlocks / extractTaskContext`；先实现通用 `pre/code` 和用户选中文本回退，再适配一个实际使用站点。
- 动态流式页面：MutationObserver + debounce；以内容 hash 判断版本变化，不按 DOM 变化次数重复提交。
- Service Worker：受控消息转发和 API 请求；不能依赖其永不休眠。
- Side Panel：提交预览、状态、结果；保存 task_id，重开后从后端恢复事件。

Content Script 与页面脚本运行环境隔离，但共享页面 DOM；消息必须校验 sender、类型和字段，页面内容不能成为特权命令。[Content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts) / [消息通信](https://developer.chrome.com/docs/extensions/develop/concepts/messaging)

权限优先 activeTab、scripting、storage、sidePanel 与最小后端 host 权限；站点长期授权按需申请。扩展令牌不写入页面 DOM或内容脚本消息，不使用 `innerHTML` 渲染不可信代码。

### 9.3 验收

多代码块选择正确；未完成输出可提示用户等待或提交快照；SPA 更新不重复创建任务；不支持站点可手动粘贴；后端离线有明确提示；扩展重启后可恢复查看任务结果。

“多模态 LLM 生成内容可信评估”项目目前仅作为用户期望的交互参考。其仓库路径、页面布局与复用许可/接口待后续提供；不阻塞当前独立实现，也不假设已有可直接复用模块。

## 10. Web 前端

页面：任务创建/导入、任务列表、任务详情、Skill 目录、经验记忆查看。

任务详情展示：代码编辑与 diff、计划步骤/版本、工具调用与证据、测试结果、错误解释、预算用量、人工补充表单。面向用户显示“等待补充输入”“测试未通过”等明确状态；底层 thread/checkpoint ID 放调试详情。

通过 SSE 接收 `task.started / plan.updated / skill.started / tool.finished / test.completed / input.required / task.finished` 等版本化事件。断线依据事件 seq 补拉并去重；SSE 授权采用能携带凭据的 fetch 流等方式，不把长期令牌放 URL。

不把原始 trace 全部推送给前端；模型上下文与可信答案分别控制访问。样例测试通过、可信测试通过、无测试诊断使用清晰不同的结果文案。

## 11. 建议新增目录

```text
code_assistant/
  service/              # CLI / API 共用任务服务
  agent_v2/             # 新状态图、Planner、动作策略、恢复入口
  tools/                # 对现有验证/执行/检索的适配
  skills/               # registry 与内置能力包
  memory/               # 情景记忆、召回、筛选与版本管理
  persistence/          # repositories、checkpointer 适配、数据模型
backend/
  app/                  # FastAPI routers / auth / schemas / dependencies
  worker/               # 领取、租约、取消与恢复
frontend/               # React 工作台
extension/              # MV3 插件及站点适配
migrations/             # Alembic
deploy/                 # Compose / 启动说明 / 配置示例
tests/                  # 保留既有测试，新增对应模块集成测试
docs/                   # 架构、协议、测试和实验报告
```

先解决当前 `from graph...` 等脚本式导入方式：建立明确的包入口和兼容 CLI 包装，再让 API 导入服务。不要在多个入口各自修改 sys.path。新模块依赖注入模型、工具、仓库与时钟，方便固定替身测试。

## 12. 分阶段实施清单

每完成一个阶段在本节填写完成日期、关键文件、测试命令与证据路径；以下默认全部未完成。

### M0：整理内核与输入协议

- [x] 保存旧测试/模拟基线，确认 legacy 输出兼容。
- [x] 包导入与服务入口重构，新平台 CLI 共用 TaskService；旧 CLI 保留为基线。
- [x] 支持导入候选代码，原始代码与修复代码分离。
- [x] 定义 diagnose / verified_repair、净化任务视图与结果等级。
- 验收：原测试通过；已有错误代码先被验收再修复；无可信用例绝不显示 verified。

### M1：Skill 与工具协议

- [x] ToolObservation、registry、metadata 校验和工具权限。
- [x] 四个内置 Skill 与渐进加载。
- [x] 复用现有验证/沙箱；工具动作独立可测。
- 验收：CLI 可展示所选 Skill、工具证据和权限拒绝路径。

### M2：规划与动态 Agent 循环

- [x] Planner、动作选择、计划版本、受限控制器。
- [x] 重规划、重复动作终止、统一生成模型预算（外部 embedding 计量尚未纳入）。
- [x] 请求输入与恢复接口，保留 legacy 引擎开关。
- 验收：三类不同问题走不同动作路径；能展示一次工具观察导致的重规划。

### M3：持久化与跨任务记忆

- [x] PostgreSQL migrations、checkpointer、产物引用。
- [x] RepairEpisode 验证门禁、规则/全文检索。
- [x] pgvector 检索适配、版本过滤、删除与降级。
- [x] 动作幂等与恢复冲突处理。
- 验收：重启可恢复；错误经验不晋升；跨任务召回有来源且无答案泄漏。

### M4：FastAPI 与独立 Worker

- [x] REST、SSE、本地认证、输入限额、就绪检查。
- [x] 队列领取、租约、心跳、取消、有限并发。
- [x] API 与 CLI 使用同一核心服务。
- 验收：重复提交不重复建任务；断线能补事件；Worker 中断后可识别恢复；取消清理真实容器。

### M5：Web 工作台

- [x] 创建/列表/详情、代码 diff、计划与证据展示。
- [x] SSE 重连、错误与人工补充交互。
- 验收：从导入代码到结果下载完成一条真实链路，展示诊断与验证结果差异。

### M6：浏览器插件

- [x] MV3、通用提取、单站点 adapter、选择预览。
- [x] 后端提交、side panel 进度、复制/下载结果。
- [x] 权限与消息校验、动态 DOM 和重启恢复测试。
- 验收：真实对话页面选择代码，经后端处理后在插件返回结果；不自动发送聊天消息。

### M7：真实效果与交付

- [ ] 单题真实模型 smoke → 小批真实任务 → 有预算的完整实验。
- [ ] 规划/Skill/记忆消融，记录成功率、实际费用/用量与延迟。
- [x] 启动脚本、演示任务、架构文档、故障排查、项目经历更新。
- 验收：至少一条真实浏览器到后端到结果的演示与可追溯实验记录。效果差也如实分析。

依赖顺序默认 M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7；前端/插件原型可以在接口契约固定后提前做，但不能以 mock 页面代替后端验收。首轮编码从 M0 开始，避免一次性重写全部目录。

## 13. 测试与评测要求

| 层级 | 必测行为 |
|---|---|
| Agent 单元 | 动作 schema、工具选择、无效动作、重规划、预算与重复终止 |
| Skill | 版本固定、工具白名单、非法参数、净化观察 |
| 记忆 | 无命中降级、版本过滤、误召回、隔离、删除、未验证经验门禁 |
| 恢复 | 领取后崩溃、动作完成快照前崩溃、租约过期、重复 resume |
| API | 幂等提交、任务归属、事件补拉、取消竞争、路径/大小限制 |
| Docker | 保留禁网/权限/资源回归，加取消清理、并发全局上限 |
| Web/插件 | 多代码块、DOM 更新、断线重连、不支持站点、提交内容预览 |

保留原四策略作为旧内核对照；新增对照按同一任务、预算和环境比较：legacy adaptive、规划+工具、规划+Skill、规划+Skill+记忆。固定种子只控制实验顺序，不能声称完全消除模型随机性。

当前 24 题偏小，还需增加“导入损坏代码、缺少入口/用例、需重规划、相似但修法不通用”的任务。诊断正确性、信息补充行为与代码通过率分开统计；记忆不能使用当前测试集运行结果预热，跨重复实验重置或冻结记忆快照。

指标至少包括：全体/有效通过率、修复成功率、未知 usage 数、真实模型/embedding 用量、总耗时、重规划与无效动作次数、工具选择可核对结果、记忆采纳后的验证结果。没有真实实验前，不写性能提升百分比。

## 14. 后续修改代码时的执行规则

1. 开始阶段前阅读本文件、相关源码与仓库指令；检查已有改动，保留用户工作。
2. 每次只完成一个可验收阶段，按依赖顺序增量改造；保留 legacy 可运行。
3. 接口或 schema 先写契约与迁移策略，再接 UI；mock 必须有标识。
4. 新增技术必须对应真实功能、可观测事件和测试，不能只加 requirements。
5. 涉及真实模型的运行先估算整批调用上限，使用用户已授权范围；本次仅写大纲，不执行付费实验。
6. 每阶段记录改动、验证、局限和下一步，更新本文件复选框；未实测不写“已完成”。

## 15. 实施前需最终确定的少量信息

- 优先适配的 LLM 网页及浏览器；默认 Chrome/Edge MV3，先通用选择提取。
- 另一个多模态评估项目的目录；未提供前独立实现，不阻塞核心。
- 真实模型与 embedding 的可用服务和预算；设计保留适配器，不把某供应商写死。
- 首版默认本地单用户，不做公网部署或复杂账号系统；如需多人访问，需另加身份、配额、隔离验收。

完成后最有价值的展示不是技术栈列表，而是：**用户选中一段有问题的代码，系统给出可检查的计划，选择工具和经验，遇到失败会调整步骤，最终返回带验证证据的修复结果，并能解释与恢复整个过程。**
