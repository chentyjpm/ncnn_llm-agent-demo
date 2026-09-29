#!/usr/bin/env python3
"""Actual ncnn Vulkan preflight; optional Mesa software compute and missing ICD.

Software ICDs exercise Vulkan commands, NOT physical GPU performance or full
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
from local_agent.device import validate_report


def invoke(binary, args=(), extra=None):
    result = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=45,
                            env=dict(os.environ, **(extra or {})), check=False)
    if result.returncode != 0:
        raise RuntimeError(f'Native probe exit {result.returncode}: {result.stderr[-3000:]}')
    return validate_report(json.loads(result.stdout))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'ok': False, 'scope': __doc__, 'commit': os.getenv('GITHUB_SHA')}
    try:
        report['auto'] = invoke(args.binary)
        if any(d['compute_ok'] and not d['hardware'] for d in report['auto']['devices']):
            raise AssertionError('auto must not use software Vulkan')
        if os.environ.get('LOCAL_AGENT_TEST_SOFTWARE_VK') == '1':
            report['software_compute'] = invoke(args.binary, ['--include-software'])
            if not any(d['compute_ok'] and not d['hardware'] for d in report['software_compute']['devices']):
                raise AssertionError('real software Vulkan dispatch did not pass')
            report['no_driver'] = invoke(args.binary, extra={
                'VK_DRIVER_FILES': '/nonexistent/local-agent-vulkan.json',
                'VK_ICD_FILENAMES': '/nonexistent/local-agent-vulkan.json'})
            if report['no_driver']['devices']:
                raise AssertionError('invalid ICD did not disable Vulkan')
        report['ok'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
