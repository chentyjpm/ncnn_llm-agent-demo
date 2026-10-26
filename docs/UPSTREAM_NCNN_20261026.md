# ncnn INT8 upstream review — 2026-10-26

Bridge pin: `11bfbb4b2206023d34f32579a14abf9241052d09` →
`1123bc623abc17cf061f2a2760cf26693440c4f9`.

The three new runtime commits since the last successful audit are:

- `fa05e983`: generate `StorageBuffer` SPIR-V for INT8 storage shaders.
- `572dc0c8`: RISC-V vector INT8 packing/unpacking.
- `1123bc62`: RISC-V packed INT8 3×3 depthwise convolution, with associated ARM depthwise output allocation corrections and upstream tests.

The previously reviewed `cb65ca26` Python-wheel release CI change is included
transitively. It is not a separate application feature.

## Interface and build review

The changed runtime files are `src/gpu.cpp`, RISC-V packing/depthwise source and
its new private kernel header, and ARM depthwise source. There is no public
ncnn API, model graph format, model directory, tokenizer, CMake interface or
submodule change in this range. Existing native adapters therefore require no
API shim. The ncnn dependency is immutable and verified by the existing build
scripts. The image engine continues to use its separately locked embedded
ncnn revision; this update does not silently change that ABI.

No changes to application confirmation defaults, command arguments, workspace
checks, model catalogs or private MiniCPM FP16 work are included.

## Focused validation

- CPU jobs add upstream packing and 24 bounded INT8 depthwise cases across
  channel-pack boundaries, odd output tails, strides 1/2 and requantization.
- A dedicated Vulkan test requires compilation of an actual INT8 storage
  shader and checks the emitted SPIR-V storage representation. Compilation
  checks are not shader dispatch or model inference.
- Existing strict software Vulkan dispatch tests, cross-platform native builds,
  tools, real Qwen CPU inference and installer workflows remain enabled.
- Resource checks run before the real-Qwen workflow downloads model weights.
  The expensive ten-model download matrix is not dispatched for this update.

A host x86 pass does not prove RISC-V kernels ran. Apple Silicon builds alone
also do not establish numeric ARM depthwise coverage. No physical RISC-V/GPU
performance claim is made. CI results for the exact PR/merge commit must be
checked independently; this review is not a predeclared pass. The known Intel
Mac installer llm/image Vulkan probe exit `-6` remains a failure, covered only
by the owner's existing narrow merge exception. Any different/new failure
blocks merging until diagnosed and resolved.
