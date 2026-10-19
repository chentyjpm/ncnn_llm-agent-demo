# Focused ncnn operator regressions

The source path must be the checkout verified against `ci/dependencies.json`.
The upstream tests are built unchanged. This suite establishes operator/runtime
coverage, not trained-model inference or physical-GPU performance.

## Strict Vulkan companion

`test_vulkan_required` creates Vulkan layers directly, so Net CPU fallback and
upstream helper return-code 233 skips cannot satisfy it. Each case requires a
valid selected pipeline, the expected buffer packing/storage, submitted command
work, successful completion, and agreement with a numerical reference.

With subgroup and FP16 capabilities, it runs **200 direct cases**:

- 68 existing LayerNorm, RMSNorm, and UnaryOp round-to-even cases.
- 112 GroupNorm/InstanceNorm cases: subgroup requested off/on, FP32/FP16
  storage and arithmetic, pack1/pack4, and seven numerical shapes per operator.
  These cover widths around the device subgroup size, width 4097, a 257×255
  high-offset input, a 256×256 constant, and 1024×512 outlier variance.
- 16 GroupNorm cases with 12 channels and 2/4/6/12 groups, exercising boundaries
  within packed lanes and across packed vectors.
- Four FP32 boundary cases: 65,536 one-element GroupNorm groups and 65,537
  InstanceNorm channels, each with subgroup requested off/on. The latter uses
  direct clones for upload and download to avoid the packing shader's separate
  channel-dispatch limit.

GroupNorm/InstanceNorm use an independent centered, two-pass double-precision
reference. Their `eps=1/1024` makes the half-precision constant case's
coefficients exact. FP32 high offsets are near 1024; FP16 inputs use exact
representable values near 64 because the normalization coefficients and output
still use half precision. Tolerances follow upstream's absolute-or-relative
comparison: FP32 outliers `0.0001`, other FP32 inputs `0.005`, FP16 high offsets
`0.01`, and other FP16 inputs `0.002`. These tolerances do not claim full FP32
accuracy for half-stored coefficients.

For both spatial norms, every selected reduction/coefficient/normalization
pipeline must exist. The test predicts **all operator workgroups**, excluding
upload/download, and compares that count to `pending_dispatch_total()`. That
API counts workgroups, not dispatch calls. The model covers subgroup dispatch
splitting and the distinct GroupNorm/InstanceNorm fallback variance chains.
Keep these checks aligned when changing the pinned implementation.

The lavapipe CI job requires both subgroup and FP16 storage/arithmetic support;
missing capabilities fail instead of silently reducing the advertised matrix.
Outside that job unsupported optional capabilities are reported explicitly,
while a missing Vulkan device always fails. The strict test has a 600-second
limit. CI separately checks that an invalid ICD fails with the device diagnostic.

## Upstream companion tests

Both CPU and Vulkan jobs build/run `test_groupnorm` and `test_instancenorm`, in
addition to the existing CPU/layout/LayerNorm/RMSNorm/UnaryOp selections. Their
Vulkan cases provide broader dimensions, affine parameters, long compensated
variance, and the same large group/channel dispatch boundaries. Their permissive
GPU helpers are supplemental coverage; the strict companion is the no-skip gate.

Local syntax checking is not shader compilation or Vulkan execution. Use the
workflow's pinned-source configure/build/CTest steps for execution evidence.
