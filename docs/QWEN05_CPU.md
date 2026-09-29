# Qwen2.5-0.5B 真实 CPU CI

`.github/workflows/qwen05-cpu.yml` 在普通 `ubuntu-24.04` 托管 runner 执行，不需要自托管机器、GPU、HF token 或仓库 secrets。可手动运行；相关代码推送 main 也触发。不会对外部 PR 自动下载和执行模型。

## 固定模型，不冒充 0.5B

使用官方 `Qwen/Qwen2.5-0.5B-Instruct`，快照固定为 `7ae557604adf67be50417f59c2c2f167def9a775`。
原始 `model.safetensors` SHA-256 为 `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe`。
不是 Qwen3-0.6B，不是随机权重或 ScriptedBackend。权重仅保存在 runner 工作区，不提交 Git，也不上传到测试报告。

该模型没有在 ncnn_llm 的镜像列表中提供现成目录，本仓库追加专用导出器：直接读取官方 safetensors，将 BF16 源权重无损扩展为 FP32，写入 ncnn 图与权重。使用原生 Gemm、RMSNorm、RotaryEmbed、SDPA/KV cache 和共享的词嵌入/输出矩阵。不使用 pickle、不执行模型仓库 Python，不把 GGUF 伪装成 ncnn。
这只支持该快照的固定结构，不是通用导出器。

## 验收分层

1. 模型、形状、文件哈希与导出器测试。
2. 用官方 Transformers + CPU PyTorch 生成独立参考向量（仅作为数值基准，不是推理兜底）。
3. 从锁定的 C++/ncnn 源码构建项目桥接器和数值测试程序。
4. ncnn CPU FP32 对照参考：3 个提示词，每个最多 4 个 token 步，包含整段 prefill 与增量 KV。要求 top-1 token 相同、有限数值、top-8 logits 最大绝对差小于 0.04。
5. 项目真实 `NcnnBridgeBackend`：算术、英文问答、中文问答；保存实际输出。此桥接器保留其原有 BF16 decoder 设置，与 FP32 数值测试单独记录。
6. 原有 Agent + 两个工作区文件工具：由模型生成动作并创建、读取随机校验内容。必须同时看到成功的 write/read 审计事件、正确磁盘文件、结束动作；空口宣称完成不能通过。

每一步失败导致 CI 失败，下一次修复需重新验证。模型有能力局限；一项简单文件任务通过不等于任意任务都可靠。

## 依赖与设备

编排层仍只需 Python 标准库。numpy/CPU torch/transformers 等仅用于这个 CI 的转换与独立参考阶段，在 workflow 中固定版本。
ncnn 二进制可以编入 Vulkan 支持，但运行明确不传 `--vulkan`；验收同时要求日志出现 `Vulkan disabled, using CPU only`。数值测试程序显式关闭 Vulkan、BF16 与 FP16 计算/存储。

生成图片不在本工作流中运行，也不据此宣称 Qwen Image 已通过 CPU 测试。

## 证据

Actions artifact `real-qwen05-cpu-<run_id>` 包含当前运行的源权重清单、ncnn 导出清单、数值对比、模型实际回答、Agent 原始输出与工具记录。仅包含本次合成测试数据，不包含用户业务文件。结果以该提交的 Actions 状态与 artifact 为准，不以本文作为成功证明。
