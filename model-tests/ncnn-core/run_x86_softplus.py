#!/usr/bin/env python3
"""Build unchanged ncnn Softplus kernels with real Mat/Option and guarded ISAs.

No CMake, downloads, installs, fake kernels or replacement ncnn types. Configures
upstream platform.h.in locally, and links unchanged option.cpp and cpu.cpp.
The normal gate uses a documented fixed absolute/relative FP32 error budget plus
BF16 nearest-storage rounding. --strict-negative additionally rejects loss of a
representable negative-tail result to zero (a known log(1+exp(x)) limitation).
This is direct operator coverage, not full library/API dispatch or model inference.
"""

import argparse
import hashlib
import json
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path


VARIANTS = {
    "scalar": ["-DAGENT_SOFTPLUS_SCALAR=1", "-fno-tree-vectorize"],
    "sse2": ["-msse2"],
    "avx": ["-mavx"],
    "avx-fma": ["-mavx", "-mfma"],
    "avx-fma4": ["-mavx", "-mfma4"],
    "avx2": ["-mavx2", "-mfma"],
    "avx-ne-convert": ["-mavx2", "-mfma", "-mavxneconvert"],
    "avx512": ["-mavx2", "-mfma", "-mavx512f", "-mavx512dq", "-mavx512bw", "-mavx512vl"],
    "avx512-bf16": ["-mavx2", "-mfma", "-mavx512f", "-mavx512dq", "-mavx512bw", "-mavx512vl", "-mavx512bf16"],
}


def invoke(command, log, timeout=180):
    printable = shlex.join([str(part) for part in command])
    print(f"COMMAND {printable}", flush=True)
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, check=False)
    log.write_text(f"COMMAND {printable}\n{result.stdout}", encoding="utf-8")
    print(result.stdout, end="", flush=True)
    return result.returncode


