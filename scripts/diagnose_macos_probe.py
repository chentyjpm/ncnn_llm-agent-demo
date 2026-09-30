#!/usr/bin/env python3
"""Reproduce the ac809c8 native probe with real macOS Vulkan and LLDB.

Uses identified prior-run artifacts ONLY for diagnosis, not release acceptance.
No weights, substitute compute kernels, skip-on-failure or altered checks.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.package_desktop import unpack
from scripts.macos_runtime import prepare_runtime
from local_agent.device import engine_env


def main():
    output = ROOT / 'reports/macos-probe-diagnostic'
    output.mkdir(parents=True, exist_ok=True)
    stage = ROOT / 'build/probe-diagnostic-payload'
    stage.mkdir(parents=True, exist_ok=True)
    report = {'scope': __doc__, 'source_commit': 'ac809c80e41c9530e8c8680f5ea0be868feb64cb',
              'source_run': '36646431074', 'diagnostic_commit': os.getenv('GITHUB_SHA'), 'engines': {}}
    try:
        for component in ('bridge', 'image'):
            archives = list((ROOT / 'build/probe-inputs' / component).glob('*.tar.gz'))
            if len(archives) != 1:
                raise ValueError('Expected one identified prior native archive')
            unpack(archives[0], stage / 'engines')
            folder = stage / 'engines' / component
            info = json.loads((folder / 'BUILD_INFO.json').read_text())
            if info['commit'] != report['source_commit'] or str(info['run_id']) != report['source_run']:
                raise ValueError('Unexpected diagnostic artifact identity')
            probe = folder / 'ncnn_device_probe'
            if hashlib.sha256(probe.read_bytes()).hexdigest() != info['probe_sha256']:
                raise ValueError('Diagnostic probe checksum mismatch')
        report['runtime'] = prepare_runtime(stage)
        for component in ('bridge', 'image'):
            probe = stage / 'engines' / component / 'ncnn_device_probe'
            # Relocation changes Mach-O bytes; reseal the local diagnostic copy.
            subprocess.run(['codesign', '--force', '--sign', '-', str(probe)], check=True)
            environment = dict(os.environ, **engine_env([str(probe)]))
            result = subprocess.run([str(probe)], capture_output=True, text=True, env=environment, timeout=45)
            record = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
            debugger = ['lldb', '--batch', '--no-lldbinit', '-o', 'settings set target.disable-aslr false',
                        '-o', 'run', '-k', 'thread backtrace all', '-k', 'register read', '--', str(probe)]
            try:
                trace = subprocess.run(debugger, capture_output=True, text=True, env=environment, timeout=90)
                record['lldb_returncode'] = trace.returncode
                (output / (component + '-lldb.txt')).write_text(trace.stdout + '\n' + trace.stderr, encoding='utf-8')
            except subprocess.TimeoutExpired as exc:
                record['lldb_error'] = str(exc)
            report['engines'][component] = record
            print(component, json.dumps(record), flush=True)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if len(report['engines']) == 2 and all(r['returncode'] == 0 for r in report['engines'].values()) else 1


if __name__ == '__main__': raise SystemExit(main())
