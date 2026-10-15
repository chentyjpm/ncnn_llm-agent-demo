# Windows / Linux / macOS 持续集成

新增两条 GitHub Actions 工作流。配置完成不等于所有平台已经构建成功；每次以对应 commit 的 Actions 实际状态和日志为准。`reports/TEST_REPORT.md` 是初次交付的历史报告，不代表本次 CI。

## 覆盖范围

| Runner | 架构 | 工具层 | 原生编译 |
|---|---|---|---|
| `windows-2022` | x64，Visual Studio | Python 3.13 | ncnn bridge / Qwen Image |
| `ubuntu-24.04` | x64，GCC | Python 3.13 | ncnn bridge / Qwen Image |
| `macos-15` | Apple Silicon arm64，Apple Clang | Python 3.13 | ncnn bridge / Qwen Image |
| `macos-15-intel` | Intel x64，Apple Clang | Python 3.13 | ncnn bridge / Qwen Image |

这是在不同操作系统 runner 上执行，不是在 Linux 内伪装 Windows/macOS。没有覆盖 Windows ARM、Linux ARM、旧版系统或所有硬件驱动。

## 1. Cross-platform tool tests

文件：`.github/workflows/ci.yml`。

每次推送 main、向 main 提 PR 或手动启动时运行，4 个独立 job，失败不取消其他平台。

执行 Python 字节码编译、标准库测试套件和 `doctor`。文件读写、Python 子进程、MCP stdio 通信使用真实操作；LLM/图像 adapter 使用明确的 fixture。CI 辅助脚本测试仅验证参数构造、退出码和日志处理，不能代替 C++ 编译。

原有 72 项测试加 10 项 CI 辅助测试，共 82 项；Windows 原有的 3 项 POSIX 链接回归测试按原来的条件跳过，报告分别显示通过、失败、错误和跳过数量，不把跳过算通过。

新报告保存到 `reports/ci/tools/`，而不是覆盖/读取仓库中的历史结果。下载产物 `tool-tests-<runner>-py313` 可查看 `tests.log` 与 `test-results.json`。

## 2. Native C++ build

文件：`.github/workflows/native-build.yml`。

4 个操作系统配置 × 2 个组件，共 8 个独立 job。修改 native、ci、原生 CI 脚本、工作流或上游锁时自动运行；纯文档更新不触发重编译。也可在 Actions 中选择本工作流，点击 Run workflow，选择 main。

每个 job 执行：读取并校验依赖锁 → checkout 固定 SHA 与子模块 → CMake configure → Release 编译和链接 → 运行真实可执行文件的启动检查 → 打包。任何一步失败都会让该 job 失败，不用 `continue-on-error` 或假程序伪装通过。

- bridge：编译 `ncnn_agent_bridge`、上游 ncnn_llm 和 ncnn；CTest 运行 `--help` / `--version`，另验证缺少模型时退出码必须为 2 且输出指定错误。
- image：编译上游 `qwenimage-ncnn-vulkan`、其 ncnn 和图像编码依赖；执行真实程序的 `-h`。

Vulkan 编译开启；使用 ncnn SIMPLEVK 和源码自带的 shader 编译依赖，不要求安装独立 Vulkan SDK。为减少 runner 差异，CI 关闭 OpenMP，单次构建并行度为 2。**不下载模型，不运行 GPU 推理，不验证画质、速度、Agent 自主规划或 Docker 隔离。** 编译成功只证明对应源码/工具链的构建与启动检查通过。

依赖锁位于 `ci/dependencies.json`，默认业务上游 SHA 必须与 `upstream-lock.json` 一致；临时 PR 构建仅允许下述经明确审核的单个例外；文字 bridge 的 ncnn 与 Qwen Image 内嵌 ncnn 分别锁定：`ncnn.commit` 用于 bridge，`qwenimage.ncnn_commit` 必须匹配图像上游 gitlink。ncnn_llm 新增的 `ncnn` 子模块不参与 bridge 构建；只允许锁文件列明的完整 SHA 保持未初始化，其他缺失/不匹配子模块仍拒绝。JSON 固定到 v3.12.0 对应提交。所有 Actions 使用完整 commit SHA。更新依赖时明确修改锁文件，重新执行矩阵，不浮动跟随 master。

## 产物与故障定位

`native-bridge-<runner>` / `native-image-<runner>`：只有编译、启动检查、打包均通过才上传；内部 tar.gz 保留 Unix 可执行权限，包含程序、构建元数据和上游许可证。**这是 CI 检查产物，不是经过推理验收、签名/公证或完整可移植性测试的正式发布包。** 不含模型权重。

`native-logs-<component>-<runner>`：成功或失败都会尝试上传，包含依赖校验、CMake 配置、编译、启动检查日志及 `status.json`。保存 14 天。先找第一个失败步骤，区分下载、配置、编译、链接和启动错误；artifact 缺失不能解释为成功。

每个 job 的 Summary 显示该次结果，不复用历史报告。工作流只申请 `contents: read`，不保存 checkout 凭据，不使用仓库 secrets，不使用 `pull_request_target` 或自托管 runner，也不推送测试日志回仓库。

## 本地复现

工具层：

```bash
python scripts/run_tests.py --output-dir reports/ci/tools
```

原生构建：将锁定的源码分别 checkout 到 `.ci-src/ncnn_llm`、`.ci-src/ncnn`、`.ci-src/json` 和 `.ci-src/qwenimage`，初始化对应子模块后执行。Windows 在 Visual Studio 开发环境中运行；其他系统需要 CMake、Git 和 C/C++ 编译器。

