# 第三方依赖与版本

本压缩包不包含 ncnn、ncnn_llm、Qwen Image 的第三方源码、预编译库、模型权重或字体文件。
MIT 许可仅覆盖本项目原创编排/桥接代码；上游代码、模型及其依赖分别遵循自身许可证，不因本项目而改变。

源码接口核对日期：2026-10-13。`upstream-lock.json` 记录核对时的仓库提交。

- ncnn_llm：`0538ae8b9e2d47f597ba3491e35d2f610c3a3114`
  - https://github.com/futz12/ncnn_llm
  - 核对：`src/ncnn_llm_gpt.h`、`src/utils/prompt.h`、`examples/llm_ncnn_run/cli_runner.cpp`、`CMakeLists.txt` 与 `src/kernel/`。
  - 模型目录应包含对应版本的 `model.json`、param/bin 和分词器资产。不要把 GGUF 直接填到这个目录。
- Qwen Image：`14953559f39c7658e5a6cc117481e1922660cc9e`
  - https://github.com/nihui/qwenimage-ncnn-vulkan
  - 核对：README 的 `-m/-p/-o/-s/-l/-r/-g/-i` 命令行。
  - 模型资产来源：https://huggingface.co/nihui-szyl/qwen-image-ncnn
- ncnn 模型库：上游 README 指向 https://mirrors.sdu.edu.cn/ncnn_modelzoo/
- ncnn 运行库固定于 `ci/dependencies.json`：文字 bridge 为 `5d1c5ad4fd4b4ba32794000cd77a19ba7145d987`，Qwen Image 内嵌版本仍为 `c6b351b56fbe32e0381ae00331e3df649b20d7b7`。两个引擎分别构建匹配的硬件探测器；不能混用旧版 SDK 的内部头文件。
- JSON C++ 依赖：https://github.com/nlohmann/json
- MCP 实现明确限定为 `2025-11-25` 的 stdio tools 子集，兼容握手版本 `2025-06-18`、`2025-03-26`。不声称实现当前最新规范全部功能。
  - https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
  - https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- Docker 执行参数参照官方文档：https://docs.docker.com/reference/cli/docker/container/run/

导出/安装阶段可以需要联网和额外工具；运行阶段只有在二进制、模型、分词器、Python 包、Docker 镜像、MCP 程序都已经就位，且不接远程 MCP 服务时，才能保持完全离线。


MiniCPM 临时构建来源：官方 [ncnn_llm PR #58](https://github.com/futz12/ncnn_llm/pull/58)，
`refs/pull/58/head`，精确提交 `a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60`；
正式上游基线仍为上述 `0538ae8…`。PR 尚未合并时不将该提交描述为正式上游发布。
详见 `docs/CI.md` 的双锁校验和更新审核规则。
