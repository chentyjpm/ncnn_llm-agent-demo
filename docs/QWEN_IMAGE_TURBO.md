# Qwen Image 2.1 Turbo 与 HF-Mirror

[返回项目首页](../README.md) · [下载线路](HF_MIRROR.md) · [模型中心](MODEL_CENTER.md)

## 使用

更新安装包并完全退出旧服务；刷新旧网页不会替换内置引擎。模型中心选择 **Hugging Face**，线路选择 **原站** 或 **HF-Mirror**。

先安装 **Qwen Image 2.1** 基础模型；Turbo 卡片的「先准备基础模型」会引导至基础包确认，不会自动开始大文件下载。再安装 **Qwen Image 2.1 Turbo**，此时只下载六个 Turbo Transformer 文件，约 **14.23 GB / 13.25 GiB**；共享组件不重复下载或复制。选择 Turbo 为当前生图模型后，界面自动锁定 **8 步**，执行前仍需确认。切回基础版恢复可编辑的 40 步默认值。

保留两个模型，不覆盖现有基础权重。首次基础版加 Turbo 合计约 **52.97 GB**（十进制），还需预留磁盘空间。基础包中的标准 Transformer 用于切回标准版，不能称为 Turbo 的共享文件。本版是两次明确确认的安装，不是隐式下载全部依赖。

## 引擎与目录

本轮更新图像引擎至 `14953559f39c7658e5a6cc117481e1922660cc9e`，2026-10-10 核对的上游 master HEAD。ncnn 子模块仍为 `c6b351b56fbe32e0381ae00331e3df649b20d7b7`，文字引擎不变。

上游按最终模型目录名以 `-turbo` 结尾选择固定调度，显式 `-l` 必须为 8。**标准模型设置 8 步不等于 Turbo；Turbo 不能沿用 2 步冒烟参数或 40 步默认值。** 不自动载入四步加速 LoRA，不提供与 Turbo 固定调度不兼容的 PDD 参数。

```text
用户数据/models/
├── qwenimage21/              原基础模型：分词器、编码器、VAE 等共享组件
└── qwenimage21-turbo/
    ├── transformer/         Turbo input / blocks / output，各 .param + .bin
    └── READY.json           Turbo 文件和校验过的基础依赖
```

上游自动从同级 `qwenimage21` 查找共享文件。没有符号链接、共享文件复制或原目录改写。基础文件缺失时阻止 Turbo 安装、启用或运行；安装末尾和手动启用时核对共享文件哈希。仍要求模型目录没有不可信并发写入者，不是 OS 沙箱。

参考：[上游模型准备与 Turbo 说明](https://github.com/nihui/qwenimage-ncnn-vulkan/blob/14953559f39c7658e5a6cc117481e1922660cc9e/README.md)。

## 来源与可信文件

Turbo HF 快照为 `86b205ef3d6ecd7387b7796aeceefbffb6bf1883`，仓库 `nihui-szyl/qwen-image-ncnn`。维护者从 HF 原站获取六个文件的大小/哈希，并核对了新快照的共享组件与原基础快照身份相同；可信清单保存在 `hf_download_pins.py`。

原站和 HF-Mirror 都按同一快照/可信哈希验证。镜像是线路，不是新模型；未知域名、TLS 降级、凭证、路径逃逸和静默重试仍不允许。合法重定向可能最终到原站或官方 CDN，界面显示实际响应主机，不承诺一定提速或某地区一定可达。尚未核实到匹配 ncnn Turbo 的 ModelScope 权重，不用原始 safetensors 冒充。

线路不改变设备策略，仍是每个引擎优先可用硬件 Vulkan，不可用再 CPU。Turbo 并不意味着所有算子都在 GPU 上运行。

## Agent 与独立生图

`images.generate` 工具 schema 随当前模型变化：Turbo 的 steps 限定为 `[8]`，省略则默认 8；标准版默认 40。系统提示要求遵循当前 schema，不强塞 40 步示例。HTTP 计划和命令执行前分别检查参数，2、4、40 等矛盾值直接失败，不会悄悄改参数后执行。审批和工具权限不变。

## 验收

`tests/test_image_turbo.py` 使用明确的小字节/引擎替身，覆盖身份、双线路、依赖哈希、增量安装、切换、schema、真实 HTTP 与审批；不当作模型运行。

浏览器脚本验证真实 Chromium 中的固定 8 步、确认、刷新、产物展示、切回标准版及缺依赖引导。截图中的合成图不代表 Qwen 效果。原生 CLI 额外检查 Turbo 非 8 步拒绝、目录大小写和尾分隔符。

`HF mirror downloads` 包含实际原站/镜像清单、小参数文件和约 34 MB 输出层权重下载，不能替代全模型推理。

**Real Qwen Image Turbo** 独立下载真实共享组件和六个 Turbo 文件，CPU 默认，256×256、完整 **8 步**。核对引擎 `model-type = Turbo`、`step 8/8 done`、VAE 完成、退出码与 PNG 完整解码，记录实际资源样本。可手动选择已注册的隔离 Vulkan runner，不能回退 CPU 后冒充硬件通过。

真实 Turbo CI 只省略本次不用的基础 Transformer，共享组件和 Turbo 完整校验。它经过应用 ImageRunner，但不等于 ModelHub 全量基础包的真实安装验收。每次结果看对应提交和证据，不以脚本存在、界面样本或小文件通过代替推理。无旧版性能对照、无画质或提示词一致性评分。
