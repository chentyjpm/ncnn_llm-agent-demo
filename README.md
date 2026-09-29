# Local Agent · 本地 AI 与文档工作台

将 **ncnn 文本引擎、Qwen Image 引擎、Python 应用运行时和 H5 文档工作台**组合成一个本机应用。安装版不要求用户安装 Python/ncnn SDK、不要求填写两个引擎的路径，也不需要云端 API Key。

**两个引擎默认都是 Vulkan 优先：`device: "auto"`。** 应用分别执行与引擎版本匹配的 Vulkan 计算预检；可用则选择硬件设备，不可用则使用 CPU，并保留选择原因。不是看到电脑“有显卡”就假定可加速，也不会把软件 Vulkan 当作物理 GPU。完整策略见 [Vulkan 与回退](docs/VULKAN.md)。

> 安装包、无模型测试、真实模型推理、物理 GPU 性能是不同的验收层。请以同一提交的 GitHub Actions 结果和产物为准。源码 ZIP 不等于安装包；小型 Vulkan 预检不等于整个 Qwen Image 已完成生图测试。

## 1. 普通用户：安装后直接使用

在 Actions 中选择成功完成的 **Application installers**，下载对应系统的 `LocalAgent-installer-*` 产物。四种构建目标为 Windows x64、Ubuntu x64、macOS Apple Silicon 和 macOS Intel。

Windows 使用 `LocalAgent-windows-x64-setup.exe`，也提供便携目录 ZIP。macOS 使用 DMG 中的应用。Linux 解压后启动 `LocalAgent`，可使用包内 `install.sh` 创建当前用户安装。代码没有商业签名证书或 Apple 公证，不能把构建成功当成签名/商店发布验收。

启动之后：

```text
双击 Local Agent
    → 自动启动本机服务并打开 H5 页面
    → 安装与模型 → 查看文本模型下载量 → 明确确认
    → 自动下载、校验、转换、启用
    → 聊天 / Agent / 文档工作台
```

两个引擎随程序分发，**模型权重按需下载**。不使用生图就不必下载图像模型。模型安装前也可以读取和导出文档；聊天不可用时会明确提示，不用固定回复冒充模型。

安装版内置 Word、Excel、PPT 处理库和模型转换器。macOS 同时打包 MoltenVK；Windows/Linux 使用机器已有的显卡驱动，缺少适用驱动就自动选 CPU。应用不安装驱动、不改系统服务。

用户数据独立于程序安装目录：

| 系统 | 数据根目录 |
|---|---|
| Windows | `%LOCALAPPDATA%/LocalAgent` |
| macOS | `~/Library/Application Support/LocalAgent` |
| Linux | `$XDG_DATA_HOME/local-agent`，默认 `~/.local/share/local-agent` |

根目录内的 `models/` 是权重，`state/` 是会话和审计，`workspace/web/<会话ID>/` 是用户文件。升级程序不需要重新填写路径；卸载不主动删除这些数据。详细安装、限制和构建方法见 [安装说明](docs/INSTALLATION.md)。

## 2. H5 界面如何使用

界面提供会话侧栏、主聊天区、底部输入框、工作文件面板、文档工作台和模型中心。HTML/CSS/JavaScript 均由本机服务提供，没有 CDN、远程字体或前端构建步骤。

### 对话与 Agent

**对话**适合提问、解释代码、整理提纲、总结附件正文：直接取得模型的文本回答，不提供工具。模型显示的代码不会自动执行。

**Agent**模式才提供工具定义，要求模型输出完整 JSON 动作，由程序验证、确认和执行。文件读取/列举与 `documents.read` 属于只读操作；文件写入、文档创建、Python、外部 MCP、系统命令和生图需要逐次确认。网页无法开启管理员未授权的能力。

Enter 发送，Shift + Enter 换行；中文输入法组合输入不会误发送。支持深浅主题、历史搜索、新建、重命名、删除和 Markdown 对话导出。刷新页面可恢复记录；删除会话仅删除会话 JSON，保留工作文件和原始审计日志。

目前返回的是完整一轮模型输出，**不是逐 token streaming**。界面通过带游标的长轮询接收模型开始、工具开始、审批、结果、完成等真实事件，不用打字动画伪装流式生成。

### 文档优先的处理流程

