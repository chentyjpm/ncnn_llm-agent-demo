#!/usr/bin/env python3
"""Opt-in real MiniCPM image-question smoke test; no downloads or tool execution.

Image paths are relative to the configured workspace, as in the application.
Nonempty output alone does not establish image understanding. Use --expect for
an explicit case-insensitive phrase check, and inspect the recorded answer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_agent.backends import NcnnBridgeBackend
from local_agent.cli import load_config
from local_agent.paths import Workspace
from local_agent.vision_input import image_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--image', required=True, help='PNG/JPEG path relative to configured workspace')
    parser.add_argument('--prompt', default='Describe this image in one short sentence.')
    parser.add_argument('--expect', help='Optional phrase required in the answer (case-insensitive)')
    args = parser.parse_args(argv)
    if args.expect is not None and not args.expect.strip():
        parser.error('--expect must contain a nonempty phrase')
    report = {'test_kind': 'REAL_MODEL_VISION_SMOKE_TEST', 'ok': False,
              'platform': platform.system(), 'prompt': args.prompt,
              'semantic_check': 'required phrase' if args.expect else 'not automatically verified'}
    backend = None
    started = time.monotonic()
    try:
        config = load_config(args.config)
        if config['llm'].get('backend') != 'ncnn_bridge':
            raise ValueError('Only the real native bridge backend is accepted')
        ws = Workspace(Path(config['workspace']))
        image = image_path(ws, args.image)
        report['image'] = {'workspace_relative_path': args.image,
                           'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
        backend = NcnnBridgeBackend(config['llm'], ROOT)
        text = backend.complete_with_image([
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': args.prompt}], ws, args.image)
        report['answer'] = text
        report['device_selection'] = backend.device_selection
        report['ok'] = bool(text.strip()) and (args.expect is None or args.expect.casefold() in text.casefold())
        if args.expect is not None:
            report['expected_phrase'] = args.expect
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        if backend is not None:
            try:
                backend.release()
            except Exception as error:
                report['ok'] = False
                report['shutdown_error'] = f'{type(error).__name__}: {error}'
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        output = ROOT / 'reports' / 'real-vision-smoke-result.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
