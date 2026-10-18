#!/usr/bin/env python3
"""Decide whether a push needs large model downloads; never download anything.

Workflow/checkout maintenance is tested without models. Manual dispatch always
requests the full test. Ambiguous history fails instead of claiming a pass.
"""
from __future__ import annotations
import argparse
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    'image': ('scripts/qwenimage_real_test.py', 'scripts/web_image_smoke.py',
              'local_agent/image_tasks.py', 'tests/test_qwenimage_validation.py', 'native/image/**'),
    'turbo': ('scripts/qwenimage_turbo_smoke.py', 'local_agent/image_profiles.py',
              'local_agent/images.py', 'local_agent/image_tasks.py', 'local_agent/web.py',
              'tests/test_auto_image_routing.py', 'tests/test_turbo_http_acceptance.py',
              'local_agent/hf_download_pins.py', 'tests/test_image_turbo.py', 'native/image/**'),
    'catalog': ('local_agent/native_catalog.py', 'local_agent/native_models.py',
                'local_agent/model_catalog.py', 'scripts/native_catalog_smoke.py',
                'tests/test_native_catalog_acceptance.py'),
}


def image_dependencies(files: dict[str, str]) -> dict:
    dependencies = json.loads(files['ci/dependencies.json'])
    lock = json.loads(files['upstream-lock.json'])
    image = dependencies['qwenimage']
    if not isinstance(image, dict) or not all(k in image for k in ('repository', 'commit', 'ncnn_commit')):
        raise ValueError('Missing image dependency identity')
    official = [r for r in lock['repositories'] if r['name'] == 'nihui/qwenimage-ncnn-vulkan']
    if len(official) != 1:
        raise ValueError('Missing or ambiguous image upstream lock')
    return {'dependency': image, 'upstream': official[0]}


def requires_models(scope: str, changed: list[str], before: dict[str, str], after: dict[str, str]) -> tuple[bool, str]:
    for path in changed:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in PATHS[scope]):
            return True, 'Relevant model test input changed: ' + path
    if scope in ('image', 'turbo') and any(p in changed for p in ('ci/dependencies.json', 'upstream-lock.json')):
        if image_dependencies(before) != image_dependencies(after):
            return True, 'Qwen Image engine or its dedicated dependency pin changed'
    return False, 'No relevant model input changed; checkout/CI maintenance does not request large downloads'


def decide(scope: str, event: str, before: str, after: str, root: Path = ROOT) -> tuple[bool, str]:
    if event == 'workflow_dispatch':
        return True, 'Explicit manual model-test request'
    if event != 'push' or any(not re.fullmatch(r'[0-9a-f]{40}', sha) or sha == '0' * 40 for sha in (before, after)):
        raise ValueError('A push requires exact nonzero before/after commits; use workflow_dispatch for a full test')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, timeout=30)
    changed = git('diff', '--name-only', '--no-renames', before, after, '--').splitlines()
    locks = ('ci/dependencies.json', 'upstream-lock.json')
    old = {p: git('show', before + ':' + p) for p in locks} if scope != 'catalog' else {}
    new = {p: git('show', after + ':' + p) for p in locks} if scope != 'catalog' else {}
    return requires_models(scope, changed, old, new)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=PATHS, required=True)
    args = parser.parse_args()
    required, reason = decide(args.scope, os.environ.get('GITHUB_EVENT_NAME', ''),
                              os.environ.get('MODEL_SCOPE_BEFORE', ''), os.environ.get('GITHUB_SHA', ''))
    print(json.dumps({'models_required': required, 'reason': reason}))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('required=' + str(required).lower() + '\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write('Model downloads: ' + ('requested' if required else 'skipped') + '. ' + reason + '.\n')


if __name__ == '__main__':
    main()
