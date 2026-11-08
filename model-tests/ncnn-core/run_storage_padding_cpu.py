#!/usr/bin/env python3
"""Bounded Linux x86-64 direct build when CMake is unavailable.

Uses unchanged ncnn support sources, generic Convolution, and SSE2 Cast/Packing/
ReLU/UnaryOp/Sigmoid/Padding. Does not install tools, download weights, or modify
the source checkout. This is not the full CMake/architecture/model test matrix.
"""

import argparse
import concurrent.futures
import hashlib
import json
import platform
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


CURRENT = "437f18f7b5fc85902cb22d15dad312e8672856e7"
PREVIOUS = "09981a0796cc0dd629ed5ba0f8d55653cc1406a5"
ALLOCATOR_SHA256 = "cda7cf14f36b1b008f067bb96e1765a20b8a4bf796f80abe5401fab2643dadad"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--old-source", type=Path, required=True)
    p.add_argument("--old-allocator-source", type=Path, required=True,
                   help="14004273e767eaee969c792ea0dbc3ae0b441251:src/net.cpp")
    p.add_argument("--build-dir", type=Path, required=True)
    p.add_argument("--reports-dir", type=Path, required=True)
    p.add_argument("--cxx", default="g++")
    args = p.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise SystemExit("This direct runner requires Linux x86-64; use the CMake suite elsewhere.")
    src, old, allocator, build, reports = [x.resolve() for x in
        (args.source, args.old_source, args.old_allocator_source, args.build_dir, args.reports_dir)]
    for output in (build, reports):
        if output == src or src in output.parents or output == old or old in output.parents:
            raise SystemExit("Build and report directories must be outside both ncnn checkouts.")
    for root, expected in ((src, CURRENT), (old, PREVIOUS)):
        actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        if actual != expected:
            raise SystemExit(f"Wrong source revision: {root}: {actual}, expected {expected}")
        dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True)
        if dirty:
            raise SystemExit(f"Tracked source edits are not permitted: {root}\n{dirty}")
    if hashlib.sha256(allocator.read_bytes()).hexdigest() != ALLOCATOR_SHA256:
        raise SystemExit("Old allocator control is not the exact 14004273 src/net.cpp.")
    include = build / "include"
    objects = build / "objects"
    variants = build / "variants"
    for path in (include, objects, variants, reports):
        path.mkdir(parents=True, exist_ok=True)

    enabled = {"NCNN_STDIO", "NCNN_STRING", "NCNN_VALIDATION", "NCNN_THREADS", "NCNN_INT8", "NCNN_BF16"}
    template = (src / "src/platform.h.in").read_text()
    configured = re.sub(r"^#cmakedefine01 (\w+)$", lambda m: f"#define {m[1]} {int(m[1] in enabled)}", template, flags=re.M)
    configured = re.sub(r"^#cmakedefine .*$", "", configured, flags=re.M)
    (include / "platform.h").write_text(configured)
    (include / "ncnn_export.h").write_text("#define NCNN_EXPORT\n#define NCNN_NO_EXPORT\n#define NCNN_DEPRECATED\n")
    classes = re.findall(r"^ncnn_add_layer\((\w+)", (src / "src/CMakeLists.txt").read_text(), re.M)
    generic = ["Input", "Cast", "Packing", "Flatten", "ReLU", "UnaryOp", "Sigmoid", "Convolution", "Padding"]
    x86 = ["Cast", "Packing", "ReLU", "UnaryOp", "Sigmoid", "Padding"]
    (include / "layer_type_enum.h").write_text("".join(f"{name} = {i},\n" for i, name in enumerate(classes)))
    decl = "".join(f'#include "layer/{name.lower()}.h"\n' for name in generic)
    decl += "".join(f'#include "layer/x86/{name.lower()}_x86.h"\n' for name in x86)
    decl += "namespace ncnn {\n" + "\n".join(f"DEFINE_LAYER_CREATOR({name})" for name in generic)
    decl += "\n" + "\n".join(f"DEFINE_LAYER_CREATOR({name}_x86)" for name in x86) + "\n}\n"
    (include / "layer_declaration.h").write_text(decl)
    registry = ""
    for name, available, suffix in (("layer_registry", generic, ""), ("layer_registry_arch", x86, "_x86")):
        registry += f"static const layer_registry_entry {name}[] = {{\n"
        for cls in classes:
            creator = cls + suffix + "_layer_creator" if cls in available else "0"
            registry += f'{{"{cls}", {creator}}},\n'
        registry += "};\n"
    (include / "layer_registry.h").write_text(registry)

    tests = Path(__file__).resolve().parent
    flags = [args.cxx, "-std=c++11", "-O2", "-msse2", "-mno-avx", "-ffunction-sections", "-fdata-sections", "-pthread"]
    flags += ["-I" + str(path) for path in (include, src / "src", src / "src/layer", src / "src/layer/x86", src / "tests")]
    commands = []

    def run(command, log, timeout=180):
        commands.append(shlex.join([str(c) for c in command]))
        with (reports / log).open("w") as handle:
            proc = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        return proc.returncode

    def compile_one(source):
        obj = objects / (str(source).replace("/", "_") + ".o")
        code = run(flags + ["-c", str(source), "-o", str(obj)], obj.name + ".log")
        if code:
            raise RuntimeError(f"Compile failed ({code}): {source}; see {reports / (obj.name + '.log')}")
        return source, obj

    sources = [src / "src" / (name + ".cpp") for name in
               ("allocator", "option", "cpu", "mat", "layer", "paramdict", "datareader", "modelbin", "blob", "net")]
    sources += [src / "src/layer" / (name.lower() + ".cpp") for name in generic]
    sources += [src / "src/layer/x86" / (name.lower() + "_x86.cpp") for name in x86]
    sources += [src / "tests/testutil.cpp", src / "tests/test_net.cpp"]
    companions = ["test_net_storage_featmask", "test_net_custom_allocator", "test_padding_int8_negative"]
    sources += [tests / (name + ".cpp") for name in companions]
    old_net = variants / "net_storage_old.cpp"
    old_padding = variants / "padding_old.cpp"
    old_allocator = variants / "net_allocator_old.cpp"
    shutil.copyfile(old / "src/net.cpp", old_net)
    shutil.copyfile(old / "src/layer/x86/padding_x86.cpp", old_padding)
    shutil.copyfile(allocator, old_allocator)
    sources += [old_net, old_padding, old_allocator]
    results = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            compiled = dict(pool.map(compile_one, sources))
        common = [compiled[s] for s in sources if s not in
                  [src / "tests/test_net.cpp", old_net, old_padding, old_allocator]
                  and s.parent != tests]

        def test(name, main_source, expected=0, marker=None, replace=None):
            objs = list(common)
            if replace:
                previous, replacement = replace
                objs[objs.index(compiled[previous])] = compiled[replacement]
            binary = build / name
            rc = run(flags + [str(compiled[main_source])] + [str(o) for o in objs] +
                     ["-Wl,--gc-sections", "-o", str(binary)], name + ".link.log")
            if rc:
                raise RuntimeError(f"Link failed for {name}; inspect {reports / (name + '.link.log')}")
            rc = run([str(binary)], name + ".log", timeout=300)
            output = (reports / (name + ".log")).read_text()
            ok = rc == expected and (not marker or marker in output)
            results.append({"name": name, "exit_code": rc, "expected_exit_code": expected,
                            "required_marker": marker, "passed": ok})
            print(f"{name}: exit={rc}, expected={expected}, {'PASS' if ok else 'FAIL'}", flush=True)

        for name in companions:
            test(name, tests / (name + ".cpp"))
        test("test_net_full", src / "tests/test_net.cpp")
        test("test_net_storage_old", tests / "test_net_storage_featmask.cpp", 2,
             "test_net storage featmask failed", (src / "src/net.cpp", old_net))
        test("test_padding_old", tests / "test_padding_int8_negative.cpp", 2,
             "NUMERICAL_MISMATCH", (src / "src/layer/x86/padding_x86.cpp", old_padding))
        test("test_net_allocator_old", tests / "test_net_custom_allocator.cpp", 2,
             "test_net extract unexpectedly cloned the input", (src / "src/net.cpp", old_allocator))
        # No source mutation occurred. Re-run the original positive binaries.
        for name in companions:
            rc = run([str(build / name)], name + ".recheck.log", timeout=300)
            results.append({"name": name + "_recheck", "exit_code": rc, "expected_exit_code": 0, "passed": rc == 0})
    finally:
        (reports / "commands.txt").write_text("\n".join(commands) + "\n")
        evidence = {"platform": platform.platform(), "python": sys.version, "source": CURRENT,
                    "old_source": PREVIOUS, "old_allocator_sha256": ALLOCATOR_SHA256,
                    "compiler": subprocess.check_output([args.cxx, "--version"], text=True).splitlines()[0],
                    "scope": "Linux x86-64 SSE2 CPU; no Vulkan, ARM/RISC-V, model weights or CMake execution. Convolution is generic FP32; other selected arithmetic/layout operators are x86.",
                    "results": results}
        (reports / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return 0 if results and all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
