# ncnn-local-agent · 本地 Agent 原型 v0.1

基于 `futz12/ncnn_llm` 与 `nihui/qwenimage-ncnn-vulkan` 的可替换后端，提供一个**确实能运行和测试的本地编排项目**，不是已经验证完成的模型推理产品。

**本包已经通过工具层测试；没有附带模型权重、ncnn 可执行文件或 C++ 依赖。原生 C++ 编译、真实模型推理、生图、GPU 和 Docker 隔离尚未完成实机验证。** 详细证据见 `reports/TEST_REPORT.md`，不要把 mock/protocol 测试当成模型性能或能力测试。

## 跨平台自动测试与编译

现已提供 Windows、Linux、macOS（Apple Silicon / Intel）的 GitHub Actions：工具层测试与真实 C++ 编译分开执行，不下载模型权重。工作流配置不代表已全部通过，**各平台当前结果以 Actions 对应提交为准**；上面的初次交付报告保持原样。使用和产物说明见 [docs/CI.md](docs/CI.md)。

## 已经实现

Python 3.10+ 标准库编排，无第三方 Python 依赖。原生推理仍在 C++/ncnn 可执行程序中，**整个项目不是纯 C++**。

```text
用户任务
  → Agent（完整 JSON 动作校验 / 轮数限制 / 日志）
    → ncnn_cli：复用上游 llm_ncnn_run，按动作启动
    → ncnn_bridge：本项目提供 C++ 常驻接口源码
    → files：列目录 / 读 / 写 / 单点文本替换
    → python.run：disabled / Docker / 显式 unsafe-host
    → system.run：管理员配置的固定 argv 命令
    → MCP stdio：真实握手 / 列工具 / 调工具 / 分页 / 超时
    → images.generate：调用 Qwen Image 文生图或多参考图编辑
  ← 工具结果反馈
  → 下一步或最终回复
```

工具返回数据、错误和生成文件可进入下一轮。MVP 没有图形界面、HTTP API、远程 MCP、完整自主编程能力保证、RAG/VLM/OCR/ASR，也没有承诺模型一定能稳定输出 JSON。未知工具不会被执行，缺失权重不会偷偷切到假模型。

## 先跑测试：无需模型、GPU 或联网

解压后，在项目根目录执行。Linux/WSL 可能需要把 `python` 换成 `python3`。

```bash
python --version
python scripts/run_tests.py
python -m local_agent doctor
```

无需 `pip install`。测试脚本会实际启动 Python/MCP 子进程，在临时工作区测试文件操作，并写入 `reports/tests.log`、`reports/test-results.json`。其中用于模型/生图适配器的进程是**明确命名的假后端 fixture**。测试套件执行包内已给出的可信测试代码；不要混淆为对不可信模型代码的隔离验证。

### 演示一条完整工作流

先阅读 `examples/demo_plan.json`。这是确定性的固定动作序列：不是大模型自主规划，也不是大模型自动纠错。

```bash
python -m local_agent demo --workspace demo_workspace --allow-unsafe-host-python
```

会实际完成：写入合成测距 CSV → 读取 → Python 故意报错一次 → 运行预置修正版 Python → 调独立 MCP 统计进程 → 读取结果 → 写摘要 → 调固定 Python 版本查询命令。输出 `distance_summary.json`、`summary.md` 和审计日志。平均值为 234 米，这只是演示合成样本，不是你的现场测距数据。

演示执行的是你能审查的包内 Python，但使用 **宿主机执行，没有安全沙箱**，所以必须额外显式授权。重复演示使用新的空目录，如 `demo_workspace_2`，不会清空或覆盖你的已有目录。

## 换成你自己的真实 ncnn

将 `configs/local.example.json` 复制成 `configs/local.json`；修改其中 `llm.command` 和 `llm.model`。优先复用已经在你电脑上单独验证能聊天的 `llm_ncnn_run`，不需要先编译本项目的桥接器。Windows 使用实际 `.exe` 路径，JSON 里可写正斜杠，如 `D:/AI/bin/llm_ncnn_run.exe`。

