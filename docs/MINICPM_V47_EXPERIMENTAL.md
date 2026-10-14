# MiniCPM-V-4.7 experimental image questions

This opt-in adapter has passed real-checkpoint image questions on Linux CPU FP32 using original `openbmb/MiniCPM-V-4.7-1B` weights converted to ncnn. Two different photographs were processed through the actual Python/native bridge, producing image-specific answers. The scope remains experimental single-thumbnail chat; this does not establish video, detail slices, multiple images, GPU or reduced-precision support. No downloadable catalog entry is added.

## Desktop import and activation

1. Build/install this feature version of LocalAgent. Native builds fetch the official `futz12/ncnn_llm` pull-request ref `refs/pull/58/head` and require exact commit `a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60`. [Upstream PR #58](https://github.com/futz12/ncnn_llm/pull/58) is experimental and unmerged; this is not a claim of upstream acceptance. A changed PR head fails the dependency check instead of silently changing the runtime. The official repository allowlist is unchanged.
2. Obtain and convert the original checkpoint separately using the [runtime conversion guide](https://github.com/futz12/ncnn_llm/blob/a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60/tests/MINICPM_CPU_VALIDATION.md). No weights are bundled or downloaded by the import action.
3. In **安装与模型**, select the local MiniCPM import card. Enter the absolute directory containing the converted `model.json`, confirm that this is a trusted local model, and import it. This registers its existing location without copying approximately 6.22 GB of weights. Keep the directory available.
4. Activate the imported model explicitly. LocalAgent derives CPU FP32, experimental single-image chat and disabled Agent/tool mode automatically; manual JSON editing is not needed.
5. Select chat, attach exactly one PNG/JPEG, enable the experimental image-question checkbox, and ask a question. Start with a 448×448 PNG. Reattach an image for a subsequent image question.

Import checks package structure, referenced local files and the bridge's weightless capability response. It does not authenticate arbitrary local weights or establish real inference on the user's machine. An incompatible bridge, missing package or unsupported mode fails explicitly.

### Source/configuration users

The same behavior can be selected in an existing local configuration:

```json
{
  "llm": {
    "backend": "ncnn_bridge",
    "command": ["/absolute/path/to/ncnn_agent_bridge"],
    "model": "/absolute/path/to/converted-minicpm",
    "experimental_vision": true,
    "device": "cpu",
    "threads": 4
  }
}
```

Retain the remaining configuration. Neither this flag nor import grants tools or enables image generation. Existing model-download consent remains unchanged. The bridge must report `minicpm_v4_7_runtime >= 1` through `--capabilities`; runtime/model capabilities are also checked before image inference.

## Scope and boundaries

- One image in the final user message, no video or detail slices. Attach the image again for another image question; image history is not implicitly reloaded.
- PNG/JPEG only, 8 MiB maximum and a bounded header-declared image size of 16 megapixels. The native vision runtime also rejects resized thumbnails above 4,096 ViT patches, preventing extreme aspect ratios from expanding into unbounded attention allocations. Malformed files can still fail in the native decoder.
- Workspace path guards reject traversal, symlinks and hardlinks. These are single-writer application checks, not a kernel sandbox against concurrent filesystem changes.
- CPU FP32 graphs remain experimental. Preprocessing explicitly targets the official `MiniCPMV4_6ImageProcessorPil` backend with Pillow 12.3.0: 1,290 resize comparisons are pixel-exact, and normalized RGB-thumbnail values match within 3e-7. The default `AutoImageProcessor` Torchvision backend can differ by up to two pixel levels on resized images and is not the reference backend for this adapter. JPEG decoder parity is not guaranteed. Keep an exactly 448×448 PNG for the first trained-model parity test; helper checks alone do not establish model correctness.
- The runtime rejects cases requiring unsupported slicing. It does not silently drop detail crops. For explicit thumbnail-only use of arbitrary photos, set `setting.vision.max_slice_nums` to `1` in the converted model configuration. Leave a value greater than `1` to retain strict rejection when the processor would produce detail slices.
- Ordinary text chat, document attachments and image generation retain their existing routes. The image-question checkbox cannot be combined with image-generation or Agent mode.
- Protocol fixture tests are separate from the real-checkpoint tests below. Linux checks do not establish Windows/macOS compatibility or GPU correctness.

The native `capabilities` RPC advertises `single_image: minicpm_v4_7_experimental` only for the matching loaded configuration. `infer` then accepts `image_path` alongside text messages. This local bridge is trusted application plumbing, not a public network API or independent filesystem sandbox.

The converted package must include the exact MiniCPM pretokenizer artifact and
`tokenizer.pretokenizer` metadata. The runtime rejects older packages without
this metadata rather than silently using legacy whole-string BPE. MiniCPM chat
uses its own checkpoint template, including empty thinking blocks when thinking
is disabled; native tool-call messages are outside this experimental scope.

## Verified real-checkpoint results

Revision `ef3ab3bf0120a60e1a2d2b52d984a79d8cb7c630` was converted from the official original safetensors, not registered as GGUF. The FP32 package occupies about 6.22 GB. The actual bridge's peak RSS was about 6.17 GiB in this test environment, so leave memory for the application and OS.

- A pencil-in-water photograph produced: “A red pencil with a yellow tip rests in a glass of water, partially submerged.” This exactly matched the independent official Transformers reference.
- A second photograph produced: “A bumblebee rests on a pink cosmos flower, surrounded by wilting blooms and red flowers in a garden.” The actual image was inspected and contains those objects.
- Both requests used the same persistent native process, with a fresh context and one image per request. No model/cloud fallback or file-tool execution was involved.

Machine-readable versions, fixture hashes, provenance and measured timings are in `minicpm_v47_real_validation.json`. The first request took about 98 seconds including loading; the second took about 68 seconds. These are recorded test runs, not cross-hardware performance guarantees. Test images and model weights are not redistributed.

To repeat a real image-question smoke test, place a PNG/JPEG inside the configured workspace and use the explicit local configuration above:

```sh
python scripts/smoke_vision.py --config configs/local.json \
  --image test-photo.png --expect pencil
```

The runner checks the real bridge capability, records the answer and image hash in `reports/real-vision-smoke-result.json`, and optionally requires a phrase. Without `--expect`, nonempty text alone is not a semantic quality check. This does not replace the separate Agent/file-tool smoke test in `scripts/smoke_real.py`.

## Import-to-answer HTTP acceptance

The current pinned revision `a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60` refactors MiniCPM into the existing upstream GPT/image/position utilities instead of six separate vision implementation files. The application bridge was rebuilt from a clean checkout of that exact revision. The same real import-to-answer HTTP test passed again in 44.9 seconds with an answer identical to the pre-refactor application result. Fresh binary/image hashes and all checks are in `minicpm_v47_refactored_http_validation.json`; the older reports retain their original revisions and measurements.

The integrated model-center path was also tested with the real PR58 native executable: trust confirmation, registration without copying weights, separate activation, automatic CPU/image configuration, restored settings, HTTP image upload and actual image-question inference. Agent mode and multiple-image requests rejected. The pencil/water answer completed in about 92 seconds; this application prompt includes attachment context and is not the earlier exact-HF prompt comparison. See `minicpm_v47_http_validation.json`.

```sh
python scripts/web_minicpm_real_smoke.py --binary /path/to/ncnn_agent_bridge \
  --model /path/to/converted-minicpm --image /path/to/refract-448.png \
  --expect pencil --expect water --output reports/minicpm-http-new
```

The output directory must be new. This test uses the same HTTP routes as the UI but does not simulate browser interaction. Local Chromium launch was blocked by the test environment's Unix-domain socket restriction. Browser model-center acceptance is covered separately by the CI browser runner, whose import package is an explicitly labelled fixture, not real inference.
