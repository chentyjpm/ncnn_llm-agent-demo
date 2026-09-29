# CI 回归用例与验收方法

本轮在原有 82 项工具/CI 辅助测试上追加 32 项回归测试，共 114 项。
Windows 原有 3 项 POSIX 链接测试仍按条件跳过，不计入通过数。
另外对真正编译的 C++ 程序和打包后的程序分别执行原生测试，不能用 Python fixture 替代。

## 修复点

1. `std::stoi` 原来允许 `4junk` 等数字前缀。桥接器改用完整消费输入的 `std::from_chars`，统一检查线程数 1..128、设备编号 0..INT_MAX。非法参数在模型加载前退出。
2. `git submodule status` 返回 0 不等于子模块正确。现在拒绝未初始化 `-`、版本错位 `+`、冲突 `U`、格式异常和已修改的依赖工作树。
3. 构建证据绑定 commit、run ID、attempt 和组件；重新配置或构建后，旧的下游成功状态失效。
4. 构建、测试、打包分别校验同一程序的 SHA-256；不能打包未测试或被替换的程序。使用新打包目录，避免残留文件进入产物。
5. 对压缩包中的实际可执行字节再次测试。打包中的 BUILD_INFO 不再包含尚未完成的 package=running；最终打包结果保存在本轮 status.json 中。

没有通过关闭失败步骤、`continue-on-error` 或更换假程序让 CI 变绿。

## Python 回归用例

文件：`tests/test_ci_regressions.py`。

| 类别 | 用例与预期 |
|---|---|
| 子模块状态 | 已初始化和无子模块通过；未初始化、错位、冲突、异常格式必须失败 |
| 真实 Git 临时仓库 | 构造真实未初始化 gitlink、修改已提交文件，校验函数必须拒绝；干净固定提交通过 |
| 证据隔离 | 同次运行可继续；不同 commit、run、attempt、组件或重新 refs 不复用旧通过结果 |
| 阶段顺序 | 重新配置/编译废弃旧测试；缺少成功构建不能 smoke；测试仍在运行不能 package |
| 程序一致性 | 构建或测试后的 SHA-256 不一致必须失败；全部一致才允许继续 |
| 原生测试驱动 | 错误退出码、缺诊断、stdout 协议污染、超时、程序不存在、脚本伪装为二进制必须失败 |
| 用例完整性 | 原生测试名称唯一、数量匹配、不让图像模型通过无参数默认路径开始推理 |

这里的临时 Git 仓库和用于测试驱动器的 Python 命令明确属于测试 fixture，不属于原生模型测试。

## 实际 C++ 测试

`native/cli_options_test.cpp` 包含 20 个参数解析用例，覆盖有效边界、空串、字母、尾随字符、小数、空白、负数和整数溢出。
Release 模式使用显式失败计数和非零退出，不使用会被 NDEBUG 删除的 assert。

CMake/CTest 中有 `bridge_cli_parse`、`bridge_help`、`bridge_version` 三个测试。

`scripts/native_regression.py` 只接收 ELF/PE/Mach-O 文件，并真实启动程序：

- bridge：22 项，覆盖帮助/版本、缺少/空模型目录、损坏的 model.json、未知/缺值参数、线程与设备边界、诊断输出不污染 JSON-RPC stdout。
- image：9 项，覆盖帮助、未知参数、缺少提示词、无效/零尺寸、零步数、无效设备、负种子与非有限 LoRA 参数。

这些用例不下载或加载有效权重，不初始化 GPU 推理。损坏 model.json 仅用于检查加载前的 JSON 解析错误。
每个构建 job 先测试构建目录中的程序，打包后再取出归档中的实际程序完整复测一次。

## 执行与证据

```bash
python scripts/run_tests.py --output-dir reports/ci/tools
python scripts/ci_native.py configure --component bridge
python scripts/ci_native.py build --component bridge
python scripts/ci_native.py smoke --component bridge
python scripts/ci_native.py package --component bridge
```

原生步骤需要按 `ci/dependencies.json` 准备固定依赖源码；图像目标把 bridge 换成 image。

`native-logs-*` artifact 包含 `status.json`、`native-test-results.json`、`packaged-native-test-results.json`、CTest 和编译日志。
`tool-tests-*` artifact 包含本次全部工具测试的 `test-results.json` 和 `tests.log`。

验收标准：同一提交的 4 个工具 job 与 8 个原生 job 全部 success；原生程序编译/链接、CTest、CLI 回归、归档复测、产物上传均成功。
这不代表模型效果、真实 GPU 推理、Docker 隔离、所有系统版本或所有硬件驱动已经验收。
