# ncnn 20261020 release review — 2026-10-21

## Scope and provenance

Base application main: `95f23e3afbf996efbc5b9864c7abecb644d6832e` (merged PR5).
Advance the bridge-only ncnn pin from
`b5721b4bd2c8d0a8b861477f72c515db6b9f6dd0` to
`7e36d25cb153092ba87a60530455c7376fedf452`.
The official `20261020` tag resolves directly to the latter commit; its release
was published on October 20 at 03:40:10 UTC. No prebuilt release binary replaces
our source build, and this project does not publish a Release.

- [Exact source comparison](https://github.com/Tencent/ncnn/compare/b5721b4bd2c8d0a8b861477f72c515db6b9f6dd0...7e36d25cb153092ba87a60530455c7376fedf452)
- [Official release](https://github.com/Tencent/ncnn/releases/tag/20261020)

The release notes cover months of older work. They are not all new changes to
this application: PR4/PR5 already integrated the native changes through b5721b4.

## Interface, source and build review

Exactly two new commits and three changed files were reviewed:

| Commit | Files | Effect on this project |
| --- | --- | --- |
| `2eecf08c37ed522cb45ba1afd24dcf79822d5e81` | `python/src/main.cpp`, `python/tests/test_extractor.py` | Optional Python binding releases the GIL around four `Extractor.extract` overloads; adds a thread-progress regression. This application uses the C++ bridge, not the Python binding. |
| `7e36d25cb153092ba87a60530455c7376fedf452` | `README.md` | Updates download links to 20261020 and documents additional prebuilt targets. |

The tuple-returning overloads reacquire the GIL before creating Python tuples;
the direct-Mat overloads use pybind11 call guards. Native `Extractor` signatures
and implementation are unchanged. This is a source review, not execution of the
optional Python binding's new threading test.

Both complete recursive Git trees contain 4,448 entries and are untruncated.
Comparison of leaf paths, modes, object types, blob SHAs and submodule gitlinks
finds only the three files above. In particular, native `src`, root CMake,
`cmake`, `tests`, `tools`, `toolchains` and glslang are unchanged. Shared identities:

- `src` tree: `0914382d3f5b54e9c811183839487149128d17ae`
- `CMakeLists.txt` blob: `48935e43c925ed265be79c6e1a86e7877e1fcb34`
- `cmake` tree: `668e27021537bae59353a7b5224d516854e36d2d`
- glslang gitlink: `780b6a209132dc74e99458f62ebe5fa7e57c41cf`

The bridge's source-build path leaves `NCNN_PYTHON` at its unchanged upstream
OFF default, and the application has no `import ncnn` dependency. No interface
or runtime adaptation is required; changing behavior just to accompany the pin
would be unnecessary. Keep the existing build flags and validation gates.
Identical native sources do not imply identical binaries: ncnn's default
`NCNN_VERSION` incorporates the configure date, and toolchains may differ.
No GIL, inference speed or GPU performance improvement is claimed for LocalAgent.

Keep ncnn_llm at reviewed official PR58 commit `a14fe16`, not its moving head.
Its master remains `0538ae8`. Keep Qwen Image `4e48f36`, its embedded ncnn
`c6b351b`, JSON and all model pins unchanged. The private local MiniCPM FP16
experiment is excluded; no part of that unpublished compatibility patch is here.

## Model-source audit

Read the six actual main dependency/catalog/source files, then compared all 17
requested sources with the successful October 20 observation:

- Four Hugging Face repositories: the three Qwen2.5 Instruct/Coder models and
  `nihui-szyl/qwen-image-ncnn`, including existing base and Turbo file identities.
- Three ModelScope Qwen `master` manifests, using their independent revisions.
- SDU root and ten original/INT8 directories for MiniCPM4 0.5B, Qwen2.5 VL 3B,
  Qwen3.5 0.8B, Qwen3 0.6B and Youtu LLM, including `model.json` bytes.

No newly changed weights, graphs, configuration, tokenizers or documentation;
no retrieval errors. All ten SDU configuration hashes and indexed file sizes
match the current catalog. The already-known base-image pin versus repository
head difference is not new: Turbo has its own existing pin. Mirrors are not an
independent model authority. This metadata/index/config audit does **not**
verify complete remote weight bytes; no model weights were downloaded locally.
Existing model quality and SDU download-timeout backlog is not repaired by this
release alignment.

## Verification and cost boundaries

Local Linux tool regression on the candidate: 633 run, 619 passed, 14 optional
Office/platform skips, zero failures/errors. These fixture-based orchestration
tests are not model inference or native compilation. Machine-generated evidence
is in `reports/ncnn-20261020-release-final/` (not committed).

Existing CI gates remain intact: focused CPU and strict software Vulkan tests,
native compilation/package retests, cross-platform tools, real Qwen CPU
numerical/generation/HTTP tests and full installers. Exact-head and exact-merge
results belong in the PR after they finish, not predeclared here. Existing image
and native-catalog scope checks avoid unrelated large-model downloads when their
inputs are unchanged. No duplicate PR, manual dispatch or retry is needed just
to repeat the previously tested native behavior.

The owner's standing merge exception covers only an exactly reproduced known
Intel Mac full-installer llm/image Vulkan probe exit=-6; it is a failure, not a
pass. New failures or a different Mac failure must be diagnosed before merge.
Software Vulkan is not physical Adreno coverage. This update does not establish
fresh trained MiniCPM VQA, physical GPU, Windows/macOS model inference or
performance results, nor compatibility with the unpublished FP16 conversion.
