# 固定任务集与对照实验

`tasks.v1.json` 是人工维护的 **24 个任务、72 个用例** 的小型自建评测集，非 HumanEval 等标准基准。
覆盖数值、数组、文本、映射、矩阵、LCEL。每题 2 个开发用例参与修复反馈，1 个保留用例只在
开发验收通过后执行，其失败信息不再进入生成过程。答案和参考代码不进入 Agent 提示词。
LCEL 用例仅验证返回行为，不能证明生成代码内部一定使用了指定框架。

## 四种策略

| 参数 | 行为 | 用途 |
|---|---|---|
| single | 只生成一次并验收 | 单次生成基线 |
| direct | 失败后把错误、当前代码、有限历史直接交给 Coder | 直接错误反馈基线 |
| full | 每次失败固定经过 Reviewer、Reflector 后再生成 | 完整反思基线 |
| adaptive | 首次语法/导入/静态策略错误直接修复；复杂错误或重复错误触发审查反思；同指纹默认第 3 次失败停止 | 本轮新增优化 |

三组固定基线不使用 adaptive 的重复失败早停，避免把优化混入对照组。
四组共享任务、生成接口、模型、角色温度、验收规则、上下文裁剪与预算机制。
角色温度分别为 Coder 0.2、Reviewer 0、Reflector 0.4。

## 运行（仓库根目录，PowerShell）

```powershell
# 无网络、无 Docker、无模型费用：固定故障剧本驱动真实状态图和报告管线
.\.venv\Scripts\python.exe code_assistant/benchmark.py --mode simulate

# 真实模型：先启动 Docker、构建镜像，并配置本地模型凭据
docker build -t code-assistant-sandbox:offline-v1 code_assistant/sandbox_runtime

# 先验证所有人工参考实现（包括保留用例），不调用模型
$env:RUN_DOCKER_TESTS = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:RUN_DOCKER_TESTS

# 固定最大生成次数：对比完整流程效果，同时报告真实调用成本
.\.venv\Scripts\python.exe code_assistant/benchmark.py --mode live --budget-profile attempts --max-attempts 3 --max-calls 9 --repeats 3

# 相同调用上限：direct 可以用更多次生成，full 必须支付审查/反思成本
.\.venv\Scripts\python.exe code_assistant/benchmark.py --mode live --budget-profile calls --max-calls 7 --repeats 3
```

`calls` 配置下总生成次数上限为 `min(max_calls, 20)`，实际调用仍受 Token 预算和早停限制。
这表示相同预算上限，不保证实际花费相等；需要结合每题实际调用和 Token 比较。
`single` 始终只有一次生成。可用 `--strategies single direct full` 仅运行三组固定基线，
`--limit 2` 先做小规模冒烟。`--seed` 仅固定任务/策略的随机运行顺序，不控制厂商模型的随机性。
任务 ID 和重复序号可用于配对分析，重复运行不是新增独立任务样本。

**预算参数按每题每策略每次重复计算**，不是整批总预算。
manifest 写入计划工作流数和调用尝试总上限，运行前请按任务数、策略数和重复次数估算总量。
真实模式在 Docker 预检失败时不会调用模型，不会生成假结果。

## 产物与指标定义

每次运行新建 `benchmark_results/<UTC时间>-<随机ID>/`，不覆盖旧实验：

- `manifest.json`：任务集版本/内容哈希、源码哈希、模型、包版本、角色温度、预算、任务 ID 和运行顺序 seed。
- `runs.jsonl`：每题每策略每次重复一条，包含代码版本、失败轨迹、节点路径、逐次调用 usage、耗时及终止原因；每完成一条即落盘。
- `summary.json`：结构化统计；含环境异常数、usage 缺失次数和终止原因分布。
- `report.md`：可阅读的对照表。中途停止后已完成记录及报告仍保留；不支持自动续跑。
- `workflows/<run_id>/`：逐节点审计、状态快照、代码版本；runs.jsonl 中的 artifact_dir 指向对应目录。

指标采用以下明确分母：

- 全体通过率：开发用例和保留用例均通过的运行数 / 全部已完成运行数。
- 有效运行通过率：排除 Docker、任务格式、模型服务错误后的通过率，同时单列排除数。
- 修复成功率：首次开发验收失败、最终开发及保留用例通过的运行数 / 首次失败的有效运行数。
- 重复错误率：同一运行内同错误指纹再次出现的失败次数 / 有效运行的失败总次数。
- 平均修复轮次：`max(0, 生成次数 - 1)` 的平均值，包括未修复成功的运行。
- 调用次数：逻辑调用尝试数，厂商自动重试已关闭；`request_started` 另行标记是否进入请求。
- Token：仅厂商 `usage_metadata` 作为实测值；缺失则为 null，不能当作零成本。
- 耗时：工作流与最终保留验收的墙钟时间，包括模型、容器启动和执行开销。

首次语法、导入、运行、逻辑错误以及持续重复失败的可控覆盖由 `simulate` 剧本和回归测试提供。
真实生成模式的错误类型由模型实际输出决定，不人为注入指定错误；两类结果必须分开解释。
Docker 验证进度见根目录 VALIDATION.md。模拟管线报告**不能用于写“修复成功率提升 X%”**；
真实模型效果必须通过 live 模式单独测量。

## 错误指纹、上下文与预算

错误指纹包含错误类型、失败用例名、规范化错误信息，去掉路径/行号/内存地址等噪声；
代码指纹使用不含位置的 AST，语法错误时退回源码哈希。相同指纹是启发式同类失败，
不能保证根因完全相同。

每次执行失败立即写记录，审查/反思只补充该记录，不重复追加。重复失败切换 `change_approach`
策略，明确要求换实现思路；adaptive 达到重复上限时以 `repeated_failure` 结束。

上下文默认只保留最近一版代码、最新错误、最新审查/反思及最近 3 次简短失败摘要。
反馈最多 6000 字符，完整提示词最多 20000 字符；裁剪处明确标记。原任务问题不会静默截断，
整体过长时拒绝调用。完整代码历史仍在本地状态/产物中，不反复注入模型。

每次调用前检查调用次数，并按 **UTF-8 字节估计输入 + schema + framing 余量 + 最大输出**
预留 Token。返回后，有 usage 则按厂商用量扣减；无 usage 或调用异常时按预留量保守扣减。
该准入估计不是厂商 tokenizer 或账单硬上限；若实际用量超过估计，记录实际用量并阻止后续超预算调用。
每次输出受 `max_tokens` 限制，调用超时 60 秒，自动重试关闭。
新终止原因包括 `call_budget`、`token_budget`、`repeated_failure`、`model_error`。
最后一次获准调用产生的代码仍执行验收，避免因预算刚好耗尽丢弃有效结果。
