# ncnn RISC-V implementation review — 2026-10-23

## Scope and provenance

Base application main: `4cae4b87f584e476975cbdead9dd807365ebb5d2` (PR6).
Advance only the bridge ncnn pin from
`7e36d25cb153092ba87a60530455c7376fedf452` to
`11bfbb4b2206023d34f32579a14abf9241052d09`.
These are four new October 22 commits since the successful October 22 audit.
The latest release is still `20261020`; this is an immutable source update,
not a newer release or a replacement with a prebuilt binary.

[Exact upstream comparison](https://github.com/Tencent/ncnn/compare/7e36d25cb153092ba87a60530455c7376fedf452...11bfbb4b2206023d34f32579a14abf9241052d09).

| Commit | Reviewed change |
| --- | --- |
| `ece40d848d2f1c6d6c6774f006b640b658930aa5` | RISC-V FP16 5x5 stride-1 depthwise convolution reuses four output rows, retaining the kernel accumulation order and existing tail loops. |
| `91c978557929197a9c9a6783ea759ce2d9223660` | RISC-V FP32 5x5 stride-2 depthwise convolution reuses adjacent input columns for paired outputs, retaining the single-output tail. |
| `30839ce868201939ab7b0ca007d50eaec22e8d34` | RISC-V ReLU gains BF16 storage execution and guards the FP16 dispatch with runtime support. |
| `11bfbb4b2206023d34f32579a14abf9241052d09` | RISC-V ShuffleChannel replaces static scratch arrays with call-local arrays and expands the C906 halfword index buffer from `8 * 4` to `8 * 8`. |

The complete comparison contains exactly five files, all under
`src/layer/riscv/`: the two depthwise headers, `relu_riscv.cpp`, its internal
header and `shufflechannel_riscv.cpp`. Public ncnn APIs, CMake build inputs,
glslang, x86/ARM/Vulkan implementations and model formats are unchanged.
No bridge ABI, application dispatch, export or model-directory adaptation is
required. Do not introduce a behavioral workaround where the reviewed interface
has not changed. The focused operator regression selection is expanded to cover
the affected operator families in addition to the existing CPU/Vulkan gates.

Keep ncnn_llm at reviewed official PR58 commit `a14fe16`, not its moving head.
Its master remains `0538ae8`; PR58 head `9b854007` is the already-known October 14
test-consolidation commit, not a new daily update. Qwen Image remains `4e48f36`,
with its separate embedded ncnn `c6b351b`. JSON and all model pins are unchanged.
The private MiniCPM FP16 experiment is not included or accessed by this update.

## Model-source audit

The six dependency/catalog/source files were freshly read at the exact main SHA.
All 17 requested model sources were retrieved successfully and compared with
the retained successful October 22 baseline:

- Hugging Face official metadata for the three Qwen2.5 repositories and
  `nihui-szyl/qwen-image-ncnn`, including every file identity and existing base
  image/Turbo pin comparisons.
- Independent ModelScope `master` file SHA256 manifests for the same three Qwen
  repositories. Their commit identities are not borrowed from Hugging Face.
- SDU root, all ten original/INT8 directory inventories for MiniCPM4 0.5B,
  Qwen2.5 VL 3B, Qwen3.5 0.8B, Qwen3 0.6B and Youtu LLM, and actual `model.json`
  bytes, compared with both the previous audit and current catalog.

No weight, graph, configuration, tokenizer or documentation changes were found;
all relevant pin sizes/digests and all SDU configuration hashes match. This is
metadata and configuration validation, **not a full remote weight-byte rehash**.
Mirror metadata is not independent model authority. No model weights were
downloaded locally for this audit; existing model-quality backlog is unchanged.

## Verification and boundaries

Local Linux Python orchestration regression: 639 run, 625 passed, 14 optional
Office/platform skips, zero failures/errors. Bytecode compilation and the
model-free CLI doctor also passed; the doctor correctly reports missing local
native engines and weights. Machine-generated evidence is retained under
`reports/ncnn-20261023-final/` (not committed). Exact candidate CI results are
recorded in the PR after execution. Fixture-based Python tests are not native
compilation or model inference. Existing cross-platform native, tool, real Qwen
CPU and installer gates remain enabled. Image/native-catalog scope gates retain
their current behavior to avoid unrelated large-model downloads. The local
cloud computer has no CMake; native compilation is performed by the existing CI
runners rather than installing tools into this environment.

The Qwen CPU runner now records available workspace disk and Linux
`MemAvailable` immediately before its first 0.5B model download, requiring at
least 12 GiB disk and 6 GiB available memory. Missing/insufficient resource data
stops the job before downloading weights. Tests execute the preflight with
sufficient and insufficient resource fixtures. This is an initial headroom
check, not a peak-memory measurement or a guarantee for later phases; the
existing ModelHub disk checks and sequential model cleanup remain intact.

Supplemental upstream evidence at the exact new ncnn SHA:
[linux-riscv64](https://github.com/Tencent/ncnn/actions/runs/39749394324) and
[linux-riscv32](https://github.com/Tencent/ncnn/actions/runs/39749394343).
These are upstream cross-build/emulator checks, not this application's RISC-V
integration or physical hardware/performance measurements. LocalAgent's x86 CI
operator tests do not execute the changed RISC-V instruction paths.

The owner's standing merge exception covers only the verified existing Intel
Mac full-installer llm/image Vulkan probe `exit=-6`. That is a failure, not a
pass. Any new failure or different Mac failure must be diagnosed before merge.
No new physical GPU, RISC-V application inference, MiniCPM FP16 compatibility,
model quality or performance improvement is claimed by this source review.