```text
上传 docx / xlsx / pptx / md
    → 安全提取正文与表格
    → 普通对话总结，或 Agent 分页读取
    → 文档工作台中审阅/编辑 Markdown 草稿
    → 保存为新的 Word / Excel / PowerPoint / Markdown
```

| 类型 | 读取 | 导出 |
|---|---|---|
| Word `.docx` | 正文、标题、表格，保留基本顺序 | 标题层级、段落、简单列表和表格 |
| Excel `.xlsx` | 工作表、单元格值和公式文本 | 第一个 Markdown 表格或 CSV，表头、筛选、冻结行和基础列宽 |
| PowerPoint `.pptx` | 按页提取文字和表格，包括分组中的文字 | 按大纲生成可编辑文字型幻灯片，长正文分页 |
| Markdown `.md` | UTF-8 文本与分页读取 | 保存原文本、基础安全预览，也作为 Office 导出的中间格式 |

工作台支持载入最近一次回答、Word 报告/Excel 表格/PPT 大纲模板、编辑/预览切换。点击“保存为新文档”直接执行专用文档接口，**不要求模型写 Python，也不要求允许任意宿主代码执行**。

这不是在线 Office 编辑器或无损格式转换器。不会执行宏、重算 Excel 公式、保持任意复杂排版，也不识别扫描件、图片中的文字、PPT 动画或备注。旧 `.doc/.xls/.ppt` 和带宏格式不支持。原文件不覆盖，导出到当前会话的 `exports/`，采用随机前缀新名称。详见 [文档工具](docs/DOCUMENTS.md)。

### 附件、上下文和大小限制

单文件上传最多 8 MiB，每条消息最多 10 个附件；网页下载最多 16 MiB。Office 正文提取会检查压缩包成员、XML、宏/嵌入对象和展开大小。读取结果最多 200,000 字符，通过 `offset`/`next_offset` 分页，每次最多 16,000 字符。

普通对话会加入 Office 提取文本的第一页，标为不可信数据；文本附件每份最多 12,000 字符，总附件上下文不超过 16,000。Agent 模式传入相对路径，可调用 `documents.read` 分页。传统 `files.read` 仍有 1 MiB 文本限制。

H5 最多带入最近 6 个完整成功问答对，历史总量限制 24,000 字符。不是无限上下文、自动 RAG 或文档全量理解保证。图片可预览和保存，但没有因此自动连接 VLM/OCR。

### 停止和错误

一次只运行一个任务，避免模型和文件修改并发抢占资源。正在运行时不接受第二个任务或上传。点击停止会请求合作取消，尝试终止文本桥接器；已经开始的其他工具可能要等结束或超时。**停止不回滚已完成的写入或外部操作。** 服务重启将未完成任务标为中断，不自动重放。

“运行设置”展示两个引擎的预检设备/选择原因，及最近任务的实际后端设备记录。就绪检查主要确认程序和模型配置存在，不等于已经校验所有权重或完成推理。

## 3. 开发者：源码启动和配置

安装版用户不需要执行本节。源码运行需要 Python 3.10+，在完整仓库根目录执行：

```bash
python -m pip install -r requirements-office.txt
python -m local_agent serve --config configs/local.json --open
```

不配置模型也能用 `python -m local_agent serve --open` 打开页面。默认只监听 `127.0.0.1:8765`，端口可通过 `--port` 修改。`--workspace` 覆盖工作根目录，`--data-dir` 指定会话/审计目录。不能直接双击 HTML 代替本地服务。

最小自动加速配置：

```json
{
  "workspace": "workspace",
  "max_steps": 12,
  "llm": {
    "backend": "ncnn_bridge",
    "command": ["bin/ncnn_agent_bridge"],
    "model": "models/ncnn-qwen05",
    "device": "auto",
    "threads": 4,
    "timeout": 300,
    "max_new_tokens": 512
  },
  "image": {
    "enabled": false,
    "command": ["bin/qwenimage-ncnn-vulkan"],
    "model": "models/qwenimage21",
    "device": "auto",
    "timeout": 5400
  },
  "documents": {"enabled": true},
  "python": {"mode": "disabled"},
  "commands": {},
  "mcp_servers": []
}
```

Windows 使用实际 `.exe`；JSON 路径推荐正斜杠。每个引擎旁需要与其版本匹配的 `ncnn_device_probe`。没有匹配探测程序时 auto 安全选 CPU，并报告 `matching_probe_missing`；不要把另一版 SDK 的探测程序随意复制过去。

