# Continued ncnn upgrade review — 2026-10-18

Continues the existing upgrade PR, rather than merging or replacing its unresolved
Intel macOS installer gate. New bridge ncnn candidate:
`5d1c5ad4fd4b4ba32794000cd77a19ba7145d987`.

[Diff from the earlier c15904 candidate](https://github.com/Tencent/ncnn/compare/c15904bb8918187ec100f1ebe708942af83b84be...5d1c5ad4fd4b4ba32794000cd77a19ba7145d987)
contains five upstream commits:

- RISC-V BF16 detection/cast/clip, with two additive CPU capability APIs and CMake
  feature checks. The same commit changes cross-platform `Net::convert_layout`:
  determine 16-to-32-bit conversion before selecting packing, using destination
  bit width, then check conversion allocation failures. This needs CPU net tests,
  not only RISC-V review.
- RISC-V FP32 5x5 stride-1 depthwise reuses four output rows. No execution claim on
  RISC-V hardware is made by Linux x86 validation.
- Vulkan UnaryOp round uses `roundEven`, including half-integer boundary tests.
- Vulkan LayerNorm and RMSNorm use subgroup reduction only when BASIC+ARITHMETIC,
  supported subgroup width (4–128), and the option permit it. New pack1/pack4
  shaders and dispatch dimensions need actual Vulkan numerical testing; shader
  compilation or a ReLU device probe is insufficient.

No removed public API or model-format change was found. Application native builds
still compile all adapters against the exact new ncnn source/internal headers.
The ncnn_llm pin remains `a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60`; image engine
and its separate embedded ncnn are unchanged. Historical MiniCPM VQA reports and
the earlier c15904 regression evidence are not relabelled as tests of this SHA.

## Validation boundaries

Extend the focused operator workflow for net/layout, normalization and rounding,
including software Vulkan execution with explicit subgroup capability reporting.
Retain the previous simpleomp/SSE2 INT8 regressions. Run the existing real Qwen
CPU regression and cross-platform application/native packaging for the exact new
application commit. Software Vulkan is not physical-GPU performance validation;
unsupported device subgroup paths must be identified rather than called passed.

No local model weights survived the environment rebuild. No new-version MiniCPM
trained VQA, RISC-V hardware, or physical Vulkan GPU inference is claimed.
Final CI evidence and candidate installers are recorded in PR #4; the existing
Intel packaged-probe failure remains an unresolved merge gate without a new
explicit exception. No Release is published.

Local preparation: 625 Python tests run, 611 passed, 14 skipped, no failures or
errors; skips are optional Office dependencies and platform-only cases. Full
report: `reports/ncnn-5d1c5ad-tools/test-results.json`. Python compilation, all
workflow YAML parsing and diff checks passed. The direct-Vulkan companion passed
GCC `-Wall -Wextra -Werror` syntax checking only; runtime execution is pending CI.
It validates selected pipeline handles, actual VkCompute dispatch/submit, pack1/4,
subgroup on/off, capability-gated FP16 and exact ties-to-even results. The CI
negative control must reject a missing ICD/device. Updated upstream tests cover
normalization group counts 65535/65536/65537. The x86 test_net FP16 storage branch
is capability-skipped; BF16 layout coverage is enabled explicitly.
