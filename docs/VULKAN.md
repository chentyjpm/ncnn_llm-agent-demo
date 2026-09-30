# Vulkan 优先与 CPU 回退

## 默认策略

LLM 与 Image 独立使用 `device: "auto"`，无需填写 GPU 编号。通过计算预检的独显优先，其次集显、虚拟/其他硬件设备。无可用候选时使用 CPU。Lavapipe/llvmpipe/SwiftShader 等软件 Vulkan 不冒充硬件加速。

`device=cpu` 跳过探测；`device=vulkan` 是严格请求，不可用时报错；auto 才自动回退。旧版显式 `llm.vulkan=false` / `image.gpu=-1` 仍尊重为 CPU。新安装版与示例默认 auto。管理员指定 GPU 编号时，不可用不会偷偷换另一块卡。

## 两阶段、每设备隔离的真实预检

`native/device_probe.cpp` 分别链接每个引擎的 ncnn，随引擎一起安装。宿主先执行 `--enumerate`，仅初始化 Vulkan 实例、读取物理设备信息，不创建逻辑计算设备，不宣称计算成功。

然后对每个候选单独启动 `--check-device N` 子进程。只有真实创建 Vulkan 资源、执行 `BinaryOp MAX(x,0)`、得到 VkMat、下载并数值核对正确，才记录 `compute_ok=true`。原生输入同时包含负数、零和正数；不是仅检查 DLL、设备名称或 CPU 计算结果。此算子等价于 ReLU，且两个引擎的精简算子集都包含它。

设备可能枚举正常，但创建计算资源时驱动崩溃。该设备仍记录 `compute_status=failed`、退出码和诊断，不变成通过；宿主继续检查其他设备，全部不可用才选择 CPU。多次子进程的等待预算合计 15 秒（超时后的进程清理可能另需少量时间）；超出预算的设备记 `budget_exhausted`，绝不猜测其可用。

无法执行枚举程序、无效枚举协议、缺失匹配探测器仍是 `probe_unavailable` / `matching_probe_missing`，安装版自检继续失败。可用的枚举程序返回零设备，或明确记录候选计算失败，是正常的 CPU 回退路径。严格 Vulkan 模式仍拒绝这类结果。

报告缓存最多 60 秒，键包含探测文件版本及 Vulkan 驱动选择环境。计算子进程必须返回与枚举一致的设备 ID、名称、类型和硬件标记，避免设备变更时错误选择。

## 本轮 macOS 故障证据

历史提交 `ac809c8` 的安装版在 Mac ARM/Intel 上失败。诊断提交 `72e2150`、运行 `36651915208` 用原始、校验过的 native artifact 在冻结前复现：Apple Paravirtual 设备在 `vkBindImageMemory → MVKImageMemoryBinding → AppleParavirtGPUMetalIOGPUFamily` 路径崩溃，来自 ncnn 的 dummy image 初始化。

因此修复是隔离设备计算失败并回退，不是宣称修好了该虚拟驱动或该 GPU 已通过。一次性的旧二进制 LLDB 重现工作流已完成取证并移除；历史提交和原始失败运行保留。持续验收仍使用当前代码的 native、installer、工具、浏览器和真实模型工作流，不删除或跳过它们。

## 结果与错误

API `/api/runtime` 的 `devices` 是预检；最终消息的 `device_selection` 是实际调用记录。生图也包含独立选择。`probe_devices` 保留每块设备的计算状态和失败诊断。

| 情况 | 自动模式行为 |
|---|---|
| 未找到引擎 | 记录 `engine_missing`，实际调用仍报错，不冒充成功 |
| 缺少匹配探测器或枚举程序损坏 | 运行可选择 CPU，但安装版自检不能通过 |
| 一块设备计算失败 | 保留失败并检查其他设备 |
| 全部候选失败或仅有软件 Vulkan | CPU，绝不显示为 GPU 计算通过 |
| 上游因模型算子限制切回 CPU | 记录上游实际行为，不保证所有层都在 GPU |
| 权重损坏、内存不足或模型推理异常 | 明确错误，不无条件重放 Agent 或工具 |

图像 Vulkan 参数为 `-g <设备编号>`，CPU 为 `-g -1`；文本为 `--vulkan --vulkan-device <编号>`。不硬编码使用第 0 块显卡。

## 打包与 macOS

两个引擎各自带版本匹配的探测器，构建和归档均检查 SHA-256 并实际运行。macOS 包含 Vulkan loader、MoltenVK 和 ICD 配置，通过应用本地路径定位；不要求用户安装 Homebrew/Vulkan SDK。Windows/Linux 使用已有显卡驱动，应用不安装驱动或改变系统服务。

PyInstaller 可重定位或临时签名 Mach-O；清单同时保留冻结前后哈希。Mac 资源封印使用临时签名，不代表 Developer ID 签名或 Apple 公证。

## CI 范围

`tests/test_device.py` 与 `tests/test_probe_isolation.py` 用明确能力 fixture 检查选择、进程隔离、超时、错误保留、缓存、参数与严格模式，不能当 GPU 实跑证据。

`scripts/probe_regression.py` 使用实际编译字节与宿主协调逻辑。Linux 的 Mesa 软件驱动必须完成真实 Vulkan 数值计算，再指定不存在的 ICD 验证无设备回退；另执行原生非法参数退出测试。归档恢复的程序也完整复测。

`Real Qwen 0.5B CPU` 使用真实权重验证问答、文件任务和 H5 HTTP；安装版还在无可搜索 Python/ncnn 的 PATH 下验证双引擎、文档、服务、默认策略，Linux 从模型中心实际下载、转换并完成问答。

小型探测不加载完整权重，不证明物理显卡性能、长时稳定性、所有模型算子或 Qwen Image 实际生图。这些须另行用实际模型和目标硬件验收。
