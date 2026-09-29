# 接入真实 ncnn：两条入口

## A. 复用已有 llm_ncnn_run：不需要编译本项目的桥接器

这是 `configs/local.example.json` 默认的 `ncnn_cli` 后端，遵循已经核对的上游交互式 CLI：

```bash
/path/to/llm_ncnn_run --model /path/to/qwen3_0.6b --threads 4
```

先确认该命令在你的机器上能够正常聊天。Windows 可执行文件通常为 `.exe`。随后复制示例配置为 `configs/local.json`，将 `llm.command` 的第一个元素换成你的绝对路径、`llm.model` 换成模型目录。GPU 开关为 `llm.vulkan` 与 `llm.gpu`。

```json
"llm": {
  "backend": "ncnn_cli",
  "command": ["/absolute/path/llm_ncnn_run"],
  "model": "/absolute/path/qwen3_0.6b",
  "threads": 4,
  "vulkan": false,
  "gpu": 0,
  "timeout": 180,
  "max_new_tokens": 1024
}
```

`ncnn_cli` 每个动作重新启动进程，带入完整对话，用 JSON 转义保留换行，再解析 CLI 回复。会冷加载模型，性能不是目标；上游 banner/输出变化可能破坏解析。该 CLI 没有在核对版本中暴露 max_new_tokens 参数，配置的 `max_new_tokens` **仅对 ncnn_bridge 生效**，CLI 模式只能靠进程 timeout 控制最坏时间。这是过渡接口，不是推荐的常驻服务架构。

注意：这里不提供一个捏造的 `--json`/HTTP 接口，也不把文件名 `.gguf` 当作可加载的 ncnn 目录。权重/分词器/导出结构需要符合上游实际格式。

## B. 常驻 C++ 桥接器：更合适的后续形态

`native/ncnn_agent_bridge.cpp` 使用已经核对的接口 `ncnn_llm_gpt::prefill`、`generate`、`GenerateConfig` 和 `apply_chat_template`。输入/输出是 JSON-RPC stdio，仅承担推理；Agent 在进程外解析结构化动作、校验权限、调用工具。

本桥接器使用“JSON 动作提示词＋外部校验”，**没有开启上游自动 native tool_callback 执行**。因此工具成功率需要实际模型检验，不能由 API 存在推断稳定性。MVP 仅传文本消息，不提供 VLM 图像输入、OCR、embedding、ASR 或图像质量复审。

模型权重常驻；每次请求重建当前对话 KV，而不是实现高性能增量多会话缓存。生图调用前主动关闭 LLM 进程，之后再加载，避免两个大模型同时常驻造成内存压力。

### 使用已有的预编译 ncnn 安装包

你已有的包需要包含 `ncnnConfig.cmake`、匹配的头文件和依赖库。新 ncnn_llm 使用的新算子未必存在于旧包。CMake 使用导出 target 来获得 include/link 路径，不再硬写 `ncnn/net.h` 这种容易与预编译包布局冲突的 include。

```bash
cmake -S native -B build/native \
  -DNCNN_LLM_SOURCE_DIR=/absolute/path/ncnn_llm \
  -Dncnn_DIR=/absolute/path/ncnn/lib/cmake/ncnn \
  -Dnlohmann_json_DIR=/absolute/path/json/share/cmake/nlohmann_json
cmake --build build/native --config Release -j 4
```

没有 JSON 的 CMake 配置时，可用 `-DNLOHMANN_JSON_INCLUDE_DIR=/path/containing/nlohmann` 指向包含 `nlohmann/json.hpp` 的目录。不自动联网拉取依赖。

Windows/MSVC 的 Release 输出通常是 `build/native/Release/ncnn_agent_bridge.exe`；生成器不同路径可能不同，填写实际生成文件。`configs/bridge.docker.example.json` 的路径是 Linux 示例，需要按实际结果修改。本次尚未完成上述 C++ 的真实编译/链接；源码接口核对与 Python 协议测试不能替代 C++ 编译测试。

### 使用已经能正常构建的 xmake 工程

可把 `native/ncnn_agent_bridge.cpp` 复制到现有 ncnn_llm 的 `examples/`，备份 `xmake.lua` 后追加 `native/bridge_target.xmake.lua` 的 target 块，然后 `xmake build ncnn_agent_bridge`。这会使用现有工程的依赖解析方式，可能需要联网下载依赖。脚本不会自动改动你的已有仓库。

### 获取核对过的源码版本（可选，需要联网）

```bash
python scripts/fetch_upstream.py ncnn_llm --directory third_party/ncnn_llm
```

脚本只向新目录拉取 `upstream-lock.json` 中的提交，不下载权重、不初始化第三方子模块、不编译、不安装系统组件。已安装运行时的用户不必执行。

## 接入 Qwen Image

先用上游发布的原生可执行文件和匹配的 `qwenimage21` 模型目录单独跑通；然后配置：

```json
"image": {
  "enabled": true,
  "command": ["/absolute/path/qwenimage-ncnn-vulkan"],
  "model": "/absolute/path/models/qwenimage21",
  "gpu": 0,
  "timeout": 1800
}
```

模型工具 `images.generate` 支持 prompt、output、width、height、steps、seed、references。有 references 自动添加上游 `-i` 进入编辑路径，最多 10 张；输出文件相对工作区，禁止覆盖。文生图宽高为 16 倍数，编辑为 32 倍数。MVP 限制 64–2048，默认 512×512、40 步、单张串行。没有封装 LoRA、ControlNet、透明度控制或 batch UI，虽然上游可能支持它们。

不要仅把步数改成 4 就认为获得了四步加速模型；相应 LoRA/调度需要额外适配。模型的最低内存要求要按当前官方 README 及实机条件判断；“少量显存可运行”不代表总内存小。本测试环境没有足够条件验证它。

## 真正的验收

```bash
python scripts/smoke_real.py --config configs/local.json
# 配置 image.enabled 后，显式增加真正的生图测试：
python scripts/smoke_real.py --config configs/local.json --image
```

脚本调用真实 ncnn，要求模型自主用工具写文件并验证，不允许 ScriptedBackend；失败会返回非零。`--image` 会运行真实 Qwen Image 命令并检查输出文件存在，不评判画质。所有结果写入 `reports/real-smoke-result.json`。MCP/Python 开启后按安全说明补充启动授权参数。
