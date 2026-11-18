# Agent 平台 v2：启动、演示与验证

2026-10-20：新增领域文档 RAG 与第五个 Skill `retrieve_domain_docs`。请先阅读 [RAG 升级指南](RAG_UPGRADE.md) 并运行知识入库；下方旧“四个 Skill”等描述属于本次升级前的说明。

当前是本地单用户平台，支持单文件 Python / 离线 LCEL。模型仍在宿主调用，候选代码只在受限 Docker 中执行。旧版 CLI 和四策略 benchmark 保留；新平台 CLI、API、Worker 共用 TaskService。

## 1. 安装与启动（PowerShell，项目根目录）

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd --prefix frontend ci
npm.cmd --prefix extension ci
npm.cmd --prefix extension run build
docker compose -f deploy/compose.yaml up -d --wait
.\.venv\Scripts\python.exe -m backend.setup
```

需要启动 Docker Desktop（Linux containers）。原沙箱镜像不存在时按 README 构建 `code-assistant-sandbox:offline-v1`。数据库端口只绑定 `127.0.0.1:55432`，Compose 密码为本地开发默认值，不用于公网服务。

首次 setup 自动生成 `.platform.env`（已忽略），包含 DATABASE_URL 和随机 PLATFORM_TOKEN；不会打印访问令牌，也不会覆盖已有文件。模型配置仍在 `code_assistant/.env`。

然后分别开三个终端：

```powershell
# 终端 1：API
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app_factory --factory --host 127.0.0.1 --port 8000
# 终端 2：独立 Worker
.\.venv\Scripts\python.exe -m backend.worker.main
# 终端 3：工作台
npm.cmd --prefix frontend run dev
```

工作台：`http://127.0.0.1:5173`；API 契约：`http://127.0.0.1:8000/docs`。在工作台输入 `.platform.env` 中的平台令牌。令牌与模型密钥是两回事，模型密钥不要填入浏览器。

也可运行 `powershell -File scripts/start_platform.ps1`，以隐藏窗口启动三个进程，日志在 `.platform/`。使用 `scripts/stop_platform.ps1` 只停止本次脚本记录且身份匹配的进程。停止前先在界面取消进行中的任务；强停可能产生 unknown_action_outcome，平台不会擅自重放不确定的收费动作。

API 默认不执行模型，Worker 收到任务后会调用已配置的模型，产生费用。`scripts/e2e_server.py` 仅供测试，使用固定 Agent 替身，禁止拿它冒充真实模型演示或作为生产启动入口。

可选开发降级：全新配置时运行 `python -m backend.setup --sqlite`。SQLite 使用持久化 checkpoint，但不提供 PostgreSQL 的向量检索和数据库全局锁；只允许本机单 Worker。已有配置如需切换必须显式修改 DATABASE_URL，setup 不会替你切换数据库。

## 2. 两种工作模式

**静态诊断**：输入目标和代码，Agent 选择静态检查/本地文档 Skill，结果为 diagnostic_only。不执行任意脚本，也不证明功能正确。

**可信验收与修复**：确认 `solve(payload)` 入口并提供人工可信 JSON 用例，先验证导入代码，失败后由 Agent 选择修复或重规划，再重新验收。缺少入口或用例会持久化为 waiting_input，补充后恢复同一 thread。

```json
[{"name":"positive","input":{"a":2,"b":3},"expected":5}]
```

可导入的演示任务见 `examples/platform_add.json`。CLI 提交：

```powershell
.\.venv\Scripts\python.exe -m code_assistant.service.cli --task examples/platform_add.json
# 仅在没有常驻 Worker 时，增加 --run 执行该任务
```

旧版仍可使用 `python code_assistant/main.py --task examples/add.json` 或 `python -m code_assistant.main ...`。

## 3. 插件安装和使用

1. `npm.cmd --prefix extension run build`。
2. Chrome/Edge 扩展管理页面开启开发者模式，加载已解压目录 `extension/dist`。
3. 点击扩展图标打开侧栏，配置本地服务地址和平台令牌。
4. 在 LLM 对话页面点击“提取当前页面代码”，选中目标代码块并核对内容；也可粘贴或先选中文本。
5. 填写任务目标、模式及必要的可信用例后提交，查看结果并复制/下载候选。

当前含通用 pre/code 提取及 ChatGPT assistant 消息 DOM 适配；站点 DOM 改版可能需要更新适配器。只读取页面可见内容，不读网站登录凭据、内部接口或完整对话。插件用轮询恢复状态，Web 工作台用 SSE。插件后台休眠后任务仍由后端持续执行。