```bash
python scripts/ci_native.py configure --component bridge
python scripts/ci_native.py build --component bridge
python scripts/ci_native.py smoke --component bridge
python scripts/ci_native.py package --component bridge
```

Qwen Image 将 `bridge` 换成 `image`。脚本不会自行联网下载依赖；缺少源码/工具链就明确失败。原有 `native/CMakeLists.txt` 的已安装 ncnn SDK 接入方式仍保留；新增 `AGENT_NCNN_SOURCE_DIR` 与 `AGENT_JSON_SOURCE_DIR` 是可选的源码构建入口。

## 上游升级分支

`upgrade/upstream-*` 推送会运行现有真实 Qwen CPU 与安装包工作流；PR 仍运行工具测试与跨平台原生矩阵。升级分支不自动合并、不发布 release。以该分支的准确提交与每个 job 的结果为准，不能复用 main 的历史绿色状态。

2026-10-13 升级将文字运行时固定到 `futz12/ncnn_llm@0538ae8b9e2d47f597ba3491e35d2f610c3a3114`、文字 ncnn 固定到 `Tencent/ncnn@b0f97f43f266617ff5e5096d440dbe3f9a4c35b8`。Qwen Image 上游、其嵌入的 ncnn 和所有模型/镜像文件的版本与校验保持不变。

新运行时将自定义算子移至 `src/kernel/`。bridge 显式接入这些源码与同一 ncnn checkout 的内部头文件；安装 SDK 模式必须额外指定匹配源码路径，不能猜测头文件版本。上游 x86 LM-head 的 AVX2/FMA 代码独立编译，通过 ncnn 的 CPU/OS 能力检测分派，旧 CPU 使用标量回退；关闭 OpenMP 时不要求 `omp.h`。共享 embedding 的裸 Gemm 统一使用 FP32 pipeline 与 forward 选项，避免 ARM/RISC-V 低精度 pipeline 和 FP32 extractor 输出不一致。

`bridge_gemv_dispatch` 检查 FP32/BF16、非整齐维度、标量与可用 AVX2 路径；`bridge_lm_head_precision` 用合成小权重检查共享投影数值。它们是原生算子回归，不是真实大模型推理。真实 Qwen CPU 工作流的数值比较改为使用与 bridge 相同的共享 LM-head，再执行生成、文件工具与 HTTP 验证。适配生成文件只写入 build 目录，原始上游 checkout 保持干净；共享投影源码校验不符时停止构建，要求重新审查。


### MiniCPM 临时官方 PR 依赖（2026-10-14）

正式上游基线仍为 `futz12/ncnn_llm@0538ae8b9e2d47f597ba3491e35d2f610c3a3114`。
在 [官方 PR #58](https://github.com/futz12/ncnn_llm/pull/58) 合并前，原生构建通过同一官方仓库的
`refs/pull/58/head` 获取可达历史，先确认锁定对象是 commit 且为该官方 ref 的祖先，再 detached checkout
`a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60`。任何 CMake 配置/构建前仍要求实际 HEAD 精确等于该 SHA。
PR ref 可以前进，但新增提交不会被构建；缺失对象、非祖先（包括不再包含该 pin 的 force-push）、错误来源或 HEAD 不匹配均失败。

`ci/dependencies.json` 记录实际构建 SHA/ref；`upstream-lock.json.repositories` 保留正式上游基线供更新监测，
`temporary_pull_requests.ncnn_llm` 单独记录 PR 编号、经审核的精确 commit/base。`scripts/ci_native.py` 校验该完整组合。
四个消费者（native-build、installers、qwen05-cpu、native-catalog）统一调用 `scripts/fetch_upstream.py`，
只在新空目录中拉取官方来源，不初始化 ncnn_llm 的未使用子模块。配置阶段再次校验官方 remote、对象类型、PR 祖先关系和精确 HEAD。
构建证据分别记录实际构建 commit、经审核 PR 来源和当次获取的 PR head；后者不代表被接受的新运行时。
此策略测试不等于跨平台构建或模型推理通过。

监测到上游或 PR 更新时只应提示人工审核，不可自动删除临时记录、更换 base 或跟随新 head。
PR 合并后恢复正式提交需要显式移除临时记录和 checkout_ref，并同时更新两个锁文件；官方仓库白名单保持不变。
Qwen Image 的上游提交、内嵌 ncnn pin、模型和镜像校验保持不变。

### 大模型下载的变更范围检查（2026-10-15）

`qwenimage-real`、`qwenimage-turbo` 和 `native-catalog` 在下载或安装测试依赖之前运行轻量 scope job。
普通 push 以该次准确的 before/after 提交比较变更：图像测试仅在对应图像代码/测试输入或
`qwenimage` 上游及专用内嵌 ncnn pin 改变时下载；文字 bridge 的独立 ncnn 升级不重复下载图像权重。
catalog 仅在模型目录、导入实现或 catalog 验收代码改变时自动下载整套目录模型。
仅 checkout、工作流编排或文档维护不会自动重下载模型；这类变化仍由工具测试和原生构建覆盖。

手动 `workflow_dispatch` 始终请求完整模型测试，适用于工作流编排维护后的模型端到端复验。
scope 无法读取准确历史或遇到损坏的相关锁时明确失败，不将未知状态当作通过。
Summary 会说明下载被跳过的原因；跳过不代表本次真实模型推理已经通过。Qwen 0.5B CPU 与安装包升级验证保持原有触发范围。
