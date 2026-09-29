# Vulkan 优先与 CPU 回退

## 默认策略

LLM 与 Image 独立使用 `device: "auto"`，无需填写 GPU 编号。通过计算预检的独显优先，其次集显、虚拟/其他硬件设备。无候选时 CPU。纯 CPU Vulkan 实现（Lavapipe/llvmpipe/SwiftShader 等）不冒充硬件加速。

`device=cpu` 跳过探测；`device=vulkan` 是严格请求，不可用时报错；auto 才自动回退。旧版显式 `llm.vulkan=false` / `image.gpu=-1` 仍尊重为 CPU。新安装版与示例默认 auto。管理员指定 GPU 编号时，不可用不会偷偷换另一块卡。

## 真实预检

`native/device_probe.cpp` 分别链接每个引擎使用的 ncnn，安装在引擎旁。它初始化 Vulkan、枚举设备，并将简单网络提取为 VkMat，经 VkCompute 下载后验证 ReLU 结果；不是仅检查驱动 DLL 或设备名称。

探测在独立子进程中，最大 15 秒。崩溃、超时、无效协议等不会拖垮整个网页；auto 记录原因并使用 CPU。报告缓存最多 60 秒，键包含探测文件版本和 Vulkan 驱动选择环境。

小型预检不加载权重，不证明完整模型的所有算子、内存、性能或长时间稳定性。文本上游主要加速 decoder，词嵌入/输出头可以仍在 CPU，不是 100% GPU 计算保证。

## 结果与错误

运行设置显示两个引擎各自的预检名/原因，最近任务还显示实际后端记录。API `/api/runtime` 的 `devices` 是预检；最终消息的 `device_selection` 是实际调用记录。生图工具结果也包含独立选择。

| 情况 | 自动模式行为 |
|---|---|
| 未找到引擎 | 记录 `engine_missing`，真正执行仍失败，不冒充成功 |
| 引擎旁缺匹配 probe | `matching_probe_missing`，使用 CPU 参数 |
| 驱动/设备/计算不可用 | CPU，保留原因 |
| 只有软件 Vulkan | CPU，不显示硬件加速 |
| 文本上游主动因模型算子限制切回 CPU | 保留该行为，日志可用时记 `model_vulkan_unsupported` |
| 权重损坏、OOM、运行中设备丢失或普通推理异常 | 明确错误，不吞掉、不无条件重放整个 Agent 或工具 |

图像 Vulkan 参数是 `-g <实际编号>`，CPU 是 `-g -1`；文本对应 `--vulkan --vulkan-device <编号>`。没有手工设置的环境变量或硬编码第 0 块 GPU 依赖。

## 打包与 macOS

两个引擎各带匹配 probe。构建和归档均检查 SHA-256 并实际启动。macOS 包含 MoltenVK，子进程通过 app-local 库路径定位；普通用户无需安装 Homebrew 或 Vulkan SDK。系统显卡硬件和供应商驱动不属于应用包可保证的资源。

PyInstaller 可修改 Mach-O 重定位/临时签名，因此清单同时保留打包前校验值和冻结后实际哈希。没有商业签名、公证或性能承诺。

## CI 证据层次

`tests/test_device.py` 使用明确能力 fixture 验证优先级、不可用回退、失败设备、缓存、超时、严格模式、软件排除和两个适配器 argv；不是 GPU 实跑。

`scripts/probe_regression.py` 在 Linux 使用 Mesa 软件 ICD，明确执行真实 Vulkan ReLU；再给不存在的 ICD 路径，要求报告无设备。该探测同样对从归档恢复的实际字节复测。

`Real Qwen 0.5B CPU` 使用实际权重、auto 策略、无 Vulkan 驱动，要求 HTTP 两轮回答正确且最终记录 CPU。安装版在没有可搜索 Python/ncnn 的 PATH 下验证双引擎、文档和默认策略；Linux 还从模型中心完成真实下载、转换、激活、CPU 问答。

这些不证明物理显卡性能，也不等于已经运行完整 Qwen Image 权重生成图片。完整生图需单独的模型/显存/设备验收，不能用微小测试图代替其权重。