def configure_platform(source, include):
    """Apply ordinary CMake configure-style choices, not substitute ncnn code."""
    enabled = {"NCNN_STDIO", "NCNN_THREADS", "NCNN_BATCH", "NCNN_BF16", "NCNN_FORCE_INLINE"}
    template = (source / "src/platform.h.in").read_text(encoding="utf-8")
    configured = re.sub(r"^#cmakedefine01 (\w+)$",
                        lambda match: f"#define {match[1]} {int(match[1] in enabled)}",
                        template, flags=re.MULTILINE)
    configured = re.sub(r"^#cmakedefine .*$", "", configured, flags=re.MULTILINE)
    if "#cmakedefine" in configured:
        raise ValueError("Unrecognized upstream platform.h.in configuration directive")
    (include / "platform.h").write_text(configured, encoding="utf-8")
    # Static executable export visibility only; no declarations/implementations.
    (include / "ncnn_export.h").write_text(
        "#ifndef NCNN_EXPORT_H\n#define NCNN_EXPORT_H\n#define NCNN_EXPORT\n#endif\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Verified pinned ncnn checkout")
    parser.add_argument("--build-dir", type=Path, required=True, help="Store binaries, logs and summary here")
    parser.add_argument("--cxx", default="g++", help="GCC/Clang executable, not a shell command")
    parser.add_argument("--require", default="scalar,sse2", help="Comma-separated ISAs that must execute")
    parser.add_argument("--expect-revision", help="Require this exact git HEAD before compiling")
    parser.add_argument("--strict-negative", action="store_true", help="Fail on representable negative outputs rounded to zero")
    args = parser.parse_args()
    required = set(args.require.split(","))
    if not required or not required <= VARIANTS.keys():
        parser.error("--require must name one or more of: " + ",".join(VARIANTS))
    if platform.machine().lower() not in ("x86_64", "amd64"):
        parser.error("UNAVAILABLE: this matrix requires an x86-64 host")
    source = args.source.resolve()
    build = args.build_dir.resolve()
    if build == source or source in build.parents:
        parser.error("--build-dir must be outside the upstream source checkout")
    build.mkdir(parents=True, exist_ok=True)
    include = build / "include"
    include.mkdir(exist_ok=True)
    files = ["src/platform.h.in", "src/mat.h", "src/option.h", "src/option.cpp", "src/cpu.cpp",
             "src/layer/x86/softplus_fp32.h", "src/layer/x86/softplus_bf16s.h", "src/layer/x86/x86_usability.h"]
    for name in files:
        if not (source / name).is_file():
            parser.error(f"Missing upstream source: {source / name}")
    revision = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if revision.returncode:
        parser.error("--source must be a pinned git checkout")
    revision = revision.stdout.strip()
    if args.expect_revision and revision != args.expect_revision:
        parser.error(f"Pinned revision mismatch: expected={args.expect_revision} actual={revision}")
    dirty = subprocess.run(["git", "-C", str(source), "diff", "--name-only", "HEAD", "--", "src"],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if dirty.returncode or dirty.stdout.strip():
        parser.error("Upstream src must be unchanged from the pinned HEAD")
    for generated in ("platform.h", "ncnn_export.h"):
        if (source / "src" / generated).exists():
            parser.error(f"Source-side {generated} would shadow the recorded test configuration")
    configure_platform(source, include)
    test = Path(__file__).with_name("test_x86_softplus.cpp").resolve()
    base = [args.cxx, "-std=c++11", "-O2", "-march=x86-64", "-mno-avx", "-mno-avx2",
            "-mno-avx512f", "-mno-fma", "-mno-fma4", "-ffp-contract=off", "-fno-fast-math", "-pthread",
            "-I", str(source / "src"), "-I", str(source / "src/layer/x86"), "-I", str(include)]
    summary = {"status": "RUNNING", "platform": platform.platform(), "source": str(source), "revision": revision,
               "compiler": args.cxx, "required": sorted(required), "strict_negative": args.strict_negative,
               "scope": "unchanged direct kernels; actual external-storage Mat and Option; serial; no Net dispatch/model inference",
               "test_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
               "source_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files},
               "variants": {}}
    summary_path = build / "summary.json"

    def save():
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    probe = build / "probe_x86_softplus"
    if invoke(base + ["-DAGENT_SOFTPLUS_CPU_PROBE=1", str(test), "-o", str(probe)], build / "probe-build.log"):
        summary["status"] = "PROBE_BUILD_FAILED"
        save()
        return 1
    # Option's real constructor uses CPU information. Compile both actual
    # upstream translation units once at baseline, then link into each variant.
    objects = []
    for name in ("option", "cpu"):
        obj = build / f"ncnn_{name}.o"
        if invoke(base + ["-c", str(source / f"src/{name}.cpp"), "-o", str(obj)], build / f"{name}-build.log"):
            summary["status"] = "DEPENDENCY_BUILD_FAILED"
            save()
            return 1
        objects.append(str(obj))
    compiler_probe = build / "compiler_probe.cpp"
    compiler_probe.write_text("int main() { return 0; }\n", encoding="utf-8")
    failed = False
    for variant, flags in VARIANTS.items():
        state = summary["variants"][variant] = {"flags": flags}
        # Optional instruction extensions may not exist in an older compiler.
        # Once the compiler accepts flags, a kernel build error always fails.
        result = invoke(base + flags + ["-c", str(compiler_probe), "-o", str(build / "compiler_probe.o")],
                        build / f"{variant}-compiler.log")
        state["compiler_probe_exit"] = result
        if result:
            state["status"] = "UNAVAILABLE_COMPILER"
            failed |= variant in required
            save()
            continue
        binary = build / f"test_x86_softplus_{variant}"
        result = invoke(base + flags + [str(test)] + objects + ["-o", str(binary)], build / f"{variant}-build.log")
        state["compile_exit"] = result
        if result:
            state["status"] = "COMPILE_FAILED"
            failed = True
            save()
            continue
        result = invoke([str(probe), variant], build / f"{variant}-capability.log")
        state["probe_exit"] = result
        if result == 77:
            state["status"] = "UNAVAILABLE_CPU_OR_OS"
            failed |= variant in required
            print(f"UNAVAILABLE ISA={variant} not_executed required={variant in required}", flush=True)
        elif result:
            state["status"] = "PROBE_FAILED"
            failed = True
        else:
            command = [str(binary), variant] + (["--strict-negative"] if args.strict_negative else [])
            result = invoke(command, build / f"{variant}-test.log")
            state["test_exit"] = result
            state["status"] = "PASS" if result == 0 else "FAIL"
            failed |= result != 0
        save()
    summary["status"] = "FAIL" if failed else "PASS_REQUIRED_MATRIX"
    save()
    for variant, state in summary["variants"].items():
        print(f"SUMMARY ISA={variant} status={state['status']}", flush=True)
    print(f"{summary['status']} report={summary_path}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        sys.exit(1)