`device=auto` 优先 Vulkan，`device=cpu` 强制 CPU且不探测，`device=vulkan` 是严格请求、不可用时报错。旧配置中明确的 `vulkan:false` 或 `gpu:-1` 仍尊重为 CPU；迁移到新默认只需改为 `device:auto`。不填写 GPU 编号会自动选择，有编号偏好时不可用不会偷换别的显卡。

安装版 `python packaging/launch.py` 对应的自动启动器不读取这份 JSON，自动定位资源和用户目录。源码使用它之前需要准备打包资源，源代码本身不包含引擎二进制。

诊断与无 UI 运行：

```bash
python -m local_agent doctor --config configs/local.json
python -m local_agent run --config configs/local.json --task "列出当前工作区文件"
```

`ncnn_cli` 兼容上游 `llm_ncnn_run`，每轮重新加载，且不支持桥接器的输出 token 上限；优先使用常驻 `ncnn_bridge`。原生构建见 [NATIVE_SETUP.md](docs/NATIVE_SETUP.md)，其中旧 CPU 示例是显式 CPU 使用方法，不改变新应用的 auto 默认。

## 4. 整体架构和分工

```text
浏览器 H5 / 原生 JS
    │ 同源 HTTP：会话 / 文件 / 文档 / 审批 / 任务事件
    ▼
WebApp（本机、单用户、独立会话工作区）
    ├─ 普通对话 ──────────────┐
    ├─ 文档工作台 → DocumentTools → 新文件
    └─ Agent.run             │
          │                  │
          ▼                  ▼
     Backend：ncnn_bridge / ncnn_cli
          │ 独立版本 Vulkan 计算预检 → Vulkan 或 CPU
          ▼
     C++ 桥接器 → ncnn → 实际权重
          │
          ▼
     完整 JSON 动作 → Registry → 授权与参数检查
          ├─ files
          ├─ documents
          ├─ Python / 固定命令（默认关闭）
          ├─ MCP 客户端 → 外部 MCP Server（默认关闭）
          └─ ImageRunner → 独立预检 → Qwen Image 进程
          │
          └─ 真实工具结果 → 下一轮模型 → 最终答复
```

**H5 不做模型计算，也不直接执行 Shell。** Python 管理任务、会话、工具和 HTTP，C++/ncnn 做推理。项目不是纯 C++，不是在浏览器内用 WebGPU 推理，也不是 OpenAI ChatGPT 的代理。

两种引擎分进程是为了隔离构建依赖、内存峰值和资源生命周期，避免强行合并两个上游 ncnn 工程。文本任务中可复用权重；不同网页消息目前会重新启动后端，不是多会话常驻模型服务。生图前主动释放文本模型，之后需要总结再重新加载。

## 5. Agent 的运行逻辑

**1．装配能力。** `make_runtime()` 按配置建立工作区、注册工具，并启动明确授权的 MCP。工具包含名称、说明、schema 和执行函数；模型不拥有任意系统调用能力。

**2．构造输入。** 系统提示给出工具定义与动作协议，再加入已完成历史、本次用户任务和不可信附件数据。一次只能输出一个完整对象：

```json
{"tool":"documents.read","arguments":{"path":"uploads/example.docx","offset":0,"limit":12000}}
```

完成时：

```json
{"final":"已经处理完毕，结果保存在 exports 中。"}
```

**3．解析和限制。** `parse_action()` 拒绝重复 JSON 键、非有限数字、混合 tool/final、未知顶层字段和不完整动作，不执行部分 token。格式错误反馈模型修正，累计三次停止；同一工具动作连续重复超过两次停止；默认最多 12 轮，还有上下文字符预算。字符上限不是精确 token 上限。

**4．确认和执行。** H5 的 `ConfirmedRegistry` 对副作用工具提出一次性确认。用户批准后才由 Registry 校验工具/参数并调用；拒绝或 5 分钟超时阻止当前操作。无权工具不会因用户消息包含“允许”而被开启。

**5．反馈真实结果。** 工具成功/错误以 `TOOL_RESULT (untrusted data)` 回传。模型可请求下一页、调整参数、继续另一工具，或输出最终答案。大结果限制传输长度；原始审计可包含更多敏感内容。