```bash
python -m local_agent doctor --config configs/local.json
python -m local_agent run --config configs/local.json --task "在工作区新建 hello.txt，写入：本地工作流已连接。然后读取并确认。"
python scripts/smoke_real.py --config configs/local.json
```

模型目录必须有实际匹配的 `model.json`、`.ncnn.param/.ncnn.bin` 和分词器文件。缺失就明确失败，不提供云模型或 mock 兜底。CLI 兼容层每轮冷加载，适合验证；需要性能时再构建 `native/ncnn_agent_bridge.cpp`，切换 `ncnn_bridge`。

详细编译、已有预编译 ncnn 包接入、模型路径、生图配置见 **`docs/NATIVE_SETUP.md`**。

## 启用 MCP

示例配置自带 math 服务，但默认 `enabled: false`。改为 true、保留明确的 allowed_tools，审查命令后执行：

```bash
python -m local_agent tools --config configs/local.json --trust-mcp
python -m local_agent run --config configs/local.json --trust-mcp --task "用 MCP 的统计工具计算 232、234、236 的平均值。"
```

配置示例：

```json
{
  "name": "math",
  "enabled": true,
  "command": ["@python", "-u", "@project/examples/mcp_server.py"],
  "allowed_tools": ["summarize_numbers"],
  "timeout": 30
}
```

工具名会变成 `mcp__math__summarize_numbers`。其他 **stdio** MCP Server 使用同一配置形式接入，必须自行验证兼容和权限。不能把远程 HTTP 地址直接放进 command。当前实现是 2025-11-25 tools 子集，不是完整 MCP SDK。

## 启用 Python 或系统命令

Python 默认 `disabled`。正式尝试模型代码优先 `docker` 模式：需要本机已有 Docker、对应本地镜像与工作区权限；可在网络可用的准备阶段自行执行 `docker pull python:3.12-slim`，运行时禁止自动拉取。不具备条件就报错，不自动降级。

`unsafe-host` 只有在配置中明确选择并传 `--allow-unsafe-host-python` 时才能启动，能访问宿主文件/网络，不能用于不受信任的代码。`system.run` 需 `--allow-commands`，仅允许运行配置里固定好的命令；不提供模型自由填写的 shell。

具体风险和边界见 **`docs/SECURITY.md`**。对第三方 MCP Server 的授权相当于信任一个本地程序，工作区路径检查不能隔离它。

## 配置与路径

`--config` 相对当前命令目录。配置中的 workspace/model 相对项目根目录；推荐给可执行文件、模型写绝对路径。`@python` 表示当前解释器，`@project/` 表示项目根目录。`--workspace` 命令行覆盖项相对当前命令目录。**`configs/local.json` 不会自动加载**，运行时显式传入 `--config configs/local.json`；未传配置只读取关闭高权限功能的示例配置。

日志位于 `reports/runs/`，包含代码、工具参数、文件正文等敏感内容，不要把实际业务日志提交到公共仓库。示例样本不是业务数据。

## 目录

```text
local_agent/       Python 编排、工具、权限、MCP 与后端适配
native/            C++ 常驻桥接器源码、CMake 与 xmake target
configs/           默认关闭高权限的示例配置
examples/          可运行的 MCP Server 与预置演示动作
scripts/           测试、环境/真实模型验收、可选拉取上游源码
tests/            标准库 unittest 与明确标记的假后端 fixture
reports/           本次真实测试日志、结果、演示输出与边界说明
docs/              本机接入及安全说明
upstream-lock.json 核对的上游源码提交（不是完整二进制依赖锁）
```

当前优先支持源目录运行；不要只复制 `local_agent/` 而漏掉 configs/examples。测试执行平台与真实结果以报告为准，不声称已经验证 Windows/macOS。
