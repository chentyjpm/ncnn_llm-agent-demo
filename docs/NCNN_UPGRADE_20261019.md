# Upstream upgrade review — 2026-10-19

Base application commit: `b4c5c2c8e9be527eeabb8e55042a597f8cac234e`.
Bridge ncnn: `44b02dabdcf97499281a4692ebd65bad73dcf8b6`.
Image engine: `4e48f36dd356c9b48fce43a349a972b1f310afa0`.

## Source review

The [ncnn compare](https://github.com/Tencent/ncnn/compare/5d1c5ad4fd4b4ba32794000cd77a19ba7145d987...44b02dabdcf97499281a4692ebd65bad73dcf8b6)
contains four commits:

- RMSNorm constructs only the selected subgroup or fallback reduction pipelines.
  Direct Vulkan tests must validate the actual selected pipeline, not assume both
  are allocated.
- GroupNorm and InstanceNorm add pack1/4 subgroup reductions with centered,
  compensated accumulation for mean/variance stability. BASIC and ARITHMETIC
  support, subgroup size 4–128 and the option gate the path; fallback remains.
  Group/channel dispatch splits over x/y to respect workgroup limits.
- Emscripten updates the toolchain and enables pthreads when NCNN_THREADS or
  NCNN_OPENMP is set, propagating public compile/link flags. These conditional
  changes do not enable pthread flags on the desktop targets. Emscripten is not
  an application target and is not validated by desktop CI.

No removed public API or model-format change was found. New pipeline members
are compiled using the matching pinned internal headers. Existing LayerNorm,
RMSNorm, roundEven, BF16 layout, simpleomp and INT8 regressions are retained;
GroupNorm and InstanceNorm receive direct Vulkan numerical coverage and their
upstream tests, including high group/channel dispatch counts.

The [image compare](https://github.com/nihui/qwenimage-ncnn-vulkan/compare/14953559f39c7658e5a6cc117481e1922660cc9e...4e48f36dd356c9b48fce43a349a972b1f310afa0)
changes exactly one file, `.github/workflows/release.yml`, from cibuildwheel
4.3.0 to 4.3.1. Native runtime, CLI, model layout and submodule gitlinks are
identical. The exact reviewed pair may skip expensive image model tests;
unknown commits, repositories, submodules or model-input changes must not use
this exception. Image native compilation and packaging still run.

ncnn_llm stays at reviewed official PR58 runtime
`a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60` and image embedded ncnn stays at
`c6b351b56fbe32e0381ae00331e3df649b20d7b7`.

## Verification boundaries

Exact-head CI results and installers will be recorded in the upgrade PR.
Required checks: focused CPU/operator/OOM and actual software Vulkan dispatch,
four-platform tools, native adapters, real Qwen CPU and complete installers.
Software Vulkan does not establish physical GPU performance or full GPU-model
inference. No RISC-V hardware, Emscripten or new-ncnn trained MiniCPM VQA claim.
The prior PR4 Intel packaged-probe exception does not authorize merging this
new upgrade with the same failure without a separate decision. No Release.

Local preparation: 633 tests run, 619 passed, 14 skipped (optional Office and
platform-specific), zero failures/errors. Actual generated report:
`reports/ncnn-44b02da-tools/test-results.json`. Python compilation and diff
checks passed. The direct Vulkan companion passed GCC C++11 strict syntax
checking, and workflow YAML/shell parsing passed; native runtime tests remain
CI-only. The strict suite requests 200 cases when FP16 is available, including
132 Group/Instance cases, double-precision centered references, both subgroup
options, pack1/4, FP32/FP16 and large dispatch boundaries. This is intended
coverage, not a claim that these cases have executed before CI.
