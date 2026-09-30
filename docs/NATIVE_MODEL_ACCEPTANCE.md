# 0.8B 模型的验收策略

按用户要求，仅以下两项允许**普通问答内容错误**不阻断 CI：
`ncnn_qwen3_5_0_8b` 和 `ncnn_qwen3_5_0_8b_int8`。这是明确 ID 白名单，
不是所有小模型或所有未来模型自动放行；Qwen3 0.6B 和其他模型仍保留原来的正确性门槛。

`native_catalog_smoke.py` 分开报告 **runtime、quality、tool_contract**：

| 情况 | CI 处理 |
|---|---|
| 这两个 0.8B 正常输出但算术/常识答错 | `passed_with_quality_warnings`，退出码 0，Actions 显示警告 |
| 权重下载/哈希/安装/启用失败，推理异常、超时或清理失败 | `failed_runtime`，退出码 1 |
| 空回答、只有思考段、未闭合思考段、非法 UTF-8 或控制字节 | `failed_runtime`，不按“答错”处理 |
| 请求了生图工具意图验收，但动作格式、工具名或已检查参数不合格 | `failed_tool_contract`，退出码 1 |
| 其他模型答错 | `failed_quality`，维持原有阻断条件 |

每个用例的 `raw`、`visible`、`passed` 原样保留；错误答案仍然是 `passed: false`。
`quality.correct / evaluated / accuracy` 记录本次基础问答结果，**不是完整能力评测**。
`ok: true` 在上述白名单中只代表运行/协议要求通过，不再代表答案全部正确。
日志的 JSON 转义只用于安全记录异常字符，不修正或替换模型输出。

没有使用 `continue-on-error`，没有跳过权重或真实推理，也没有更换题目/预期答案。
这次是验收标准调整，不是模型数值、INT8、分词器或 UTF-8 缺陷修复；
已有失败报告不会被覆盖成通过，新的结论必须看新提交的实跑。

本地策略回归：`python -m unittest tests.test_native_catalog_acceptance -v`。
该回归使用明确的合成回复和异常，验证 CI 分类及退出码，不冒充真实模型推理。

集成位置：`scripts/native_catalog_smoke.py` 与 `.github/workflows/native-catalog.yml`。详见[多模型与生图](MODELS_AND_IMAGES.md)。
