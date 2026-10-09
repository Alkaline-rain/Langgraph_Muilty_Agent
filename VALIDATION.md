# 项目验证记录

## 最新状态（2026-10-02）

第三优先项已实现：独立运行目录与原子快照、逐节点事件、代码版本导出、异常/中断记录，
模型认证/权限/限流/连接/超时/解析错误细分，环境诊断、测试脚本、操作文档及容器传递依赖锁定。

环境：Windows + WSL 2，Docker CLI 29.8.1，Linux containers，宿主 Python 3.12.10。
Docker Desktop 代理配好后，CLI 获取 Docker Hub 令牌仍直连；为本次构建进程显式设置
HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897 后构建成功。脚本现在支持 -ProxyUrl，且会恢复原进程设置。

- 镜像：code-assistant-sandbox:offline-v1。
- 实测镜像 ID：sha256:c7a03b00722473e9cf4400defe0aae36c68373a76a1152973847ddbbb3f3ad43。
- 构建时 Python 基础镜像 digest：sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016。
- doctor --probe：ready=true，真实容器输入 41、返回 42。
- scripts/run_tests.py --docker：55 项全部通过，0 失败、0 错误、0 跳过，耗时 244.511 秒。
- 包含 24 个参考任务的全部 72 个用例，以及死循环、输出限额、离线 LCEL 和容器内核配置验证。
- 内核探针确认非 root、仅 lo 网络接口、根文件系统和代码只读、NoNewPrivs=1、CapEff=0，
  256 MiB memory.max、64 pids.max、0.5 CPU quota；这是配置实际生效检查，不是 Docker 逃逸安全证明。
- 宿主 pip check 通过；PowerShell 测试脚本语法检查通过。

测试报告：[report.json](test_results/20261002T064200Z-dbd31732/report.json)。
另外，scripts/smoke_workflow.py 已实际完成两轮修复：第一轮减法实现被判 test_failure，
经固定审查/反思后第二轮加法通过 3 个真实容器用例。
示例记录：[state.json](runs/20261002T064802Z-1bab76f4/state.json)，Agent 为固定测试替身，执行器为真实 Docker。
未调用真实 LLM，本次证明工程和沙箱测试通过，不代表已经测得模型修复成功率提升。
下方为历史记录，之前的“缺少 Docker”限制已由本次验证解除。

## 第一优先项历史记录

日期：2026-10-01。

已实现：可信 JSON 用例验收、基于测试的成功条件、失败修复路由、明确终止原因；
统一静态预检、离线依赖 Dockerfile、禁止宿主执行降级、环境错误快速失败。

本地命令：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。
最终结果：28 项，23 项通过，5 项 Docker 集成测试跳过。
图测试使用真实 LangGraph，模型调用和容器执行结果由固定数据替换，不代表真实模型效果。
宿主输出限额/超时测试仅运行人工固定测试脚本，不执行任何模型生成代码。
人工固定 LCEL 示例已在宿主虚拟环境验证 FakeListLLM 接口兼容性。
`main.py --check-environment` 已验证在缺少 Docker 时退出，未调用模型。

未验证：Docker 镜像构建、Linux 容器启动、容器网络/内存/CPU限制的实际效果、真实模型调用。
环境原因：本机当前 PATH 没有 Docker CLI。完成这些验证需要可用的 Linux Docker 环境；
README 提供了镜像构建和显式启用集成测试的命令。

## 第二优先项（2026-10-01）

新增固定任务集（24 题，48 个开发用例 + 24 个最终保留用例）、single/direct/full 三组基线及
adaptive 策略。已运行 96 个模拟工作流并生成 JSONL、JSON 汇总和 Markdown 报告。
模拟故障剧本覆盖成功、语法、导入、运行、逻辑、环境错误；模拟结果不是模型能力指标。

新增错误/代码指纹、重复失败升级与终止、上下文裁剪、调用与 Token 预算、厂商 usage 记录。
单元测试验证调用前拒绝、最后一次调用结果仍验收、结构化解析失败仍计费、未知 usage 不记零、
固定基线不使用自适应重复早停、真实模式环境预检失败不调用模型等行为。

测试结果：46 项，40 项通过，6 项 Docker 集成测试跳过。
第 6 项集成测试覆盖所有 24 题参考实现的全部 72 个用例。
未调用真实模型，未得到真实成功率提升或成本降低结论；Docker 验证仍待可用环境。