## 4. 规划、Skill 与记忆

- Agent 图：intake → plan → execute_skill → plan；模型选择动作，控制器校验计划依赖、工具权限、预算和完成标准。
- 四个内置 Skill 位于 `code_assistant/skills/builtin`。描述摘要用于选择，完整说明按需读取；任意上传/执行外部 Skill 不受支持。
- 复杂失败经 Reviewer/Reflector 再由 Coder 生成，简单静态错误可直接修复；角色调用共同计入预算。
- 动作和模型调用进入 action_runs 日志。完成结果可复用；started 但结果未知的动作停止为 unknown_action_outcome，避免盲目重复计费。
- checkpoint 不包含可信答案；答案保留在内部 task request，模型只得到净化观察。
- 长期记忆仅保存可信通过任务的精简错误/修复经验，带来源与镜像、Skill 内容身份；可查看和删除。PostgreSQL 使用全文与词项检索，默认不调用额外模型。
- 设置 `EMBEDDING_URL`（完整 embeddings 端点）、`EMBEDDING_MODEL`、`EMBEDDING_API_KEY` 后可启用语义召回；有外部调用成本，默认关闭。当前 pgvector 用精确余弦查询，未建立 ANN 索引，不宣称大规模低延迟检索。
- embedding 缺失/失败时降级到词项检索；其用量尚未并入生成模型账单，不应将当前 Token 显示当作总费用。

## 5. 数据与恢复边界

PostgreSQL 保存任务、事件、动作、产物和经验；代码产物以文本列存储（首版规模受限），不是任意磁盘路径。计划版本通过事件和 checkpoint 追踪，未另设 plans 表。Skill 版本通过 registry/hash 追踪，未另设 skill_versions 表。

Worker 以数据库领取任务、租约、心跳和 fence 拒绝过期提交；首版全局单 Worker、串行容器执行。SQLite 使用文件锁。取消会触发容器有界清理；进行中的模型请求可能要等 SDK 超时，无法保证取消退款。

检查 `/health/live` 只证明 API 存活；`/health/ready` 同时检查数据库、近期 Worker 心跳和 Docker。SSE 事件有递增 seq，可以补拉，读取不会触发执行。

## 6. 验证命令

```powershell
# 不调用付费模型：单元/API 回归（未显式启用的外部服务测试会跳过）
.\.venv\Scripts\python.exe scripts/run_tests.py

# 真实 Docker + PostgreSQL，模型全部用替身
$env:RUN_PLATFORM_INTEGRATION='1'
.\.venv\Scripts\python.exe scripts/run_tests.py --docker
Remove-Item Env:RUN_PLATFORM_INTEGRATION
.\.venv\Scripts\python.exe scripts/platform_smoke.py

# TypeScript/打包与浏览器链路（测试启动 8000/5173，请先停常驻服务）
npm.cmd --prefix frontend run build
npm.cmd --prefix extension run build
npm.cmd --prefix frontend exec playwright install chromium
npm.cmd --prefix frontend run test:e2e
```

浏览器测试使用独立临时配置和固定 Agent；插件完整加载测试当前使用已安装的 Edge，代码提取测试使用 DOM 固定样例。它们不证明任意网站最新版 DOM 都兼容。

`scripts/platform_smoke.py --live` 使用已有模型配置，单题最多 9 次调用，会发送人工加法任务和候选代码到配置的模型服务并产生费用。只有明确接受该范围后再运行。真实模型实验与模拟/固定 Agent 测试必须分开解读。

## 7. 当前仍需补齐的范围

v2 消融入口为 `python -m code_assistant.benchmark_v2 --limit 2`，默认只预览工作流数和调用上限，不调用模型。显式 `--execute-live` 才执行；`--max-experiment-calls` 约束整批计划调用上限。保留最终 holdout，不向修复反馈。

默认比较 legacy adaptive、Agent 无 Skill 指导文本、Agent 有 Skill 指导文本；没有独立审核的 `--memory-snapshot` 时，不伪造“有记忆”组。快照只读，本次评测不向其中学习。无指导文本组仍保留受控工具与动作协议，不能称作“完全没有 Skill”。

真实模型 smoke 与大规模消融实验、真实 embedding 服务效果、真实已登录站点提取演示尚需独立验证。规划/Skill/记忆的收益不能由固定替身的通过率得出。

目前没有多文件编辑、任意语言、在线候选代码、Skill 热上传、多租户公网服务、自动升级 Skill、ANN 向量索引或自动重试结果不明的模型调用。这些不影响本地单文件平台的工程闭环，但不能写成已实现能力。
