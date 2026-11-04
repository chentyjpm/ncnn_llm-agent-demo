#!/usr/bin/env python3
"""Compile unchanged ncnn x86 RMSNorm headers and run a guarded ISA matrix.

Requires an existing GCC/Clang C++ compiler and x86-64 host. No downloads,
installation, ncnn configuration, or source changes. Intended for the existing
Linux GCC/Clang CPU CI matrix; other platforms are not claimed as tested.

Unsupported CPU/OS ISA paths are always printed and recorded, never passed.
Use --require scalar,sse2,avx,avx-fma,avx512 to require the entire matrix.
Compiler errors and executed test failures always fail, even for optional ISAs.
"""

import argparse
import json
import platform
import shlex
import subprocess
import sys
from pathlib import Path


VARIANTS = {
    "scalar": ["-DAGENT_RMSNORM_SCALAR=1", "-fno-tree-vectorize"],
    "sse2": ["-msse2"],
    "avx": ["-mavx"],
    "avx-fma": ["-mavx", "-mfma"],
    "avx512": ["-mavx2", "-mfma", "-mavx512f", "-mavx512dq", "-mavx512bw", "-mavx512vl"],
}


def invoke(command, log, timeout=120):
    printable = shlex.join([str(part) for part in command])
    print(f"COMMAND {printable}", flush=True)
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, check=False)
    log.write_text(f"COMMAND {printable}\n{result.stdout}", encoding="utf-8")
    print(result.stdout, end="", flush=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Verified pinned ncnn checkout")
    parser.add_argument("--build-dir", type=Path, required=True, help="Store binaries, logs, and summary here")
    parser.add_argument("--cxx", default="g++", help="GCC/Clang compiler executable (not a shell command)")
    parser.add_argument("--require", default="scalar,sse2", help="Comma-separated ISAs that must execute")
    args = parser.parse_args()
    required = set(args.require.split(","))
    if not required <= VARIANTS.keys() or not required:
        parser.error("--require must name one or more of: " + ",".join(VARIANTS))
    if platform.machine().lower() not in ("x86_64", "amd64"):
        parser.error("UNAVAILABLE: this matrix requires an x86-64 host")
    source = args.source.resolve()
    include = source / "src" / "layer" / "x86"
    for name in ("rmsnorm_fp32.h", "x86_usability.h"):
        if not (include / name).is_file():
            parser.error(f"Missing pinned upstream header: {include / name}")
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    test = Path(__file__).with_name("test_x86_rmsnorm.cpp").resolve()
    # Explicit baseline defeats ambient compiler defaults such as native AVX512.
    base = [args.cxx, "-std=c++11", "-O2", "-march=x86-64", "-mno-avx",
            "-mno-avx2", "-mno-avx512f", "-mno-fma", "-ffp-contract=off",
            "-fno-fast-math", "-I", str(include)]
    probe = build / "probe_x86_rmsnorm"
    summary = {"platform": platform.platform(), "source": str(source), "compiler": args.cxx,
               "required": sorted(required), "variants": {}}
    if invoke(base + ["-DAGENT_RMSNORM_CPU_PROBE=1", str(test), "-o", str(probe)],
              build / "probe-build.log") != 0:
        print("FAIL baseline CPU/OS probe could not compile", flush=True)
        return 1

    failed = False
    for variant, flags in VARIANTS.items():
        binary = build / f"test_x86_rmsnorm_{variant}"
        state = summary["variants"][variant] = {}
        # Compile even when the execution ISA is unavailable; compile errors
        # remain failures and are never disguised as hardware skips.
        result = invoke(base + flags + [str(test), "-o", str(binary)],
                        build / f"{variant}-build.log")
        state["compile_exit"] = result
        if result != 0:
            state["status"] = "COMPILE_FAILED"
            failed = True
            continue
        result = invoke([str(probe), variant], build / f"{variant}-capability.log")
        state["probe_exit"] = result
        if result == 77:
            state["status"] = "UNAVAILABLE_CPU_OR_OS"
            failed |= variant in required
            print(f"UNAVAILABLE ISA={variant} not_executed required={variant in required}", flush=True)
            continue
        if result != 0:
            state["status"] = "PROBE_FAILED"
            failed = True
            continue
        result = invoke([str(binary), variant], build / f"{variant}-test.log")
        state["test_exit"] = result
        state["status"] = "PASS" if result == 0 else "FAIL"
        failed |= result != 0

    summary["status"] = "FAIL" if failed else "PASS_REQUIRED_MATRIX"
    (build / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for variant, state in summary["variants"].items():
        print(f"SUMMARY ISA={variant} status={state['status']}", flush=True)
    print(f"{summary['status']} report={build / 'summary.json'}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        sys.exit(1)
