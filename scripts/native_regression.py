#!/usr/bin/env python3
"""Model-free regression tests against a real ELF/PE/Mach-O executable.

No model downloads, inference, GPU initialization or generated stand-in program.
Each case runs the supplied binary and checks its real exit status and diagnostics.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tempfile
import time


def validate_binary(binary: Path) -> Path:
    binary = binary.resolve(strict=True)
    if not binary.is_file() or binary.stat().st_size < 4:
        raise ValueError('Missing or empty native executable')
    with binary.open('rb') as f:
        magic = f.read(4)
    formats = (b'\x7fELF', b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
               b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')
    if not (magic in formats or magic[:2] == b'MZ'):
        raise ValueError('Expected an ELF, PE or Mach-O executable, not a script/fake backend')
    return binary


def run_case(argv: list[str], expected_code: int, diagnostic: str, *,
             stdout_empty: bool = False, timeout: float = 30) -> dict:
    started = time.monotonic()
    record = {'argv': argv, 'expected_code': expected_code, 'diagnostic': diagnostic}
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                encoding='utf-8', errors='replace', timeout=timeout,
                                check=False, shell=False)
        record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        errors = []
        if result.returncode != expected_code:
            errors.append(f'exit {result.returncode}, expected {expected_code}')
        if diagnostic not in result.stdout + result.stderr:
            errors.append('expected diagnostic not emitted by executable')
        if stdout_empty and result.stdout:
            errors.append('error/log text leaked into the JSON-RPC stdout channel')
        if errors:
            raise AssertionError('; '.join(errors))
        record['status'] = 'passed'
    except Exception as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    record['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return record


def cases(component: str, scratch: Path) -> list[tuple]:
    if component == 'bridge':
        missing = '--model must point to a converted ncnn model directory'
        bad = 'Unsupported/incomplete bridge argument'
        result = [
            ('help', ['--help'], 0, 'Usage: ncnn_agent_bridge', False),
            ('short_help', ['-h'], 0, 'Usage: ncnn_agent_bridge', False),
            ('version', ['--version'], 0, 'ncnn_agent_bridge 0.1.0', False),
            ('no_model', [], 2, missing, True),
            ('missing_model_path', ['--model', str(scratch / 'not-present')], 2, missing, True),
            ('empty_model_directory', ['--model', str(scratch / 'empty')], 2, missing, True),
            ('malformed_model_json', ['--model', str(scratch / 'malformed')], 2, 'parse_error', True),
            ('unknown_option', ['--no-such-option'], 2, bad, True),
            ('missing_option_value', ['--model'], 2, bad, True),
            ('threads_minimum', ['--threads', '1'], 2, missing, True),
            ('threads_maximum', ['--threads', '128'], 2, missing, True),
            ('device_zero', ['--vulkan-device', '0'], 2, missing, True),
        ]
        for name, value in [('letters', 'abc'), ('suffix', '4junk'), ('fraction', '4.5'),
                            ('zero', '0'), ('negative', '-1'), ('too_large', '129'),
                            ('overflow', '999999999999999999999')]:
            result.append((f'threads_{name}', ['--threads', value], 2, 'Invalid value for --threads', True))
        for name, value in [('suffix', '0junk'), ('negative', '-1'), ('overflow', '2147483648')]:
            result.append((f'device_{name}', ['--vulkan-device', value], 2,
                           'Invalid value for --vulkan-device', True))
        return result
    if component == 'image':
        return [
            ('help', ['-h'], 0, 'Usage: qwenimage-ncnn-vulkan', False),
            ('unknown_option', ['--no-such-option'], 2, 'unknown option', False),
            ('missing_prompt_value', ['-p'], 2, 'missing value for -p', False),
            ('invalid_size', ['-s', 'invalid'], 2, 'invalid image size', False),
            ('zero_size', ['-s', '0,512'], 2, 'invalid image size', False),
            ('zero_steps', ['-l', '0'], 2, 'invalid steps', False),
            ('invalid_gpu_id', ['-g', '-2'], 2, 'invalid gpu-id', False),
            ('negative_seed', ['-r', '-1'], 2, 'invalid random-seed', False),
            ('nonfinite_lora_scale', ['--lora-scale', 'NaN'], 2, 'invalid LoRA scale', False),
        ]
    raise ValueError('Unknown native component')


def run_suite(binary: Path, component: str, output: Path) -> dict:
    binary = validate_binary(binary)
    records = []
    with tempfile.TemporaryDirectory(prefix='native-regression-') as tmp:
        scratch = Path(tmp)
        (scratch / 'empty').mkdir()
        (scratch / 'malformed').mkdir()
        (scratch / 'malformed/model.json').write_text('{invalid', encoding='utf-8')
        for name, args, code, diagnostic, empty in cases(component, scratch):
            record = run_case([str(binary), *args], code, diagnostic, stdout_empty=empty)
            record['name'] = name
            records.append(record)
            print(f'{name}: {record["status"]}', flush=True)
    report = {'component': component, 'binary': str(binary),
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'platform': platform.platform(), 'machine': platform.machine(),
              'scope': 'Real native executable; model-free CLI/error-path tests only. No inference or GPU execution.',
              'tests_run': len(records), 'passed': sum(r['status'] == 'passed' for r in records),
              'failed': sum(r['status'] != 'passed' for r in records), 'tests': records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--component', required=True, choices=['bridge', 'image'])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = run_suite(args.binary, args.component, args.output)
    print(f'{report["passed"]}/{report["tests_run"]} native tests passed')
    return 0 if report['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
