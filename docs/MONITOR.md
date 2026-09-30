# 原生后台管理与静默推理子进程

## 安装后使用

更新安装包，先从旧程序退出本地服务，再覆盖安装。双击 Local Agent 会启动独立的 **“Local Agent · 后台管理”** 原生窗口；网页仍用于聊天、工具确认、模型下载和文档操作。这个监控窗口由 Tk/Tcl 绘制，不是另一个网页、WebView 或图片；关闭浏览器不影响它的采样。

可以打开网页工作台、暂停/恢复接收新任务、停止当前任务、导出诊断、最小化、退出服务。暂停不终止当前任务或正在下载的模型；停止任务是合作取消，工具结束/超时前可能要等待，已经写入的文件不回滚。关闭管理窗口时选择退出服务或最小化继续运行，不会留下一个无法找回的隐藏服务。最小化使用任务栏/系统窗口管理器，**尚无系统托盘图标**。再次双击当前安装版会向已有实例发送经过 token 校验的唤起请求。

## 修复每次推理弹命令行

`process.background_options()` 在 Windows 返回 `CREATE_NO_WINDOW` 和 `STARTF_USESHOWWINDOW/SW_HIDE`，同时保留 stdin/stdout/stderr 管道和错误码。短进程的 `run_process()`、常驻桥接器/MCP 的 `StdioRPC` 共用这一策略；Vulkan 预检、生图、固定命令和已授权的 Python 也通过这些入口运行。

不是先弹出窗口再隐藏，也没有将 stderr 丢弃或用假成功遮盖错误。不更改 POSIX 启动行为，不为模型增加管理员权限，不影响故意打开的浏览器与原生管理窗口。第三方工具若主动自行创建 GUI/控制台，不保证替它阻止所有窗口。

## 指标的含义

| 指标 | 数据来源与口径 |
|---|---|
| 整机 CPU | psutil 的系统 CPU 采样，范围 0–100%，第一帧等待第二次采样 |
| 整机内存 | 总量、可用量、使用比例；已用量显示 total − available |
| Local Agent CPU | 本程序及当前可观察子孙进程的 CPU 时间变化，除以整机逻辑核数，与整机量纲一致 |
| Local Agent 内存 | 本程序与子孙进程 RSS/Working Set 合计，可能重复计入共享页，不是精确独占内存 |
| 进程 | PID、名称、CPU、RSS、线程数、状态；不收集命令行、环境变量、提示词 |
| 模型/任务 | 配置的模型名称、实际任务状态、当前工具、后端报告的设备选择；空闲不声称模型仍在显存常驻 |
| Windows GPU | PDH/WDDM 计数器，使用语言中立的计数器路径；显存按整适配器计数，GPU 利用率取同一适配器最忙引擎 |
| Windows GPU 名称/容量 | DXGI GetDesc1，按 LUID 对应 PDH；不是按 Vulkan 设备序号强行对应 |
| NVIDIA 回退 | 已有驱动中的 nvidia-smi；整张卡利用率、显存和温度，不假称本模型独占 |
| Linux DRM | 驱动暴露时读取 sysfs 的 GPU busy 与 VRAM 字节数；不安装驱动、不修改 sysfs |
| macOS GPU | 暂未接入性能计数；显示不可用，不把 Apple 统一内存伪装成独立显存 |

Windows 有 GPU 也不一定暴露 WDDM 性能计数器。某项缺失时显示 **不可用** 而不是 0；软件/虚拟设备和驱动权限差异也可能导致缺失。共享内存不是独立显存。采集接口的 ID 不等于 Vulkan 的 GPU 编号。

CPU/内存约每秒采一次，GPU 约每 3 秒采一次；GPU 查询有超时，期间整体采样可能延后，底部显示数据年龄。初次进程采样/刚出生或已退出的短进程可能没有有效百分比。显示采集失败不改变 Vulkan 优先策略，监控不会反复调用计算预检、重启模型或为了指标开启 GPU 权限。

90 个内存中的样本用于趋势线，不自动永久保存；点击“导出诊断”才在用户指定位置写 JSON。文件含本机进程 ID、程序名、硬件和任务 ID，分享前自行检查；不含聊天正文、工具参数或登录令牌。原始任务日志仍单独保存在数据目录 `state/runs`。

## 开发与测试

源码 CLI 编排仍可不安装 psutil；桌面包包含 psutil 和 Tcl/Tk。开发者使用 `python -m pip install -r requirements-monitor.txt`。源码桌面入口是 `python packaging/launch.py`，不是普通 CLI 的 `serve` 子命令。

自动化无 GUI：`LocalAgent --no-browser --no-monitor`。原生管理窗口验收：`LocalAgent --monitor-test native-monitor.json`，创建真实窗口并实际采集本机 CPU/内存，核对当前 PID、暂停/恢复以及最小化。这个参数不执行假模型推理，也不安装权重。

- `tests/test_monitor.py`：进程、HTTP、真实内存采样；GPU 汇总测试使用明确计数器 fixture。Windows 两个用例在实际子进程中读取 GetConsoleWindow/GetConsoleCP，要求均无控制台，同时原有 RPC/错误输出测试保留。
- `Native desktop monitor`：四个系统分别运行测试并创建 Tk 窗口；Linux 使用 Xvfb 虚拟显示，不是浏览器模拟窗口。
- `Application installers`：冻结后在真实打包程序中运行 monitor-test，验证 Tk、psutil 和资源都已打入安装目录；原有双引擎、安装/卸载、Office 和真模型验收不删除。

UI/资源测试不证明物理显卡上模型推理成功；真实模型 CI 与物理 GPU 实机验证仍单独记录。没有显卡的托管 runner 只能验证“明确报告不可用”，不能声称 GPU 指标全部验收。

## 实现文件

`monitor.py`：采样线程、进程对象生命周期、不可变快照、暂停/取消控制。
`gpu_stats.py`：PDH、DXGI、nvidia-smi 与 Linux sysfs 只读采集。
`monitor_ui.py`：主线程 Tk 窗口、趋势、三个明细标签页与诊断导出。
`monitor_http.py`：桌面专用 WebApp 扩展；token 保护的只读 `/api/monitor` 和唤起 `/api/monitor/show`。网页/模型没有任意进程终止或暂停监控的接口。

依据：[Python subprocess](https://docs.python.org/3/library/subprocess.html)、[psutil](https://psutil.readthedocs.io/)、[Microsoft PDH](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw)、[NVIDIA SMI](https://docs.nvidia.com/deploy/nvidia-smi/)。新增代码不以隐藏控制台作为安全沙箱或后台提权。