**6．结束与保存。** 保存回答、工具 trace、设备选择和 JSONL 审计，释放模型进程。取消与异常也释放资源，但不回滚已有副作用。模型说“完成”并不是事实保证，业务关键结果仍需检查。

MCP 流程为 `initialize → initialized → tools/list → allowlist → tools/call`。模型只输出工具请求，MCP 协议由 Python 客户端处理。目前是 stdio tools 子集，不是完整 HTTP/OAuth MCP SDK。第三方 MCP 是宿主程序，工作区防护无法约束其内部任意文件/网络访问。

## 6. 文件与模块结构

```text
local_agent/
  cli.py                 源码命令入口、配置和运行时装配
  desktop.py             安装版自动定位、单实例、浏览器启动和自测
  model_hub.py           固定来源、明确同意、下载校验、转换和原子激活
  device.py              每引擎 Vulkan 优先选择、缓存、超时和回退原因
  web.py                 回环 HTTP、会话、任务、审批、取消和文件/文档 API
  webui/
    index.html           页面结构和文档/模型对话框
    style.css            主题与桌面/窄屏布局
    app.js               聊天、任务事件、文件和安全文本渲染
    workbench.js         Office 草稿、导出、模型中心和设备显示
  agent.py               动作解析、Agent 循环、历史、限制和审计
  backends.py            常驻桥接器/交互 CLI；明确标记的测试后端
  tools.py               工具注册与 schema 校验
  documents.py           有界 Office/XML 读取与新文档导出
  paths.py               工作区相对路径、文本读写和精确 patch
  execution.py           显式 Python 模式与管理员固定 argv 命令
  process.py             子进程、环境过滤、输出和超时限制
  rpc.py / mcp.py        stdio JSON-RPC 和 MCP 生命周期
  images.py              Qwen Image 参数、独立设备选择与结果
native/
  ncnn_agent_bridge.cpp  文本推理 JSON-RPC 桥
  device_probe.cpp       实际 Vulkan ReLU 探测，两引擎分别编译
  image/CMakeLists.txt   Qwen Image 与其同版本 probe 的构建包装
  CMakeLists.txt         文本引擎依赖、桥接器和 probe 构建
  *test.cpp              参数解析与异常展开原生回归
packaging/
  launch.py              PyInstaller 入口
  windows.iss            Windows 当前用户安装/快捷方式/卸载
  install-linux.sh       Linux 用户目录安装，不修改系统服务
scripts/
  ci_native.py           依赖锁定、构建、CLI/probe/归档校验
  probe_regression.py    真实 Vulkan 指令和无 ICD 报告验证
  package_desktop.py     封装两引擎、文档库和解释器，生成安装产物
  test_installed_app.py  清洁 PATH 下冻结程序 HTTP/Office/模型验收
  qwen05_*.py            固定官方 0.5B 导出、参考与真实推理
  web_real_smoke.py      真实模型 HTTP 历史与 auto→CPU 回退验收
  browser_web.py         Chromium 聊天交互，模型明确为 fixture
  browser_documents.py  无需模型的真实文档与模型中心浏览器验收
  run_tests.py           unittest 日志和结构化结果
model-tests/native/     ncnn FP32 与独立参考的数值比较
configs/                源码用户示例，不是安装版必填配置
ci/dependencies.json    原生依赖固定提交
upstream-lock.json      业务上游固定提交
.github/workflows/      4 平台工具/原生、真实模型、H5 和安装包 CI
docs/                   使用、架构、设备、安全和测试细节
```

上游分工：`futz12/ncnn_llm` 提供文本模型运行时；`nihui/qwenimage-ncnn-vulkan` 提供生图/编辑；本仓库提供编排与交互。未自动开放它们所有潜在的 VLM/OCR/ASR/RAG 功能。

早期 `reports/TEST_REPORT.md` 和 `MANIFEST.sha256` 保留历史交付记录，不是后续 Git 提交的实时成功证明。当前完整性以提交 SHA、同次 CI 和分发哈希为准。

## 7. 权限与数据边界

Python、固定系统命令、外部 MCP 和图像模型默认关闭；文档专用工具不依赖它们。源码管理员可按 [SECURITY.md](docs/SECURITY.md) 配置，并用 `--trust-mcp`、`--allow-commands` 等明确授权。`unsafe-host` 不是沙箱；冻结安装版不会把自身 exe 假装成 `python.exe` 去执行用户脚本。

