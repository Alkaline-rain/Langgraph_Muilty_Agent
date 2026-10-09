# LangGraph 代码生成与可信验收修复

求职材料：[最新简历项目经历与完成情况](docs/简历项目经历.md)、
[项目知识与 45 个面试问答](docs/项目知识与面试问答.md)。材料区分工程验证与尚未完成的真实模型效果实验。

第二阶段新增：固定评测集、三组基线及自适应策略对照、错误指纹、有限上下文与调用预算。
实验操作与指标定义见 [benchmarks/README.md](benchmarks/README.md)。

第三阶段新增：独立运行目录、逐节点审计和原子快照、模型错误细分、环境诊断和测试报告。
Windows/Docker 完整操作步骤见 [docs/TESTING.md](docs/TESTING.md)，产物说明见 [docs/RUN_RECORDS.md](docs/RUN_RECORDS.md)。

当前支持 **离线、单文件、JSON 输入输出** 的 Python / LCEL 任务。生成代码实现
`solve(payload)`，由人工维护的验收用例判断结果。全部用例通过才报告任务成功。
测试覆盖的是任务规格中列出的行为，不等于证明任意输入正确，也不证明使用了指定算法或框架。

## 安装和运行（项目根目录，PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker build -t code-assistant-sandbox:offline-v1 code_assistant/sandbox_runtime
.\.venv\Scripts\python.exe code_assistant/main.py --check-environment
.\.venv\Scripts\python.exe code_assistant/main.py --task examples/add.json
```

已有虚拟环境时无需重建。也可以用脚本完成依赖检查、镜像构建、烟雾测试及全部回归：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_project.ps1 -Docker -BuildImage
# 如果本机代理为 127.0.0.1:7897，加上 -ProxyUrl http://127.0.0.1:7897
```

代理参数只对脚本进程生效，结束时恢复；Docker Desktop 自身也需配置代理。
上述烟雾测试和回归测试均不调用真实模型，不产生模型调用费用。

需 Python 3.12、Docker Desktop 的 Linux 容器模式（或 Linux Docker）。镜像构建需要联网；
**候选代码运行时无网络**，不自动拉取镜像或安装依赖。镜像预装固定版本的 `langchain-core`。
宿主依赖版本记录于 `requirements.txt`，来自本次测试虚拟环境。

生成、审查、反思 Agent 在宿主调用模型，因此这部分仍需联网及真实 API 凭据。
在本地 `code_assistant/.env` 配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_API_URL`，模型名沿用
`config.py` 的配置；没有调用真实模型验证账号、模型可用性或费用。
`.env` 不会复制或挂载到执行容器。模型凭据不要提交到版本库。

环境预检失败会在调用模型前以退出码 2 结束。任务验收失败时退出码为 1，通过为 0。
`--max-iterations` 范围为 1–20；表示总生成次数，而非重试次数。

## 任务与验收协议

参考 `examples/add.json` 和 `examples/lcel_offline.json`：

```json
{
  "question": "实现 solve(payload)，返回 payload.a 与 payload.b 之和",
  "execution_mode": "offline",
  "cases": [
    {"name": "positive", "input": {"a": 2, "b": 3}, "expected": 5}
  ]
}
```

- 用例由任务作者人工维护，不由待测模型生成或改写；无用例、空用例、在线模式被拒绝。
- 每例输入及预期输出上限 64 KiB，用例数 1–50；支持有限 JSON 数值。
- 候选代码只得到当前用例输入。预期输出不挂载到容器，也不进入 Agent 提示词。
- 容器 runner 导入候选模块并调用 `solve(payload)`，序列化返回值；普通 print 走诊断通道。
- 宿主比较 JSON 值：对象键顺序无关，数组顺序有关，布尔值与数值不同，1 与 1.0 视为相等。
  不支持浮点容差比较，相关任务请规定舍入精度。
- 每个用例使用新容器，任一失败立即停止本轮；通过用例数不代表全部用例已执行。
- 错误答案或非法 JSON 为 `test_failure`；语法、导入、运行、超时错误保留各自分类。
- LCEL 用 `FakeListLLM` / `RunnableLambda` 进行离线验证。真实在线服务任务暂不支持，
  不能简单开放容器网络来绕过限制。

## 工作流与终止规则

`generate → execute（统一预检 + Docker + 可信比较）`

- 用例全部通过：`tests_passed → END`。
- Docker 服务/镜像/执行配置异常：`environment_error → END`，不浪费模型调用修复环境。
- 无效任务：`invalid_task → END`。
- 默认 adaptive：首次语法/导入/静态策略错误直接回到 generate；复杂或重复错误经 review → reflect → generate。
- 重复失败和调用/Token 预算到达上限时终止；可用 `--strategy full` 使用原来的固定反思流程。
- 次数耗尽：`max_iterations → END`，保存最后一轮失败。

Reviewer 的 `passes` 仅作为模型审查意见，不能覆盖客观验收失败。
默认输出 `runs/<run_id>/`，每个节点后更新 `state.json`；另有 `manifest.json`、`events.jsonl`
及 `code/attempt_001.py` 等代码版本。`--output` 可指定运行目录根路径，不再覆盖同一个轨迹文件。
保留终止原因、验收明细、代码历史和失败记录，不复制任务的可信预期答案；候选代码自身输出也会记录。

## 执行边界

`SANDBOX_MODE=docker` 为默认；兼容 `auto`，但同样只使用 Docker。
`safe` 或未知模式直接报错，旧的进程内执行入口以及 `muilt_agents.py` 已停用。
可通过 `SANDBOX_IMAGE` 指定由操作者构建并信任的同协议镜像。

语法编译、导入白名单和 AST 规则在 Docker 前统一运行；白名单见
`code_assistant/utils/static_validation.py`。这些规则可能限制普通 Python 写法，
也不构成完整的 Python 安全证明，隔离依赖 Docker 和宿主配置。

容器配置：禁网、非 root、移除 capabilities、禁止提权、只读代码和根文件系统，
256 MiB 内存及同值 memory-swap、0.5 CPU、64 进程、64 MiB tmpfs。
每例默认 10 秒容器超时、15 秒宿主兜底；stdout/stderr 分别限制 64 KiB，超限终止。
成功和异常路径均尝试有超时上限的清理；无法确认清理则报环境错误并给出容器名。
退出码 137 仅说明 SIGKILL，不能据此断言一定 OOM。

## 测试

```powershell
# 不调用真实模型，不需要 Docker；使用真实 LangGraph，替换模型/执行返回值
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 构建镜像并启动 Docker 后，执行真实容器集成测试（仍不调用付费模型）
$env:RUN_DOCKER_TESTS = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:RUN_DOCKER_TESTS
```

启用 Docker 测试后，如果环境不可用会失败，不会悄悄跳过。
集成用例覆盖正确/错误加法、无限循环超时、过量输出和离线 LCEL。
`scripts/run_tests.py --docker` 还会验证全部参考任务和容器内核配置，并将结果写入 `test_results/`。
最新实测状态见 [VALIDATION.md](VALIDATION.md)。
