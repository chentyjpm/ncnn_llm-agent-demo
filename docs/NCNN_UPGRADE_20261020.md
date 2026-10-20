# Continued upstream review — 2026-10-20

Continue existing PR5 from `c4cebf2308e6570fae141e6fb55f8a9c79aaaaa0`,
without merging its unresolved Intel installer gate. New bridge ncnn:
`b5721b4bd2c8d0a8b861477f72c515db6b9f6dd0`.

[Upstream comparison](https://github.com/Tencent/ncnn/compare/44b02dabdcf97499281a4692ebd65bad73dcf8b6...b5721b4bd2c8d0a8b861477f72c515db6b9f6dd0)
contains four commits (23 files):

- `VkWeightAllocator` includes TRANSFER_SRC when creating storage buffers,
  allowing MemoryData weight-buffer copying. This is cross-platform Vulkan,
  not an Adreno-only change.
- Required subgroup-size pipeline metadata is attached only when both size
  control is supported and COMPUTE is included in requiredSubgroupSizeStages.
  This fixes MoltenVK validation; existing subgroup execution remains tested.
- Qualcomm/Adreno FP16-packed GEMM and SDPA mask addressing computes vector
  offsets before extracting lanes. The generic vendor branch is unchanged.
  INT8 cooperative convolution changes packed strides and predicates, disables
  KHR matrix padding on Qualcomm, accounts for 2048 bytes of scratch for 1x1,
  fixes Winograd output stores and int16 unpacking.
- simplevk adds a final momoten library-name fallback after existing loader
  attempts fail. The application neither downloads nor bundles this library;
  existing native loader search and isolated probe/fallback policies remain.
- Upstream Python/mobile wheel workflows and optional OpenCV imports change.
  The application builds native C++ adapters, not those Python wheels. No new
  Python/mobile package installation or support claim is introduced.

No public header/model-format removal was found. Matching pinned internal
headers remain mandatory. ncnn_llm stays at a14fe16; image engine stays at
4e48f36 with its separate embedded ncnn c6b351. No model-source revision changed.

## Validation

Retain the previous 200 strict normalization/rounding Vulkan cases and CPU
simpleomp/layout/INT8 tests. Add focused GEMM/SDPA packed-half and MemoryData
copy coverage and relevant convolution checks. Real-Qwen CPU, native builds,
platform tools and complete packaging run for the published exact head.
Final results and artifact links belong in PR5, not in historical reports.

Lavapipe executes generic software Vulkan paths. It does not establish the
Qualcomm conditional shaders, Adreno cooperative-matrix behavior, physical GPU
performance or mobile driver behavior. No physical Adreno device is available.
No new-ncnn trained MiniCPM VQA is claimed. The known Intel bundled-probe
failure remains a separate, unapproved merge gate for PR5; no Release.

Local Python regression: 633 run, 619 passed, 14 optional/platform skips,
zero failures/errors; generated evidence is
`reports/ncnn-b5721b4-tools/test-results.json`. These tests use explicitly fake
model adapters where documented and are not inference evidence. Native and
Vulkan execution is pending exact-head CI, with no local CMake/ICD available.

Additional strict gate requests 39 direct matrix/attention/copy cases: 24 GEMM,
9 masked grouped-head SDPA and 6 MemoryData copies, across FP32, packed FP16
(native storage disabled) and native FP16 storage. It verifies selected pipelines,
actual submitted workgroups and numerical references. Test-local pass-through
Vulkan call observation verifies weight buffer usage and an actual source copy;
all calls reach the real driver unchanged. Five bounded upstream convolution
inputs supplement this gate; their permissive helper skips do not prove Adreno
execution. The unchanged 200-case norm gate remains. GCC strict syntax, YAML and
workflow shell syntax checks pass; actual execution remains pending CI.
