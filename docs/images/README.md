# README 图片与来源

这些图片是本项目已完成的浏览器 / 原生窗口验收截图，不是效果图。原 PNG 文件未裁改，保留图中测试标记和实际读数；发布到仓库后不依赖 Actions artifact 的保留期。

| 图片 | 内容与范围 |
|---|---|
| `ui-home-dark.png` / `ui-home-light.png` | H5 深浅主题实拍；顶部明确标注模型为测试替身。 |
| `ui-chat-files.png` | 真实浏览器、工具与文件操作；模型回复为固定测试替身，HTML 字符串为注入防护样本。 |
| `ui-documents.png` | 实际文档工作台，使用合成测试文稿。 |
| `ui-approval.png` | 实际授权卡与按钮；被审批动作来自模型测试替身。 |
| `model-center-sources.png` | 实际来源选择与确认界面，使用测试元数据。 |
| `model-center-download.png` | 实际进度界面，速度与下载字节数是受控测试值，不是模型下载实测。 |
| `monitor-overview.png` | Windows 打包程序实际运行，CPU / 内存来自当次系统采样，未加载模型。 |
| `monitor-gpu.png` / `monitor-processes.png` | Windows 原生窗口布局验收，真实系统采样；不是物理 GPU 推理性能测试。 |

## 固定来源

H5 图片来自提交 `0b03f4351324783df1a37eacb3a86571b8233646` 的[浏览器验收](https://github.com/chentyjpm/ncnn_llm-agent-demo/actions/runs/36684429497)。

监控图片来自提交 `b611f8d6b4398a18467027f2b49eabb45443a93b` 的[原生窗口验收](https://github.com/chentyjpm/ncnn_llm-agent-demo/actions/runs/36688043974)与[Windows 安装验收](https://github.com/chentyjpm/ncnn_llm-agent-demo/actions/runs/36688044006)。

每张图片的原始归档路径、运行编号、artifact 编号、尺寸和 SHA-256 保存在 [PROVENANCE.json](PROVENANCE.json)。更换截图时应同步更新清单和说明，不擦除测试标记，不将监控系统占用冒充模型独占。

[返回项目首页](../../README.md#screenshots)