Docker 模式需要用户另行准备可信容器环境/镜像，失败不降级到宿主执行。MCP 工具 allowlist、workspace 检查和用户确认是不同层的保护，不构成恶意代码隔离证明。

服务只监听 127.0.0.1，检查 Host/Origin/Fetch-Site、随机 API token、请求大小和同源 CSP。Markdown/Office 内容只作为数据处理，前端不执行任意 HTML。不能直接改成公网监听就安全上线。

会话日志包含用户正文、模型原文、工具参数和结果，可能敏感。升级/卸载不自动删除；不要提交真实日志。没有全局磁盘配额、多用户认证、TLS、原子 handle 级目录隔离或 Windows Job Objects。路径安全仍要求可信单写者环境。

## 8. 构建与 CI 验证

普通用户直接使用成功产出的安装程序。维护者运行 **Application installers** 一条工作流即可完成：4 个系统/架构上各编译两个引擎与 probe → 同提交/同 run 哈希校验 → 打包 Python/H5/Office → 冻结程序自测 → 实际 HTTP/Office 验收 → 生成安装程序。Windows 还执行实际安装后检查和卸载；Linux 安装程序验收显式下载、转换、启用真实 0.5B 并通过 HTTP 问答。

这是一套统一构建入口，不是一个跨系统通用二进制。签名/公证、实体显卡矩阵与完整生图不因其他测试通过而自动合格。

| 工作流 | 真实执行范围 |
|---|---|
| Cross-platform tool tests | 4 平台 Python/HTTP/文件/Office；模型和 GPU 能力使用标记 fixture |
| Native C++ build | 8 个引擎任务，真实 C++ 编译、CLI/CTest、设备 probe、归档复测 |
| Real Qwen 0.5B CPU | 固定官方权重、参考数值对照、实际问答/文件工具/HTTP、无 Vulkan 时 auto→CPU |
| H5 browser acceptance | 实际 Chromium：聊天 fixture 流程；无需模型的真实文档导出/读取和安装界面 |
| Application installers | 8 引擎任务 + 4 安装程序任务，冻结程序启动、Office、Windows 安装/卸载、Linux 真实模型 |

Linux 原生 CI 安装 Mesa 软件 Vulkan，以 `--include-software` 明确测试真正的 Vulkan 指令。自动模式仍排除该软件实现；不存在的 ICD 路径验证真实无驱动状态。**软件实现不是物理 GPU 加速或性能证据。**

本地测试：

```bash
python -m pip install -r requirements-office.txt
python scripts/run_tests.py --output-dir reports/ci/tools
python -m unittest tests.test_device tests.test_desktop -v
```

浏览器测试依赖仅用于开发：

```bash
python -m pip install playwright==1.57.0
python -m playwright install chromium
python scripts/browser_web.py --output reports/web-browser
python scripts/browser_documents.py --output reports/web-documents
```

真实模型需要真实引擎和匹配权重。0.5B 只适合短问答与明确引导任务，已有记录显示通用提示下会出现错误动作；不能由一个简单文件任务推断通用 Agent 已可靠。

## 9. 常见问题

**显示 CPU 而不是 Vulkan？** 查看运行设置中的原因。没有硬件设备、驱动不可用、匹配 probe 缺失或预检失败会在 auto 下回退。纯软件 Vulkan 不作为默认加速设备；源码旧配置明确强制 CPU 的仍然生效。

**为什么显卡预检成功，模型还是失败？** 小型 ReLU 不证明模型所有算子、显存和驱动长期稳定性。权重损坏、OOM、运行中设备丢失等错误不会被吞掉或无条件重复工具操作。

**安装后聊天不可用？** 在安装与模型中确认下载文本模型；引擎已经内置但权重按需安装。联网失败有错误提示，没有未知来源兜底。

**Word/PPT 排版为什么与原稿不完全相同？** 当前是正文提取、Markdown 编辑、新文件导出，不是原版式在线编辑。需要完整排版保持时应在原 Office 软件里处理。

**为什么 Excel 公式没有结果？** 读取保留公式文本，导出将类似公式的文本按字面保存，不执行工作簿代码，也不带公式计算引擎。

**在手机上能用吗？** 布局适配窄屏，但回环地址只能当前机器访问。远程访问要另外设计认证、TLS 和工具隔离。
