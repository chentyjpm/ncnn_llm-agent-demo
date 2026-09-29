# Project rules for coding agents

- Read README.md, docs/SECURITY.md and reports/TEST_REPORT.md before changing behavior.
- Runtime defaults: Python disabled, external MCP processes disabled, fixed OS commands disabled, image generation disabled.
- Never claim model inference passed based on ScriptedBackend, fake CLI/image/native processes, or protocol tests.
- Core test: `python scripts/run_tests.py`. Save real output in reports/; do not hand-edit test results.
- Real model test: `python scripts/smoke_real.py --config configs/local.json` (requires actual local weights/runtime).
- Do not download large model files, change system services or modify the user's existing ncnn checkout without explicit approval.
- Preserve argv arrays / shell=False, workspace guards, explicit unsafe-host opt-in, MCP tool allowlists and call limits.
- MCP stdio servers are separate trusted executables, not sandboxed by client workspace checks.
- Native C++ is a separate adapter; pin and validate its external ncnn ABI instead of silently using master.
- Python stdlib only for orchestration. Do not add a cloud dependency as a hidden fallback.
- Record which operating systems were actually tested. Linux tests do not establish Windows/macOS correctness.
- Application path checking is single-writer defense, not a kernel filesystem isolation boundary.
