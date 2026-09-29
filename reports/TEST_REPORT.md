# ncnn-local-agent v0.1 测试报告

核对/交付日期：2026-09-29。以下记录对应这次交付，不把上游 README 的宣传或样例性能当作本机测试。

## 结论

**工具编排层可运行：72 项自动化测试，72 项通过，失败 0、错误 0、跳过 0。**

**尚不能宣称完整 ncnn 大模型工作流已实机跑通。** 当前环境缺 ncnn 可执行文件、匹配模型/权重和原生开发依赖，没有 GPU 设备与 Docker。未验证真实模型自主规划/工具调用成功率、生图质量/速度、Vulkan、C++ 编译/链接或 Docker 隔离效果。适配器使用模拟进程通过的测试仅证明协议与参数处理。

## 测试环境及命令

- Python：`3.13.5`。
- 平台：`Linux-6.18.44-x86_64-with-glibc2.41`；当前测试仅 Linux。
- 测试命令：`python scripts/run_tests.py`。
- 本次用时：18.588 秒，仅表示当前环境这一次的工具测试用时，不是模型性能指标。
- 证据：`tests.log`、`test-results.json`、`environment.json`。

| 分组 | 测试数量 | 本次结果 |
|---|---:|---|
| AdapterTests | 10 | 全部通过 |
| AgentTests | 13 | 全部通过 |
| CLITests | 4 | 全部通过 |
| ExecutionTests | 12 | 全部通过 |
| MCPTests | 9 | 全部通过 |
| RPCTests | 7 | 全部通过 |
| WorkspaceTests | 17 | 全部通过 |

## 实际跑通的链路

演示命令：

```bash
python -m local_agent demo --workspace reports/demo_workspace --allow-unsafe-host-python
```

演示总计 9 步、8 次工具调用；其规划与纠错步骤来自 `examples/demo_plan.json`，明确使用 `SCRIPTED_TEST_DOUBLE_NOT_LLM`，没有任何实际 LLM 参与。

| 顺序 | 执行动作 | 实際结果 |
|---|---|---|
| 1 | files.write 写合成测距 CSV | 成功；值为 232、234、236 |
| 2 | files.read 读取 CSV | 成功 |
| 3 | python.run，故意使用错误列名 distance | 真实解释器抛 KeyError，退出码非零；预期失败 |
| 4 | python.run，改为 distance_m | 真实执行成功，生成 distance_summary.json |
| 5 | MCP tools/call 调独立 Python 服务器 | 实际 JSON-RPC/stdio 进程通信，mean=234 |
| 6 | files.read 读取统计结果 | 成功 |
| 7 | files.write 写 summary.md | 成功 |
| 8 | system.run 固定 Python 版本查询 | 实际执行成功 |
| 9 | 最终回复 | 固定测试规划器返回结束；额外代码核对全部演示断言 |

实际生成的统计数据：

```json
{"count": 3, "mean_m": 234.0, "min_m": 232.0, "max_m": 236.0}
```

这只是演示合成样本，不是用户现场测距结论。输出在 `reports/demo_workspace/`；完整动作结果见 `demo-result.json` 及对应 `runs/demo-*.jsonl`。

## 什么是真的、什么是模拟

| 能力/项目 | 本次验证层级 | 未验证内容 |
|---|---|---|
| 工作区文件读/写/patch/列表 | 真实本地文件系统 | 对恶意并发目录替换的内核级隔离 |
| Python 执行/错误/超时/输出上限 | 真实宿主 Python 子进程 | 任意不可信代码安全隔离 |
| 固定系统命令 | 真实宿主子进程、argv 数组、shell=False | 通用 shell/管理员系统操作 |
| MCP handshake/list/call/pagination/通知/错误 | 真实本地进程与 JSON-RPC 消息；专用测试/示例服务 | 任意第三方服务器兼容性、HTTP/OAuth、完整最新规范 |
| Agent 多步/非法 JSON 重试/循环限制 | 固定 ScriptedBackend 与真实工具 | 真模型规划、代码生成和自主纠错能力 |
| ncnn CLI 适配 | 明确的 fake-cli 进程验证输入输出解析 | 实际模型加载/推理 |
| ncnn C++ bridge | 源码接口核对；fake native 进程验证 Python 侧协议 | C++ 编译/链接、真实推理、Vulkan |
| Qwen Image 适配 | 参数/路径验证与 fake-image 进程固定 PNG fixture | 实际生图、画质、模型占用/速度 |
| Docker 模式 | 参数构造及不可用时拒绝降级的测试 | Docker 容器执行和逃逸防御 |

所有 fake 模型/图像输出都明确命名为 fixture，不会在正常 `run` 中自动启用。本包没有展示任何假称由 Qwen 生成的图片。

## 真实推理/原生构建的实际尝试

`python scripts/smoke_real.py --config configs/local.example.json` 已尝试，返回失败：

```text
PolicyError: ncnn model/model.json not found; no fallback to a fake model
```

LLM 状态为 **not_run**，不是 passed；没有发生实际模型推理。证据：`real-smoke-result.json`、`real-smoke-attempt.log`。

`cmake -S native -B /mnt/data/native-build-check` 已尝试，在配置阶段因没有可用 `NCNN_LLM_SOURCE_DIR` 而停止。没有进入 C++ 编译或链接；不能据此评估 C++ 源码正确性或 ncnn ABI 兼容。证据：`native-configure-attempt.log`。

容器下载源码/依赖的网络探测结果见 `network-probe.json`。仓库接口通过外部 GitHub 阅读工具核对，不代表容器已有源码。没有可用 GPU 设备节点或 Docker 可执行文件，见 `environment.json`。

## 已测试的防误操作措施

路径越界、绝对路径、Windows UNC/盘符/ADS、符号链接/硬链接、保留文件名、重复/空 patch、文件上限、隐式覆盖、未知工具、参数类型、未授权 Python、未知命令和 MCP 目标改写被拒绝。另测试进程超时、输出截断、错误 JSON、重复动作、最大轮数和上下文字符预算。

**这些测试不是安全认证。** 宿主 Python 和第三方 MCP 有宿主权限；路径保护是单写者应用层检查，不是 OS 沙箱；Docker 模式也未做实机隔离验证。完整边界在 `docs/SECURITY.md`。

## 在用户设备上接续验证

修改 `configs/local.json` 的实际模型与可执行文件路径，按 `docs/NATIVE_SETUP.md` 完成：

```bash
python -m local_agent doctor --config configs/local.json
python scripts/smoke_real.py --config configs/local.json
# 显式配置并启用 image 后：
python scripts/smoke_real.py --config configs/local.json --image
```

需要检验：模型能否稳定遵循 JSON 动作、工具执行后的继续推理、实际中文代码生成质量、原生接口兼容、内存峰值与生图耗时。小模型能加载不等于这些能力合格。

## 压缩包复测

已将压缩包解压到新的临时目录，并执行 `python -m unittest discover -s tests -v`，72 项再次通过。证明源目录没有依赖本次创建目录的固定路径；不改变真实模型、C++ 和 Docker 尚未验证的范围。证据：`archive-retest.json`、`archive-retest.log`。
