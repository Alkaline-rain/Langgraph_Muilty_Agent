# v2 升级交付记录

日期：2026-10-13。工程升级已落地并完成以下验证；真实付费模型验收尚待明确授权，不能宣称模型效果提升。

## 已实现

| 模块 | 实现与入口 |
|---|---|
| 共用内核 | 包化导入；新 CLI/API/Worker 使用 TaskService，旧 CLI 和四策略评测保留 |
| 任务协议 | 导入候选、诊断/可信修复区分、原始代码保留、可信答案与模型视图隔离 |
| Agent | 模型选择计划/动作，依赖与权限校验、重规划、重复动作限制、统一模型预算、人机中断 |
| Skill | 四个内置能力包、注册/版本/hash、按需加载、输入/输出 schema、工具白名单 |
| 持久化 | PostgreSQL + Alembic；LangGraph checkpoint；动作日志、事件序号、代码产物 |
| 记忆 | 验证门禁、来源、镜像/Skill 环境过滤、全文/词项与 pgvector 余弦融合、删除、降级 |
| 后端 | FastAPI、本地令牌、请求大小和频率限制、幂等提交、SSE 补拉、任务取消与恢复 |
| Worker | 独立进程、单 Worker 全局限制、任务租约/心跳/fence、不确定动作停止而非盲目重放 |
| Web | React/TypeScript、Monaco 编辑/diff、任务/计划/事件、补充输入、Skill 与记忆查看 |
| 插件 | MV3 侧栏、页面代码块/选区提取、人工预览提交、结果轮询、重新打开恢复、复制/下载 |
| 评测 | 旧四策略保留；v2 消融入口默认预算预览，冻结记忆与 holdout 隔离 |

## 实测证据

- **70 项 Python 回归全部通过，0 失败/错误/跳过，252.682 秒**：包含原 55 项、新平台 12 项、PostgreSQL/Docker 3 项。[完整报告](../test_results/20261013T120248Z-dbd5c0f5/report.json)
- 后续 Skill schema、上下文压缩、diff 统计和全文检索修改另做 **15 项平台定向回归通过**，`pip check` 无依赖冲突。
- 新增 **2 项 v2 评测入口测试通过**：默认预算预览不调用模型/沙箱，未知用量不计为零。
- **3 项 Playwright 浏览器测试全部通过**，38.5 秒：工作台→真实 API→真实 Docker；动态 DOM 提取；实际加载 MV3 插件→提交→修复→重开恢复。
- PostgreSQL/checkpointer 的关闭重开、中断恢复、租约 fencing、pgvector 实际 SQL 与 namespace 隔离均已运行。
- 固定 Agent + 真实 Docker + PostgreSQL 的失败后重规划修复通过：[记录](../test_results/platform-af183d70-0a1f-4fc3-ad04-4ee14c09f915/report.json)。
- Web 与插件 TypeScript 检查和构建通过。Monaco 体积较大，当前是功能优先的本地工作台，尚未做按语言裁剪。
- [工作台截图](../test_results/platform-workbench.png)、[插件截图](../test_results/platform-extension.png)。截图中的模型行为是测试替身，不是付费模型实测。

测试中修复了 Docker `--rm` 与显式清理竞争产生的误报，并添加确定性回归；也修复了扩展页面作为标签页打开时的消息来源校验兼容问题。

## 与原设计相比的具体取舍

1. 新平台 CLI 与 API 共用服务；旧 CLI 作为独立基线保留，没有强行替换所有旧图入口。
2. 计划与 Skill 版本分别存在 checkpoint/事件、registry/hash 中，未创建冗余 plans/skill_versions 表；代码产物保存在数据库文本列。
3. 经验默认仅保存已验证条目，不提供未验证经验候选的人工晋升界面。向量以 JSON 保存、查询时转 pgvector 做精确距离计算，未建 ANN 索引。
4. embedding 接口已实现，默认关闭；实际检索 SQL 用确定性向量替身验证。外部 embedding 的用量尚未并入模型预算账本，不宣称总费用已精确控制。
5. 插件用轮询恢复结果，Web 用 SSE；插件显示原代码与最新候选，详细 Monaco diff 在工作台。
6. 状态恢复针对节点 checkpoint 与已完成动作结果。不确定模型动作停止为 unknown_action_outcome，不承诺 exactly-once 或自动续跑所有故障。
7. 网站适配由 DOM 固定样例验证；没有操作用户已登录的真实对话站点，所以不能保证站点当前布局全部兼容。

## 尚未完成的效果验收

- 一题真实 DeepSeek 调用：自动审批以“需要明确授权发送的数据、外部目的地和付费范围”为由拒绝，尚未执行。拟发送人工加法要求、错误减法代码及修复上下文至 api.deepseek.com，单题最多 9 次调用、150000 预算 Token（估计非账单硬上限）。
- 大批量真实模型对照与统计结论：尚未运行。
- 外部 embedding 模型的质量/费用与真实站点演示：尚未验证。

启动与使用请看 [PLATFORM.md](PLATFORM.md)。大纲中的 M7 仍保留待完成状态，不以固定替身的工程验证替代真实模型能力验收。
