# 独立 Qwen Image 真实生图 CI

工作流：`Real Qwen Image`（`.github/workflows/qwenimage-real.yml`）。不安装文本 Qwen，不依赖小语言模型规划，不使用随机权重或固定图片作为推理替身。

## 上游版本核对

2026-09-30 核对 `nihui/qwenimage-ncnn-vulkan` 默认分支 `master`，HEAD 为 `128530a5e8541d85ae516727314eb5dcf0109157`，与本项目两个锁文件相同；因此本次**重新获取并构建已核实的最新源码，不虚构一次版本升级**。该提交的 `src/ncnn` 子模块为 `c6b351b56fbe32e0381ae00331e3df649b20d7b7`。

这一版已含上游的内存映射权重加载、Transformer 前缀 KV cache、分阶段释放和自动 VAE 分块等实现。代码存在并不等于本项目已验证其性能收益。CI 每次记录最新 HEAD 与实际构建的锁定 SHA；后续上游更新会在 `upstream.json` 中显示不一致，不会悄悄切换到未锁定版本。依据：[上游源码](https://github.com/nihui/qwenimage-ncnn-vulkan/tree/128530a5e8541d85ae516727314eb5dcf0109157/src)。

## 如何运行

Actions → **Real Qwen Image** → **Run workflow**，分支选择 main。

| runner | 用途 |
|---|---|
| `hosted-cpu`（默认） | 普通 Ubuntu 24.04 托管 runner，明确 CPU；尝试真实模型，有资源不足/超时就如实失败 |
| `self-hosted-cpu` | 已由你配置并隔离的 Linux x64 runner，标签 `qwenimage-cpu`，适合大内存 CPU |
| `self-hosted-vulkan` | 已配置 Linux x64 + Vulkan 硬件的 runner，标签 `qwenimage-vulkan`；必须真正选中硬件 Vulkan，不能 CPU 回退或软件 ICD 冒充 |

工作流本身不创建或付费租用机器，也不自动注册 runner。自托管仅在受信任 main 手动触发，外部 PR 不运行该任务。不要把未经审查的项目代码放在有私人数据的长期 runner 上运行。

相关测试/引擎锁文件推送 main 会执行默认 CPU 验收；普通 UI/README 更新不下载大模型。并发请求会取消旧运行。全任务有 90 分钟上限，下载最多约 30 分钟，生图最多 40 分钟。

## 验收链路

1. 31 项验证器单元用例使用**明确标注的合成样本**，测试完整性/异常拒绝逻辑，不记作模型推理。
2. 查询上游 HEAD，记录锁定提交；获取带精确子模块的 Qwen Image 源码，编译原生引擎与 Vulkan 探测器，执行原有 CLI 和预检。
3. 通过现有 Hugging Face 来源适配器解析固定快照，保存完整文件清单。只下载文生图必须的 12 个文件：分词器、文本编码器、Transformer 输入/32层块/输出和 VAE 解码器；**不下载本次不使用的 vision、VAE encoder、ControlNet 或 LoRA**。这不是裁剪或缩小实际文生图模型。
4. 每个文件核对大小及来源校验值；权重使用 SHA-256。仅复用已下载且重新校验通过的完整文件，半截 `.part` 会丢弃；当前不承诺 HTTP Range 断点续传。
5. 通过应用的 **ImageRunner.run** 跑真实模型，固定 `256×256 / 2 steps / seed 42`，保存日志、参数、设备选择和每秒进程资源样本。
6. 强制匹配请求设备，核对退出码、无超时、输出文件，用 Pillow 验证 PNG 并完整解码像素，检查尺寸和全透明/近纯色异常。

**2 步只做推理通路冒烟测试，不做画质或提示词一致性验收。** 不把固定输出 hash 当成跨 GPU 的唯一正确值，不因 PNG 能打开就宣称画面内容正确。本轮不测图像编辑、LoRA、ControlNet 或 Agent 对生图工具的自主调用能力。

## 资源与证据

低分辨率减少中间计算，不会把权重缩成小模型。下载前记录实际内存、swap、可用磁盘和可读取的 cgroup 限制；磁盘不足直接失败。脚本不删除 runner 的系统 SDK，不创建 swap，不改用户机器的内核/服务设置。普通 runner 是否能跑完，以本轮报告为准；大内存机器是后续替代环境，不是预设通过结论。

产物 `real-qwenimage-<device>-<run_id>-<attempt>` 保留 14 天，只包括证据和成功生成的 PNG，不包括模型权重。关键文件：

| 文件 | 内容 |
|---|---|
| `upstream.json` | 实际查询时间、上游最新 SHA、实际构建 SHA、是否一致 |
| `model-manifest.json` | 固定模型快照、全部必要文件及校验值 |
| `resources-before.json` | 下载前实际磁盘/内存与容量门槛 |
| `download-result.json` | 每个文件的大小和校验结果 |
| `result.json` | 真模型调用结果、耗时、设备、解码检查 |
| `resources.jsonl` | 本次子进程的实际 CPU 时间、RSS、VMS 样本；RSS 不是显存，共享页可能重复 |
| `engine.stdout.log` / `engine.stderr.log` | 引擎原始输出（保留原应用输出上限） |
| `generated.png` | 本轮原始生成图，仅成功解码校验后发布 |
| `*-stage.json` | 各阶段状态；未执行不是通过 |

没有截图、退出码或日志时不得补造结果。构建成功、Vulkan 小算子成功、CPU 生图成功、硬件 Vulkan 生图成功是四个不同结论。

## 本地复现

先按原有 `ci_native.py` 流程准备锁定源码、构建并 smoke；它会产生二进制哈希与本轮源版本记录。随后：

```bash
python -m pip install Pillow==12.0.0 -r requirements-monitor.txt
python scripts/qwenimage_real_test.py upstream
python scripts/qwenimage_real_test.py prepare
python scripts/qwenimage_real_test.py download
python scripts/qwenimage_real_test.py run --binary build/ci-image/qwenimage-ncnn-vulkan --device cpu
```

Windows 使用对应 `.exe`，`--native-status` 指向该程序实际构建/预检生成的 `status.json`。Linux Vulkan 机器可将最后一行改为 `--device vulkan`。命令行参数 `--output` 指定报告目录，`--model` 指定测试权重目录；不应把它指向用户业务文件夹。
