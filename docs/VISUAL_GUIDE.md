# 图解 Local Agent

[返回项目首页](../README.md) · [完整架构](ARCHITECTURE.md) · [模型与生图](MODELS_AND_IMAGES.md)

封面采用用户选定的「樱花暮色中的本地智能工作站」插画。以下三张图用同一套浅粉紫、青蓝配色和小向导说明工程结构；**它们是架构示意，不是运行截图、模型生成效果或性能报告**。封面上的装饰图标与箭头不用于定义技术调用关系。

## 01 / 模块分工

![模块分工图](images/guide-modules.svg)

H5 通过本机 HTTP 与 WebApp 交换消息、任务事件和确认请求。Python 层的 WebApp 管理会话与任务，Agent 组织模型动作循环，Registry 注册和调用工具。文档读写在宿主工具层完成，不需要经过 ncnn 推理。

文字与图像是两条独立通道：`Backend → ncnn_agent_bridge → ncnn_llm` 与 `images.generate → ImageRunner → Qwen Image`。两个引擎分别使用 ncnn，不是先运行文字引擎再把它当作生图引擎的一层。文本桥接器的 JSON-RPC 是推理通信协议，不能与外部 MCP 工具协议混淆。

ModelHub 管理来源、校验、准备和启用；原生监控读取资源与任务状态并提供管理操作，不参与模型决策。窗口与 HTTP 服务属于同一应用的不同执行线程，图中两个入口不代表两个独立桌面程序。

对应代码：[web.py](../local_agent/web.py)、[agent.py](../local_agent/agent.py)、[tools.py](../local_agent/tools.py)、[backends.py](../local_agent/backends.py)、[images.py](../local_agent/images.py)、[model_hub.py](../local_agent/model_hub.py)、[monitor_ui.py](../local_agent/monitor_ui.py)。

## 02 / 工作流程

![对话、Agent 与独立生图流程图](images/guide-workflows.svg)

**对话模式**只将消息和允许的历史交给文字模型，不提供执行工具。**Agent 模式**由模型生成完整 JSON：`tool` 动作经过校验与必要确认后才执行；`final` 直接结束任务并回答。工具结果返回模型，形成可继续调用的循环。

**独立生图模式**和用户顶层 `/image` 指令是显式工作流：准备画面参数、确认、运行 Qwen Image、校验并展示实际图片，不要求先安装或调用文字模型。Agent 中的生图工具仍叫 `images.generate`，并且仍由模型选择。

实际产物必须来自成功工具结果和工作区文件；模型说“图片已生成”或给出任意链接，不足以创建可信图片卡片。普通聊天中提到“海报”，以及附件中的 `/image` 文本，不会触发这条显式路径。

对应代码：[image_tasks.py](../local_agent/image_tasks.py)、[web.py](../local_agent/web.py)、[agent.py](../local_agent/agent.py)。使用说明见[多模型与生图](MODELS_AND_IMAGES.md)。

## 03 / 安装与设备选择

![模型安装与计算设备流程图](images/guide-model-lifecycle.svg)

选择来源后先查询文件清单、大小与空间，再由用户确认下载。文件通过校验后按类型准备：受支持的官方 Qwen2.5 权重需要专用转换，已转换的原生 ncnn 包按清单部署；这不是通用模型格式转换服务。文字和生图模型分别启用，READY 完成发布前不能使用半成品。

运行时，两个引擎分别进行设备预检。默认 `device: auto` 优先可用硬件 Vulkan，无可用硬件时使用 CPU 并记录原因。预检成功不代表所有模型层都使用 GPU，INT8 等路径可能仍包含 CPU 算子；权重损坏、超时等推理错误不能靠无条件 CPU 重试隐藏。

对应代码：[model_catalog.py](../local_agent/model_catalog.py)、[model_sources.py](../local_agent/model_sources.py)、[native_models.py](../local_agent/native_models.py)、[model_hub.py](../local_agent/model_hub.py)、[device.py](../local_agent/device.py)。

## 编辑与来源

三张 SVG 含可编辑文字与向量几何，固定画布为 1600×1000；没有嵌入字体文件、JavaScript、外部图片或动态数据。PNG 可由同一脚本渲染，SVG 在 GitHub 上按阅读设备的中文字体显示。

```bash
# 仅重新生成 SVG：Python 标准库即可
python docs/render_project_guides.py --svg-only

# 同时生成本机 PNG 预览：需已有 CairoSVG 和可用中文字体
python docs/render_project_guides.py
```

图解以 `cc0b0676c4a44bfc9879e835a6b3904b23c5563e` 的结构为基线。后续流程变化，应同步修改[绘图脚本](render_project_guides.py)，而不是仅替换标题。封面来源、缩放处理和文件哈希见 [ILLUSTRATIONS.json](images/ILLUSTRATIONS.json)；已有真实运行截图保留自己的 [PROVENANCE.json](images/PROVENANCE.json)，两者不混为一类证据。
