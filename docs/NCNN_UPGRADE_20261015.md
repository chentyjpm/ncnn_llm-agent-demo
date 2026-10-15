# ncnn bridge dependency review — 2026-10-15

Candidate: `Tencent/ncnn@c15904bb8918187ec100f1ebe708942af83b84be`, replacing
`b0f97f43f266617ff5e5096d440dbe3f9a4c35b8` for the text/vision bridge only.

[Upstream diff](https://github.com/Tencent/ncnn/compare/b0f97f43f266617ff5e5096d440dbe3f9a4c35b8...c15904bb8918187ec100f1ebe708942af83b84be)
contains two commits:

- `fcde729c7b0b0fcecf273ed21b0e25dd2deb67be`: RISC-V INT8 Winograd23/43;
  also changes ARM32 NEON, x86 SSE2, MIPS MSA and LoongArch LSX INT8 tile
  alignment from 4 to 8. It fixes nested-team state save/restore in both
  Clang and GCC simpleomp paths, separating Clang's requested team size from
  the currently executing team. These changes are not RISC-V-only.
- `c15904bb8918187ec100f1ebe708942af83b84be`: upstream Python wheel release
  tooling moves cibuildwheel 4.3.0 to 4.3.1; not an application runtime API change.

The ncnn_llm runtime stays pinned to reviewed official PR58 commit
`a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60`. Later test-only PR commits are not
silently adopted. Qwen Image stays at `14953559f39c7658e5a6cc117481e1922660cc9e`
and its embedded ncnn at `c6b351b56fbe32e0381ae00331e3df649b20d7b7`.

## Verification plan and boundaries

The focused ncnn-core workflow compiles unchanged upstream CPU, INT8 convolution
and convolution-allocation-failure tests against the exact lock. Linux GCC and
Clang exercise simpleomp; a separate GCC OpenMP-disabled configuration matches
the packaged bridge policy. Higher x86 ISAs and runtime dispatch are disabled in
this focused build so the modified SSE2 path cannot be hidden by AVX dispatch.
The CPU test explicitly receives `-fopenmp` for simpleomp configurations; otherwise
upstream's private compile flag leaves its nested pragmas inactive.

Existing native matrix, real Qwen numerical/prefill/KV/generation regression and
full installers remain required evidence. Compiler/operator tests are not
trained-model inference. Existing MiniCPM reports retain their old ncnn revision;
this change must not relabel those historical VQA tests as new-version tests.
No RISC-V, MIPS, LoongArch or ARM32 execution is claimed by x86 tests.

The known Intel macOS bundled Vulkan probe failure is not fixed or waived by this
upgrade. If reproduced, it is reported separately and blocks an unconditional
all-platform-green/automatic-merge claim. No large image or catalog redownload is
needed merely because the independent bridge ncnn lock changed.

## Local pre-publication evidence

Linux Python 3.12.14: 625 tests run, 611 passed, 14 skipped, zero failures/errors.
Skips are optional Office dependencies and platform-specific tests, not passing
inference. See `reports/ncnn-c159-tools/test-results.json`. Python compilation,
workflow YAML parsing and whitespace validation passed. The actual official
PR58 fetch verified head `9b85400739ecf611b4301109f2d5272dd65053c8` contains
`a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60` and checked out exactly the latter.

This fresh local environment has no CMake/Clang or cached model weights. Native
and real-model validation will be performed by the existing CI environments;
no local C++ or new-version MiniCPM VQA pass is claimed here. CI results and
installer links will be recorded in the upgrade PR after their exact-head runs.
