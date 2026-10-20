# Focused ncnn operator regressions

The source path must be the checkout verified against `ci/dependencies.json`.
The original upstream test targets are built unchanged. The bounded convolution
companion below reuses an unchanged upstream helper. This suite establishes operator/runtime
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

## Strict matrix, attention and weight-copy companion

`test_vulkan_matrix_required` adds **39 direct cases** when native FP16 storage
is supported; the existing 200-case normalization companion is unchanged:

- 24 GEMM cases: FP32, packed FP16, and native FP16 storage; subgroup requested
  off/on; transposed/non-transposed A and B; aligned and odd M/N/K shapes;
  output pack1/pack4. All use a nonuniform full M×N C matrix to select the
  changed `broadcast_type_C=3` addressing path.
- Nine SDPA cases: the same three storage modes and 2D, singleton-3D, and
  per-head-3D masks. Four query heads and two KV heads exercise grouped heads.
  Odd query/value embedding widths force the two `sdpa_cross` products rather
  than flash attention. Every cross-product and all four Softmax pipelines
  must be valid, and the total workgroup count must match all six stages.
- Six MemoryData cases: the same storage modes with pack1/pack4. Model upload
  uses an actual `VkWeightAllocator`, then MemoryData copies into a separate
  blob allocation. A single-threaded RAII observer forwards real
  `vkCreateBuffer` and `vkCmdCopyBuffer` calls unchanged, verifies the source
  buffer has STORAGE/TRANSFER_SRC/TRANSFER_DST usage, and requires exactly one
  nonempty weight-buffer copy. Submission and exact numerical comparison must
  also succeed. Copy-only commands are explicitly required to have zero compute
  workgroups. This checks the allocator fix even on drivers that would tolerate
  invalid usage without Vulkan validation layers.

Packed FP16 explicitly disables native FP16 storage and FP16 arithmetic. Inputs
are exact binary fractions, uploaded as pack1 buffers; outputs are cloned back
and unpacked on CPU, excluding upload/download packing from operator dispatch
counts. GEMM uses an independent double-precision matrix product reference;
SDPA uses independent double-precision stable softmax attention. Tolerances are
`0.0001` for FP32 and `0.002` for half-stored matrix/attention results; MemoryData
requires exact equality. Both strict executables must fail for an invalid ICD.

Lavapipe must support native FP16 storage and GEMM's basic/shuffle subgroup
capabilities. Other devices explicitly report unsupported native FP16/subgroup
paths; FP32 and packed FP16 still run. The test does not override the device's
vendor ID, enable unsupported features, or replace shaders. On lavapipe it
compiles and executes the **generic** GEMM/SDPA paths only. Qualcomm-specific
packed-half address fixes, Adreno cooperative-matrix INT8 padding/scratch/output
fixes, physical GPU performance, and model inference remain unvalidated.

## Upstream companion tests

Both CPU and Vulkan jobs build/run `test_groupnorm` and `test_instancenorm`, in
addition to the existing CPU/layout/LayerNorm/RMSNorm/UnaryOp selections. Their
Vulkan cases provide broader dimensions, affine parameters, long compensated
variance, and the same large group/channel dispatch boundaries. Their permissive
GPU helpers are supplemental coverage; the strict companion is the no-skip gate.

Local syntax checking is not shader compilation or Vulkan execution. Use the
workflow's pinned-source configure/build/CTest steps for execution evidence.

The Vulkan job also runs unchanged `test_memorydata` and a bounded
`test_convolution_int8_smoke`. The latter includes pinned upstream
`test_convolution_3a.cpp` with its exhaustive entry point renamed and calls its
unchanged helper on five small inputs: 1×1, 3×3 (requesting Winograd23/43), and
2×2 SGEMM, including requantization and channel tails. It retains upstream CPU
references, option variants and permissive GPU skips, so a pass does not prove
an INT8 cooperative-matrix path ran. The exhaustive `test_convolution_3a` remains
in the CPU matrix only. Each new companion has a 600-second test timeout.
