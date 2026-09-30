# 原生后台管理与静默推理子进程

## 安装后使用

先退出旧程序的本地服务，再覆盖安装。双击 Local Agent 会启动独立的 **“Local Agent · 后台管理”** 原生窗口；网页仍用于聊天、工具确认、模型下载和文档操作。监控窗口由 Tk/Tcl 绘制，不是网页或 WebView；关闭浏览器不影响采样。

管理窗口支持：打开网页工作台、暂停/恢复接收新任务、停止当前任务、导出诊断、最小化、退出服务。暂停不会终止当前任务或模型下载；停止是合作取消，工具结束/超时前可能需要等待，已完成的文件写入不回滚。关闭窗口时可选择退出服务或最小化继续运行。最小化使用系统任务栏，**尚无托盘图标**。再次双击安装版会向已有实例发送 token 保护的唤起请求。

## 命令行窗口修复

`process.background_options()` 在 Windows 使用 `CREATE_NO_WINDOW` 和 `STARTF_USESHOWWINDOW/SW_HIDE`，同时保留 stdin/stdout/stderr 管道、异常和错误码。短进程 `run_process()` 与桥接器/MCP 的 `StdioRPC` 共用这一策略。Vulkan 预检、生图、固定命令和已授权 Python 均通过这些入口执行。

这不是先弹出再隐藏，也不丢弃 stderr；不修改 POSIX 启动行为、不增加权限，不影响有意启动的 GUI。第三方程序若主动另建窗口，不能保证阻止它的所有窗口。

## 指标含义

| 指标 | 来源和范围 |
|---|---|
| 整机 CPU | psutil 系统采样，0–100%；第一帧等待有效采样 |
| 整机内存 | 总量、可用量、比例；已用量为 total − available |
| 本程序 CPU | 本程序及当前子孙进程，按整机逻辑核数归一化 |
| 本程序内存 | RSS/Working Set 合计，可能重复计入共享页，不是精确独占内存 |
| 进程 | PID、名称、CPU、RSS、线程数、状态；不采集命令行与环境变量 |
| 模型/任务 | 模型名、任务阶段、当前工具、后端报告的设备选择；空闲不声称模型仍常驻 |
| Windows GPU | PDH/WDDM 语言中立计数器；按适配器汇总，利用率取最忙引擎 |
| Windows 名称/容量 | DXGI GetDesc1 按 LUID 对应 PDH，不按 Vulkan 设备序号强行匹配 |
| NVIDIA 回退 | 已有 nvidia-smi；整张卡利用率、显存与温度，不是模型独占 |
| Linux DRM | 驱动提供时读取 sysfs 的 GPU busy、VRAM；不安装或修改驱动 |
| macOS GPU | 暂未接入性能计数；明确不可用，不把统一内存当独立显存 |

某项不可读取时显示 **不可用**，不伪造为 0。共享内存与专用显存分别展示；应用显存只在可按进程归属的计数器存在时显示。硬件、虚拟设备、驱动、权限均影响可用性，托管 runner 没有 GPU 指标并不表示实现已在所有显卡验证。

CPU/内存约每秒采集，GPU 约每 3 秒采集；GPU 查询超时会延长整体采样间隔，底部显示数据年龄。新建、退出或无法读取的进程可能导致部分统计缺失。监控不执行重复 Vulkan 计算预检、不重启模型，也不改变 Vulkan 优先/CPU 回退策略。

趋势最多保留 90 个内存样本；诊断 JSON 仅在用户选择导出后写入。包含硬件信息、PID、程序名、任务 ID，不包含聊天正文、Cookie 或工具参数。分享前仍应检查。原始任务日志独立保存在本机 `state/runs`。

## 开发与验证

源码 CLI 可不安装 psutil；安装版包含 psutil 和 Tcl/Tk。开发环境：`python -m pip install -r requirements-monitor.txt`。桌面入口：`python packaging/launch.py`，不是普通 `serve`。

自动化无 GUI 使用 `LocalAgent --no-browser --no-monitor`。`LocalAgent --monitor-test native-monitor.json` 会创建真实 Tk 窗口，实际采集 CPU/内存、核对本机 PID、暂停/恢复和最小化，并尽可能保存实际窗口截图；不加载模型、不注入假 GPU。

`tests/test_monitor.py` 包含真实子进程/HTTP/内存采样测试；GPU 汇总解析使用明确计数器 fixture。Windows 两个测试在真正的 worker/RPC 子进程中检查 GetConsoleWindow 为空、stdout/stderr 仍为 PIPE，保存原始 JSON。GetConsoleCP 仅是代码页诊断，不是窗口可见性。

`Native desktop monitor` 在 Windows、Linux、Mac ARM/Intel 分别运行；Linux 使用 Xvfb。`Application installers` 在冻结程序中再次执行 monitor-test，证明 GUI 与采样依赖进入应用，同时保留双引擎、Office、HTTP、模型和安装/卸载验收。不以 Linux 测试代替 Windows，也不以 UI 测试代替物理 GPU/模型推理。

## 首轮 Windows 测试更正

提交 `f4f7985` 在实际 Windows 测试中返回空窗口句柄，但 GetConsoleCP 返回 437，导致“代码页必须为零”的错误断言失败。复测改为核对没有控制台窗口、输出仍是 PIPE，并保留代码页。未修改静默启动实现、未跳过 Windows 测试。代码页不是窗口可见性的指标，首轮失败记录保留。

## 模块

`monitor.py`：采样线程、进程生命周期、快照、暂停/取消。
`gpu_stats.py`：PDH、DXGI、nvidia-smi、Linux sysfs 只读采集。
`monitor_ui.py`：Tk 主线程窗口、趋势、明细标签页、诊断。
`monitor_http.py`：桌面专用扩展，token 保护的 `/api/monitor` 和 `/api/monitor/show`；没有任意进程终止接口。

依据：[Python subprocess](https://docs.python.org/3/library/subprocess.html)、[psutil](https://psutil.readthedocs.io/)、[Microsoft PDH](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw)、[GetConsoleWindow](https://learn.microsoft.com/en-us/windows/console/getconsolewindow)、[GetConsoleCP](https://learn.microsoft.com/en-us/windows/console/getconsolecp)、[NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/)。隐藏控制台不代表安全沙箱或提权。
