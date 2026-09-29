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

依赖锁位于 `ci/dependencies.json`，两个业务上游 SHA 必须与 `upstream-lock.json` 一致；ncnn 固定到 Qwen Image 的子模块提交，JSON 固定到 v3.12.0 对应提交。所有 Actions 使用完整 commit SHA。更新依赖时明确修改锁文件，重新执行矩阵，不浮动跟随 master。

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
