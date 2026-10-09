# 运行记录、异常与调试

普通运行位于 `runs/<UTC时间-ID>/`，每次创建独立目录。
benchmark 中每个工作流也有独立记录，位于实验目录的 `workflows/` 下。

| 文件 | 内容 |
|---|---|
| manifest.json | run_id、开始/结束时间、状态、模型、Python/包版本、源码/依赖/任务哈希、预算、实际镜像 ID（真实模式） |
| events.jsonl | 工作流开始/结束、节点开始/结束/状态合并事件，逐事件 flush/fsync |
| state.json | 最后完成节点的状态白名单、全部代码历史、失败轨迹、路由、验收及调用明细；原子替换 |
| code/attempt_NNN.py | 每轮可检查的代码版本；只供查看，仍应在 Docker 中运行 |
| holdout.json | benchmark 的最终保留用例验收；不反馈给修复 Agent |

每次执行失败马上创建失败记录，Reviewer/Reflector 补充同一条记录。每轮 attempt_history
保留完整验收信息，包括失败分类、用例名、耗时和截断 stdout/stderr（各最多 4096 字符）。
节点事件时间使用带时区的 UTC；耗时使用单调时钟。

日志不复制 `.env`、认证参数或 task.cases 的预期答案。候选程序自身输出和错误诊断会被保存，
因此包含业务数据的运行产物仍应按项目数据管理。供应商异常只保存分类和异常类名，不保存原始请求/响应。

## 异常与终止

`exec_error_type` 是执行/工作流错误类别；`termination_reason` 表示为何停止。
`call_history[].error_code` 进一步区分模型失败原因：

- `model_authentication` / `model_permission`：认证与权限。
- `model_rate_limit` / `model_timeout` / `model_connection`：限流、超时、连接。
- `model_request_invalid` / `model_server_error`：请求或服务端错误。
- `model_configuration` / `model_output_invalid` / `model_internal_error`：配置、结构化解析及其他内部错误。

模型失败统一以 `model_error` 终止，不能误当作候选代码错误反复重试。
执行器/工作流意外异常以 `internal_error` 终止并保存最后状态；Ctrl+C 保存 `interrupted`。
进程被强杀或断电时不能保证写入最终终止事件，但之前完成节点的快照保留；manifest 可能仍为 running。
磁盘无法写入时不能承诺记录完整。没有实现自动续跑或 LangGraph 持久化 checkpoint。

## 排查顺序

1. 从 manifest 的 status/termination_reason 确认是代码失败、预算、环境还是模型服务失败。
2. 在 state 的 attempt_history 找到失败轮次和用例，核对 code/attempt_NNN.py。
3. 结合 failure_trail 的指纹/重复次数、审查/反思建议，判断是否更换策略。
4. 在 call_history 查看请求是否开始、实际 usage 或未知标记，避免把缺失用量视为零成本。
5. 在 events 中查找只有 node_started、没有 node_completed 的节点，定位中断位置。

benchmark 中工作流 succeeded 只表示开发用例通过；最终是否通过还要看 holdout.json 和 runs.jsonl 的 success。
