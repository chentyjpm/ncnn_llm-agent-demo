# Local Agent · 本地 AI 工作台

一个以 **ncnn 原生推理为后端、Python 为编排层、H5 为操作界面**的本地 Agent 原型。可以在浏览器里聊天，上传和查看文件，让模型提出工具调用，再由你确认执行。

界面采用熟悉的“侧边会话列表 + 主聊天区 + 底部输入框”布局，但本项目**不是 OpenAI ChatGPT 客户端，也不需要 OpenAI API Key**。HTML、CSS、JavaScript 均由本机提供，不加载 CDN、外部字体或远程模型 API。启用的第三方 MCP 服务、Python 或固定系统命令仍可能访问网络，不能将“本地界面”理解为这些工具天然断网。

> **先区分四件事：页面能打开、工具能执行、模型能推理、模型能可靠完成任务。** 它们分别测试，不能相互代替。本仓库已建立跨平台原生构建和真实 Qwen2.5-0.5B CPU 验证流程；H5 的浏览器、HTTP 和真实模型连接验证也单独保留证据。Qwen Image 实际生成、GPU 性能和 Docker 隔离没有因这些测试自动获得通过结论。

## 导航

[启动 H5](#1-启动-h5-界面) · [配置真实模型](#2-连接真实模型) · [界面使用](#3-怎样使用界面) · [整体架构](#4-整体架构) · [Agent 逻辑](#5-agent-究竟怎样运行) · [项目目录](#6-项目结构逐层说明) · [工具权限](#7-工具权限与安全边界) · [测试与 CI](#8-测试与持续集成) · [常见问题](#9-常见问题)

## 1. 启动 H5 界面

需要 Python 3.10 或更新版本。推荐在完整仓库的根目录运行；Linux/WSL 的命令可能需要将 `python` 换成 `python3`。

```bash
git clone https://github.com/chentyjpm/ncnn_llm-agent-demo.git
cd ncnn_llm-agent-demo
python -m local_agent serve --open
```

打开 `http://127.0.0.1:8765`。已有本机配置时：

```bash
python -m local_agent serve --config configs/local.json --open
```

**第一次不配置模型也能打开界面**，查看布局、运行设置和会话管理；发送按钮会禁用并提示模型未就绪。不会自动下载权重，不会用固定回复冒充模型。配置好真实可执行文件和模型目录后，重启服务即可。

前端无需 Node.js、npm、Vue、React 或打包步骤；应用运行时只使用 Python 标准库。`--open` 仅打开本机浏览器。也可以手动打开终端给出的地址。

| 启动参数 | 含义 |
|---|---|
| `--config configs/local.json` | 读取真实模型和工具配置，不传则读取保守的示例配置 |
| `--port 8765` | 更换端口；端口冲突时使用另一个本机端口 |
| `--data-dir web-data` | 存放会话记录和审计日志，默认相对当前命令目录 |
| `--workspace workspace` | 覆盖工作根目录；H5 在其 `web/<会话 ID>/` 下为每段对话建独立目录 |
| `--open` | 启动后打开本机浏览器 |
| `--trust-mcp` / `--allow-commands` / `--allow-unsafe-host-python` | 管理员在启动时明确授权相应能力，详见第 7 节 |

关闭服务：在运行终端按 `Ctrl+C`。页面不是独立的 `file://` 静态网页，必须由这个本地服务提供 API。默认且只监听 **127.0.0.1**，没有开放公网或局域网监听开关。响应式布局适配窄屏，但不等于手机在同一 Wi-Fi 下能直接连接本机回环地址。

## 2. 连接真实模型

### 2.1 三样东西缺一不可

1. **完整的本仓库**：包括 Python 代码、网页资源、配置和示例。
2. **可执行程序**：`ncnn_agent_bridge`（推荐）或上游 `llm_ncnn_run`。Windows 使用对应 `.exe`。
3. **匹配的 ncnn 模型目录**：包括 `model.json`、实际 `.param/.bin` 权重和分词器数据。不是随便一个 Hugging Face 目录，更不是 `.gguf`。

`ncnn` 是推理引擎，不是模型；`Qwen` 是模型；`Agent` 是决定怎样把模型和工具串起来的程序；`H5` 只是你操作它们的界面。

### 2.2 一份可直接修改的 CPU 配置

创建 `configs/local.json`，填入你本机的真实路径。下面是完整最小配置，不含外部 MCP、Python 或系统命令授权：

```json
{
  "workspace": "workspace",
  "max_steps": 12,
  "llm": {
    "backend": "ncnn_bridge",
    "command": ["bin/ncnn_agent_bridge"],
    "model": "models/ncnn-qwen05",
    "threads": 4,
    "vulkan": false,
    "gpu": 0,
    "timeout": 180,
    "max_new_tokens": 512
  },
  "python": {"mode": "disabled"},
  "commands": {},
  "mcp_servers": [],
  "image": {"enabled": false}
}
```

Windows 示例：`command` 改为 `["D:/AI/bin/ncnn_agent_bridge.exe"]`，`model` 改为 `D:/AI/models/ncnn-qwen05`。JSON 中使用正斜杠最省事。配置中的模型、程序和 workspace 相对项目根目录；命令行 `--config`、`--data-dir` 和覆盖项 `--workspace` 相对当前工作目录。

先检查再启动：

```bash
python -m local_agent doctor --config configs/local.json
python -m local_agent serve --config configs/local.json --open
```

`doctor` 和界面“模型就绪”目前检查**程序和模型配置文件是否存在**，不是完整权重健康检查；损坏的权重、ABI 不匹配或内存不足仍会在实际推理时明确报错。

### 2.3 从哪里获取桥接器和 Qwen 0.5B

本仓库 Actions 的 `Native C++ build` 可产生对应系统的 `native-bridge-…` artifact；这些是构建/启动检查产物，不是附带权重的完整安装包。选择与你系统和架构匹配的产物，将程序放到配置的 `bin/` 路径。也可按 [原生接入说明](docs/NATIVE_SETUP.md) 自行构建。

已经有 `llm_ncnn_run` 时，把 `backend` 设为 `ncnn_cli`，`command` 指向它即可。这种兼容方式每个动作重新启动程序并加载模型，速度可能明显较慢，也不支持本界面的输出 token 上限控制。

本项目的真实 CPU 测试使用**官方 Qwen2.5-0.5B-Instruct**。权重下载与专用导出方法：

```bash
# numpy 仅用于这一转换过程，不是网页或 Agent 编排的运行依赖
python -m pip install numpy==1.26.4
python scripts/qwen05_export.py download --source models/official-qwen05
python scripts/qwen05_export.py export --source models/official-qwen05 --output models/ncnn-qwen05
```

下载实际大模型会消耗流量和磁盘。导出器固定源快照和权重 SHA-256，仅支持这个模型的结构，不是通用模型转换器。转换后的权重和原始文件合计需要数 GB 空间；不要提交到 Git。细节见 [Qwen 0.5B CPU 验证](docs/QWEN05_CPU.md)。

**0.5B 可以用于短问答和明确引导的小任务，但通用 Agent 可靠性有限。** 已有测试曾发现它输出混合的 tool/final JSON 和绝对路径；安全校验将其拒绝。补充清楚的动作格式示例后，简单的文件写入/读取流程通过。这不是任意编程或复杂业务流程都能成功的保证。

## 3. 怎样使用界面

### 3.1 普通对话与 Agent 模式

输入框左下方有两个模式：

| 模式 | 适合做什么 | 实际执行路径 |
|---|---|---|
| **对话** | 问答、解释代码、构思提纲、阅读较短的文本附件 | 网页 → 本机 HTTP 服务 → ncnn 模型 → 直接显示文本；不提供工具 |
| **Agent** | 读写文件、生成结果文件、连接已授权的 MCP 或其他工具 | 网页 → Agent 循环 → 完整 JSON 动作校验 → 用户确认 → 工具 → 再调用模型 |

想“写一段 Python 给我看看”，用普通对话即可；想“把脚本保存并实际执行”，需要 Agent 模式，以及明确开启的对应权限。普通对话中的代码块只是文本，点击复制也不会执行。

输入框支持 Enter 发送、Shift + Enter 换行，并避开中文输入法组合输入时的误发送。`Ctrl/⌘ + K` 清空当前视图进入新对话。欢迎页建议卡片只填入提示词，不自动运行。

### 3.2 工具执行过程与确认

界面显示“等待模型”“工具执行中”“已完成/失败”等事件。工具名称、输入参数和结果可以展开查看。写入文件、补丁修改、Python、系统命令、外部 MCP 和生图调用**都需要逐次确认**；只读的文件列举和读取无需确认。一次授权只放行当前请求，不会永久放行整个工具。

确认最多等待 5 分钟；拒绝、停止或超时都不执行该次工具。网页不能修改程序路径、扩大工作区或开启管理员未授权的能力。

目前后端返回完整的一轮模型输出，**不是逐 token 输出**。前端使用带游标的长轮询接收真实任务事件；不会把整段回复拆成打字动画冒充模型流式生成。大型工具运行期间，页面仍可查询进度或请求停止。

### 3.3 会话、文件与附件

左侧支持搜索、新建、切换、重命名和确认删除会话。刷新网页或重启服务后，历史记录仍保留。发送新消息时，后端最多带入最近 **6 个完整且成功的问答对**，并限制历史文本总量在 24,000 字符以内；不是无限上下文或向量知识库。

输入框的加号用于上传文件。服务器创建随机前缀文件名，避免无意覆盖。每个文件最多 8 MiB，每条消息最多附带 10 个文件。

- **对话模式**：小于等于 48,000 字节、可读的 UTF-8 附件会作为“不可信文件数据”传给模型，每份最多 12,000 字符，总附件上下文不超过 16,000 字符。
- **Agent 模式**：传入工作区相对路径，由模型请求文件工具读取。现有 `files.read` 的文本上限为 1 MiB。
- **图片/PDF/Word 等二进制文件**：可以保存、下载；图片可在文件面板预览，但不等于已经接入 VLM、OCR 或文档解析。当前文本桥接器不会自动理解图片，也不会自动抽取 PDF/Word 内容。

右上角“工作文件”提供目录浏览、文本/图片预览、下载和再次附加到消息。HTML、SVG 等内容不会作为活动页面嵌入，文件下载强制使用附件形式。网页单次下载上限 16 MiB，更大的生成文件请从本机工作目录取得。

**删除会话只删除会话记录，保留工作文件和审计日志**，界面删除前会提示。它们可由你在本机检查后手动整理。当前没有全局磁盘配额或自动保留期清理。

### 3.4 停止、失败与恢复

点击方形“停止”按钮会请求取消任务。Agent 在推理前后、工具执行前后检查取消标志；对于已启动的原生桥接器，服务会尝试终止其进程。正在执行的 Python、图像或第三方工具可能仍需等当前操作结束或达到超时，因此不是保证即时强杀所有子进程。

**取消不回滚此前已经完成的文件写入或其他工具副作用。** 服务重启会将未完成的消息标记为中断，不会偷偷重放写操作。出现错误可复制诊断、检查终端/审计日志，再重新发送；“重试”先把用户消息放回输入框，由你确认发送。

一次只允许运行一个任务，避免多个模型或文件修改并发争抢内存。正在执行时可以浏览其他会话，但不能开始第二项任务或上传新文件。

## 4. 整体架构

```text
浏览器：H5 / CSS / 原生 JavaScript
  │ 同源 HTTP API（会话、文件、任务事件、确认、取消）
  ▼
local_agent.web：127.0.0.1 本地服务
  ├─ 会话 JSON 持久化、独立工作区、单任务控制
  ├─ 普通对话 ──────────────────────────────┐
  └─ Agent 模式                            │
       ▼                                   │
    Agent.run                              │
       ├─ system prompt + 工具 schema      │
       ├─ 模型输出 → JSON 动作校验          │
       └─ 工具结果 → 下一轮推理             │
                  │                        │
                  ▼                        ▼
          NcnnBridgeBackend / NcnnCLIBackend
                  │ 子进程 / JSON-RPC stdio 或交互 CLI
                  ▼
          C++ ncnn_agent_bridge / llm_ncnn_run
                  │
                  ▼
          ncnn + 真实权重（CPU 或可选 Vulkan）

工具分支：
Registry → H5 单次确认 → files / Python / 固定命令 / MCP / Qwen Image
              │                                      │
              └─ 事件与原始日志                独立生图进程
                       ▼
            网页工具记录 + 本机输出文件
```

**为什么分进程？** 文本模型与生图的加载、内存峰值、ncnn 版本和资源生命周期不同。先用进程接口组合，比强行把两个上游仓库合并成一个库更容易定位问题。浏览器不负责模型计算，也不直接执行 Shell。

**为什么仍有 Python？** 推理在 C++/ncnn 内，Python 管理会话、JSON、工具、MCP 与 HTTP。项目不是“全 C++”，也不是“在浏览器里用 WebGPU 跑模型”。保留标准库编排，可以减少应用安装依赖；测试与模型转换使用的额外包单独安装。

## 5. Agent 究竟怎样运行

### 第一步：收集当前可用工具

`make_registry()` 始终提供工作区文件工具。只有在配置和启动授权允许时，才加入 Python、固定系统命令、外部 MCP、生图等工具。每个工具包含名称、说明、参数 schema 和 Python 调用函数。关闭的工具不会成为可调用能力。

### 第二步：把工具定义和任务交给模型

`system_prompt()` 要求模型一次只返回一个 JSON 对象。H5 还传入已完成的聊天历史；新附件在对话模式下是文本数据，在 Agent 模式下主要是文件路径。

模型需要二选一：

```json
{"tool":"files.write","arguments":{"path":"summary.md","content":"# 结果\n..."}}
```

或者完成任务：

```json
{"final":"已生成 summary.md，内容如下……"}
```

### 第三步：完整解析，而不是见到字符串就执行

`parse_action()` 会处理完整 JSON、已闭合的代码围栏/思考段，并拒绝重复 JSON 键、非有限数字、未知顶层字段或同时包含 tool/final 的动作。不会从部分 token 中猜一个命令执行，也不会自动执行自然语言里的代码。

格式错误会作为错误消息返回模型请求修正。累计三次格式错误停止；同一工具动作连续重复超过两次停止；默认最多 12 轮；还有上下文字符上限。字符预算不是精确的模型 token 窗口，模型较小时仍应缩短任务。

### 第四步：确认和执行

命令行入口按启动授权执行；**H5 会额外对有副作用的调用逐次请求确认**。确认通过后，Registry 校验工具是否注册、参数是否符合内置 schema，再调用实际函数。

`files.write` 只写当前工作区；`python.run` 由 PythonRunner 执行；`system.run` 只选择管理员配置的固定 argv；MCP 由客户端调用服务器的 `tools/call`；`images.generate` 执行 Qwen Image 程序。

这些动作由宿主程序做，**不是模型自己获得了操作系统权限**。模型只是生成请求，程序决定是否接受和如何执行。

### 第五步：把真实结果送回模型

工具返回内容包含成功与否、结果或错误。Agent 将其标记为 `TOOL_RESULT (untrusted data)`，加入下一轮对话。模型可以继续请求读取结果、修正参数、调用另一个工具或输出最终答案。过大的工具返回会截断，完整原始记录仍可能保留在本机审计文件中。

### 第六步：释放模型与保存会话

一个任务中的桥接器可保持权重常驻；整个请求结束后释放。**目前不同网页消息之间会重新启动后端，不是跨会话共享的长期模型服务。** CLI 兼容后端更简单，每个动作都冷启动。

遇到生图动作，Agent 先释放文本模型进程，再运行图像程序，降低同时常驻的内存压力；之后需要文本总结时再加载 LLM。

### MCP 放在哪里？

```text
MCP Server → initialize → tools/list → allowlist 筛选 → Registry
模型 JSON 动作 → Registry → MCP Client → tools/call → Server
结果 → Agent → 模型的下一轮输入
```

模型不需要自己实现 MCP 协议。当前支持 stdio tools 子集，不是远程 HTTP/OAuth 或完整 MCP SDK。第三方服务器本身是有宿主权限的独立程序，客户端的 workspace 检查不能约束它的内部行为。

## 6. 项目结构逐层说明

```text
local_agent/
  __main__.py            python -m local_agent 入口
  cli.py                 doctor/run/tools/tool/demo/serve 参数和配置装配
  web.py                 本地 HTTP 服务、会话、文件 API、任务、审批与取消
  webui/
    index.html           页面结构、会话侧栏、聊天区、文件与设置面板
    style.css            深浅主题、桌面与窄屏响应式布局
    app.js               API 调用、消息渲染、任务事件、上传下载与交互
  agent.py               Agent 循环、JSON 动作解析、历史输入、取消与审计
  backends.py            原生常驻桥接器/交互 CLI 适配；明确标记的测试后端
  tools.py               工具注册与参数校验，模型工具名映射到实际函数
  paths.py               单写者工作区路径防护、文本读写和精确 patch
  execution.py           Python disabled/docker/unsafe-host 与固定命令
  process.py             argv 子进程执行、环境过滤、输出/时间限制
  rpc.py                 有界、换行分隔的 JSON-RPC stdio 传输
  mcp.py                 MCP 生命周期、工具发现和工具调用
  images.py              Qwen Image CLI 参数与输出路径适配
native/
  ncnn_agent_bridge.cpp  原生模型推理桥；JSON-RPC infer/ping
  CMakeLists.txt         已安装 SDK 或锁定源码依赖两种构建方式
  cli_options*.h/.cpp    命令行参数解析及回归
  exception_unwind_test.cpp  MSVC 异常展开/析构回归
model-tests/native/      真模型 FP32 logits 与增量 KV 数值对照程序
configs/                 默认保守配置和示例；local.json 为本机私有配置
examples/                可信示例 MCP 服务、明确标注的固定演示动作
scripts/
  run_tests.py           运行工具/HTTP/CI 辅助测试并生成实际结果
  ci_native.py           锁定依赖、编译、原生测试、哈希和打包验证
  native_regression.py   ELF/PE/Mach-O 实际程序的无模型错误路径测试
  qwen05_export.py       固定官方 0.5B 快照下载和专用 ncnn 导出
  qwen05_reference.py    Transformers CPU 参考，不是推理兜底
  qwen05_cpu_test.py     真正的 ncnn 文本推理与引导式工具任务验收
  web_real_smoke.py      真 ncnn 模型通过 H5 HTTP API 的连续问答验收
  browser_web.py         Chromium 桌面/窄屏交互测试，明确使用模型 fixture
  smoke_real.py          已配置本机模型/可选生图的验收入口
  fetch_upstream.py      按锁文件取得上游源码，不隐式安装模型
tests/                  unittest、明确的测试替身与测试样本
.github/workflows/       跨平台工具、原生构建、0.5B 真模型与 H5 浏览器 CI
ci/dependencies.json     原生依赖固定提交
upstream-lock.json       两个业务上游的固定提交
web-data/                运行时生成：sessions/*.json、runs/*.jsonl（不入 Git）
workspace/web/<id>/       运行时生成：当前网页会话上传与输出文件（不入 Git）
docs/                    接入、安全、CI 和真模型说明
reports/                 历史测试记录；新的 CI 结果按运行保存为 artifact
```

上游职责：[`futz12/ncnn_llm`](https://github.com/futz12/ncnn_llm) 提供文本推理运行时；[`nihui/qwenimage-ncnn-vulkan`](https://github.com/nihui/qwenimage-ncnn-vulkan) 提供图像生成/编辑程序。本项目负责它们之间的编排与交互，不重新训练权重，也不把上游全部能力自动暴露到 H5。

`MANIFEST.sha256` 是早期交付包的历史清单，不代表后续每个 Git 提交的完整内容；当前完整性以 Git 提交 SHA 和 CI 产物哈希为准。`reports/TEST_REPORT.md` 保留初次交付的历史状态，不应用其“尚未编译”描述覆盖后来已有的原生 CI 证据。

## 7. 工具权限与安全边界

### Python

默认关闭。将配置改为 `"python":{"mode":"docker"}` 可使用本机事先准备好的 Docker 镜像；工具运行时禁止自动拉取，并设置断网、只读根文件系统等限制。**容器隔离仍需实机验证，不等价于虚拟机安全保证。**

`unsafe-host` 必须同时在配置选择并在启动时传 `--allow-unsafe-host-python`，会执行有宿主权限的代码，不适合不可信内容。H5 即使显示确认按钮，也不能把宿主 Python 变成沙箱。

### 固定命令与 MCP

系统命令由配置中 `commands` 定义 argv 数组，启动时加 `--allow-commands`；模型不能自由填 Shell 文本。构建项目等固定命令依然可能执行项目脚本，使用前必须审查。

MCP 要同时配置 `enabled: true`、明确 `allowed_tools`，并使用 `--trust-mcp` 启动。示例：

```json
{
  "name": "math",
  "enabled": true,
  "command": ["@python", "-u", "@project/examples/mcp_server.py"],
  "allowed_tools": ["summarize_numbers"],
  "timeout": 30
}
```

工具名变为 `mcp__math__summarize_numbers`。外部服务器进程在任务装配阶段启动，调用工具时再逐次确认；启动授权意味着你已信任这个程序。

### 图像生成

H5 不包含模型权重。需要另行配置 `image.command`、`image.model`、`image.enabled`。文本 CPU 使用 `llm.vulkan=false`；Qwen Image CPU 使用 `image.gpu=-1`。生图模型远大于 0.5B 文本模型，CPU 内存与耗时需要另外验收。

`images.generate` 支持 prompt、output、width、height、steps、seed、references；由 Agent 发起并确认后执行。当前没有独立的生图参数编辑器、LoRA/ControlNet 图形面板或自动视觉复审。

### H5 自身

采用回环监听、Host/Origin 检查、每次服务启动的随机 API token、同源请求和 CSP。浏览器只展示安全构造的文本/基础 Markdown，不执行模型 HTML 或自动加载外部图片。会话和工作文件不放在公共静态目录中。

这仍是 **单用户、本机原型**：不是多租户产品，没有账号体系、TLS 或生产网关，也未实现 OS 级路径竞争隔离、磁盘配额、Windows Job Objects 等完整安全能力。`http.server` 官方亦不建议直接用于生产公网服务。不要以管理员身份运行，不要暴露公网，不要让不同实例同时写同一个 data-dir。详见 [安全说明](docs/SECURITY.md) 与 [H5 API](docs/WEB_UI.md)。

## 8. 测试与持续集成

### 本地无模型测试

```bash
python scripts/run_tests.py --output-dir reports/ci/tools
python -m unittest tests.test_web -v
```

HTTP 测试会启动真实本地服务器，执行真实文件读写、上传下载、审批、取消、会话持久化和权限拒绝；**推理回复使用明确标注的测试替身**，不算真实模型验收。

### 浏览器测试

```bash
# 以下依赖仅用于测试，不是应用运行的必需安装
python -m pip install playwright==1.57.0
python -m playwright install chromium
python scripts/browser_web.py --output reports/web-browser
```

测试实际 Chromium 与本地 HTTP 交互：欢迎页、主题、设置、发送、历史、附件、工具审批、文件下载、重命名、导出、刷新恢复、停止、搜索、删除、窄屏布局和 HTML 注入防护。输出截图和机器可读报告。窄屏是浏览器视口模拟，不等于所有真实手机、Safari 或 Firefox 已验证。

### CI 分层

| 工作流 | 范围 | 不能据此声称 |
|---|---|---|
| `Cross-platform tool tests` | Windows、Ubuntu、macOS ARM/Intel 的工具与 HTTP 标准库测试 | 已执行真实模型 |
| `Native C++ build` | 4 平台 × bridge/image，编译、链接、CTest、CLI 与归档复测 | 已完成 GPU 或生图 |
| `Real Qwen 0.5B CPU` | 官方权重哈希、专用转换、独立参考对照、真实 CPU 问答/工具和 H5 HTTP 连续问答 | 所有模型、所有平台推理都可靠 |
| `H5 browser acceptance` | Chromium 真实网页交互、桌面/窄屏截图；模型是标注的 fixture | 这些截图是模型能力证明 |

所有结果以**同一提交**对应的 Actions 日志和 artifact 为准。运行中、失败、跳过不能记为成功；没有用 `continue-on-error` 隐藏失败。CI 产物通常保留 14 天，不包含权重和用户业务资料。

## 9. 常见问题

**页面打不开？** 检查终端服务是否仍在运行，使用显示的 `http://127.0.0.1:端口`，不要双击 HTML，也不要输入 HTTPS。浏览器策略或防火墙可能限制本机访问。

**“模型未就绪”？** 查看 `llm.command`、`llm.model/model.json` 是否真实存在。配置改动后重启。不要把程序名称或模型目录写成不匹配的示例路径。

**界面显示就绪但推理失败？** 就绪不是完整加载验收。检查实际错误、依赖 ABI、权重完整性、内存、线程和模型格式。不存在自动云端回退。

**想看长文章却只返回一半？** 在运行设置提高输出 token 上限，或将任务拆小。只有 `ncnn_bridge` 支持这个配置，0.5B 模型的能力和上下文也有限。

**Agent 卡在确认？** 展开当前消息的授权卡，审查参数后选择允许或拒绝。刷新后会重新读取当前任务事件，仍可处理未过期请求。

**Agent 格式错误？** 这是模型没有遵循动作协议，不是前端替它补一个危险调用。用更短的任务、明确的相对路径和格式示例；更复杂的任务需要能力更强且兼容的模型。

**上传了 Word/PDF，为什么不能直接总结？** 当前没有文档解析器。先转为 UTF-8 文本，或在明确授权的工具环境中增加解析能力。不能把文件上传成功当作内容已经理解。

**能在另一台电脑或手机访问吗？** 当前服务器只监听本机。要做远程服务需要单独设计身份认证、TLS、网络边界和工具隔离；不能仅靠改成 `0.0.0.0` 就安全上线。
