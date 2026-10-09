# Windows + WSL 2 + Docker 测试操作

所有命令在项目根目录的 PowerShell 执行。使用 Windows 项目虚拟环境即可，无需再在 Ubuntu 中
安装一套 Python 或 Docker Engine。安装、启动引擎、成功构建镜像是三个不同步骤。

## 1. 确认 Docker 引擎

1. 打开 Docker Desktop，等待引擎启动完成。
2. Settings → General 使用 WSL 2 based engine，保持 Linux containers 模式。
3. 新开 PowerShell，检查：

```powershell
wsl --version
wsl --list --verbose
docker version
docker info --format '{{.OSType}}'
```

`docker version` 应同时显示 Client 和 Server，最后一条应输出 `linux`。
本项目会自动寻找 PATH、用户级和系统级 Docker 安装路径。若是其他自定义位置，可设置
`DOCKER_EXECUTABLE` 为完整 docker.exe 路径，不要把引号写入变量值。

## 2. 代理（需要代理的网络）

在 Docker Desktop → Settings → Resources → Proxies 配置 System proxy 或 Manual configuration。
如果有单独的 Containers proxy，设为 Same as host proxy 或相同代理，它影响镜像拉取。
当前机器此前检查到的系统代理是 `127.0.0.1:7897`，HTTP/HTTPS 两栏使用
`http://127.0.0.1:7897`；以后端口变化时以代理软件实际设置为准。Apply & Restart 后再测试。

本机遇到的一个实际问题：Desktop 已配置代理，但 BuildKit 的 CLI 认证令牌请求仍走直连。
使用脚本的 `-ProxyUrl` 可为该进程显式设置 HTTP_PROXY/HTTPS_PROXY。
这不会修改系统代理；本项目运行候选代码的容器仍为 `--network none`。

参考：[Docker 代理设置](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies)、
[Docker WSL 2 后端](https://docs.docker.com/desktop/features/wsl/)。

## 3. 一条命令执行完整测试（无需模型密钥）

已有本项目 `.venv` 时直接运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_project.ps1 -Docker -BuildImage -ProxyUrl http://127.0.0.1:7897
```

不需要代理时省略 `-ProxyUrl`。已有镜像且 Dockerfile/容器依赖未变更时省略 `-BuildImage`。
脚本依次检查依赖、检查引擎、构建镜像、运行 41→42 固定烟雾测试，再运行全部回归。
任何一步失败即停止，不能把构建失败后的旧测试结果当成本次成功。
`ExecutionPolicy Bypass` 仅作用于此 PowerShell 进程，不修改机器执行策略。

首次建立 Python 环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 4. 分步运行与结果位置

```powershell
# 本地单元回归：不需要 Docker，集成测试会标记 skipped
.\.venv\Scripts\python.exe scripts/run_tests.py

# 环境与实际容器烟雾测试
.\.venv\Scripts\python.exe code_assistant/doctor.py --probe

# 全部回归：没有 Docker 或镜像时明确失败
.\.venv\Scripts\python.exe scripts/run_tests.py --docker

# 固定模拟 Agent + 真实 Docker，生成可查看的失败→修复完整轨迹
.\.venv\Scripts\python.exe scripts/smoke_workflow.py
```

`test_results/<时间-ID>/report.json` 包含测试数、失败数、跳过数和逐测试状态。
Docker 测试包括正确/错误答案、死循环超时、输出限额、离线 LCEL、24 题全部 72 个参考用例，
以及容器非 root、禁网、只读挂载、capabilities、no-new-privileges、CPU/内存/进程数配置检查。
这些测试使用固定代码与模拟 Agent 响应，不调用付费模型。

## 5. 再运行真实模型流程

复制 `code_assistant/.env.example` 为本地 `.env`（已有文件不要覆盖），填写自己的 API Key、
服务地址和账号实际可用的模型名。生成 Agent 在宿主联网，候选代码仍在离线容器中执行。

```powershell
.\.venv\Scripts\python.exe code_assistant/main.py --task examples/add.json --strategy adaptive --max-calls 3 --max-total-tokens 30000
```

这一步才会调用真实模型。完整对照实验按 `benchmarks/README.md` 执行，建议先 `--limit 1`。
测试通过不代表真实模型修复效果已测量，更不能用模拟报告数字填写简历。

## 常见失败

| 现象 | 检查位置 |
|---|---|
| 找不到 docker 命令 | 重开终端；使用项目自动发现或 DOCKER_EXECUTABLE |
| npipe/dockerDesktopLinuxEngine 不存在 | Docker Desktop 尚未启动、正在重启或启动失败 |
| auth.docker.io / registry / TLS timeout | Desktop/Containers 代理及脚本 ProxyUrl；先排除镜像下载网络问题 |
| No such image | 先构建 code-assistant-sandbox:offline-v1 |
| 41→42 探针失败 | 查看 doctor 的 error_type/error_message，检查镜像及挂载访问 |
| model_authentication / model_permission | 本地密钥、服务地址、账号权限，不让 Coder 修复认证问题 |
| model_rate_limit / model_timeout | 服务限流或连接超时，查看 call_history；默认不会自动重试 |

## 依赖复现

宿主的直接依赖在 `requirements.in`，完整固定版本在 `requirements.txt`。
容器的 `sandbox_runtime/requirements.txt` 固定了 33 个包的传递依赖。
基础镜像允许用 `-BaseImage` 覆盖，严格复现时传入完整 digest；默认 tag 会随上游更新。
实际镜像 ID 由 doctor 和真实运行 manifest 记录。更新依赖或 runner 后必须重新构建并跑测试。
