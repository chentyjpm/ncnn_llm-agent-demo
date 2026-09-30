#!/usr/bin/env python3
"""Actual isolated ncnn Vulkan preflight; software dispatch and CPU fallback.

Hardware device failures are retained as failures for that device, not passed
GPU tests. Software ICDs exercise Vulkan commands, NOT GPU performance or full
Qwen Image generation. No model weights are involved.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_agent.device import probe, select, validate_report


def invoke(binary, *, software=False, extra=None):
    saved = {key: os.environ.get(key) for key in (extra or {})}
    try:
        os.environ.update(extra or {})
        # Passing the probe's own path lets the same real host coordinator find
        # its canonical sibling. No test double replaces its child processes.
        data = probe([str(binary.resolve(strict=True))], software=software)
        if data.get('reason'):
            raise RuntimeError('Probe enumeration unavailable: ' + str(data))
        return validate_report(data)
    finally:
        for key, value in saved.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'ok': False, 'scope': __doc__, 'commit': os.getenv('GITHUB_SHA')}
    try:
        report['auto'] = invoke(args.binary)
        report['auto_selection'] = select({'device': 'auto'}, 'llm', report=report['auto'])
        if any(d['compute_ok'] and not d['hardware'] for d in report['auto']['devices']):
            raise AssertionError('auto must not use software Vulkan')
        expected = 'vulkan' if any(d['compute_ok'] and d['hardware'] for d in report['auto']['devices']) else 'cpu'
        if report['auto_selection']['selected'] != expected:
            raise AssertionError('Selection disagrees with actual per-device computation')
        if os.environ.get('LOCAL_AGENT_TEST_SOFTWARE_VK') == '1':
            report['software_compute'] = invoke(args.binary, software=True)
            if not any(d['compute_ok'] and not d['hardware'] for d in report['software_compute']['devices']):
                raise AssertionError('real software Vulkan dispatch did not pass')
            report['no_driver'] = invoke(args.binary, extra={
                'VK_DRIVER_FILES': '/nonexistent/local-agent-vulkan.json',
                'VK_ICD_FILENAMES': '/nonexistent/local-agent-vulkan.json'})
            if report['no_driver']['devices']:
                raise AssertionError('invalid ICD did not disable Vulkan')
        # These are real native error paths, not parser fixtures.
        report['invalid_arguments'] = []
        for argv in (['--check-device'], ['--check-device', '-1'],
                     ['--check-device', '16'], ['--check-device', '1junk']):
            p = subprocess.run([str(args.binary.resolve()), *argv], capture_output=True,
                               text=True, timeout=15, check=False)
            if p.returncode != 2 or p.stdout:
                raise AssertionError('Invalid device argument did not fail cleanly')
            report['invalid_arguments'].append({'argv': argv, 'returncode': p.returncode, 'stderr': p.stderr})
        report['ok'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
