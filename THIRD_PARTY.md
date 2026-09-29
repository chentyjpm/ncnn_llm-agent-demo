# 第三方依赖与版本

本压缩包不包含 ncnn、ncnn_llm、Qwen Image 的第三方源码、预编译库、模型权重或字体文件。
MIT 许可仅覆盖本项目原创编排/桥接代码；上游代码、模型及其依赖分别遵循自身许可证，不因本项目而改变。

源码接口核对日期：2026-09-29。`upstream-lock.json` 记录核对时的仓库提交。

- ncnn_llm：`301d60a1498f3be3ecef8bbcf233841e00e766ec`
  - https://github.com/futz12/ncnn_llm
  - 核对：`src/ncnn_llm_gpt.h`、`src/utils/prompt.h`、`examples/llm_ncnn_run/cli_runner.cpp`、`xmake.lua`。
  - 模型目录应包含对应版本的 `model.json`、param/bin 和分词器资产。不要把 GGUF 直接填到这个目录。
- Qwen Image：`128530a5e8541d85ae516727314eb5dcf0109157`
  - https://github.com/nihui/qwenimage-ncnn-vulkan
  - 核对：README 的 `-m/-p/-o/-s/-l/-r/-g/-i` 命令行。
  - 模型资产来源：https://huggingface.co/nihui-szyl/qwen-image-ncnn
- ncnn 模型库：上游 README 指向 https://mirrors.sdu.edu.cn/ncnn_modelzoo/
- ncnn 运行库的确切版本/ABI **尚未锁定**。它由使用者提供，须与上述 ncnn_llm 源码及转换后的模型兼容。不能保证旧版预编译 ncnn 可用；应记录成功测试后的具体版本和编译选项。
- JSON C++ 依赖：https://github.com/nlohmann/json
- MCP 实现明确限定为 `2025-11-25` 的 stdio tools 子集，兼容握手版本 `2025-06-18`、`2025-03-26`。不声称实现当前最新规范全部功能。
  - https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
  - https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- Docker 执行参数参照官方文档：https://docs.docker.com/reference/cli/docker/container/run/

导出/安装阶段可以需要联网和额外工具；运行阶段只有在二进制、模型、分词器、Python 包、Docker 镜像、MCP 程序都已经就位，且不接远程 MCP 服务时，才能保持完全离线。
