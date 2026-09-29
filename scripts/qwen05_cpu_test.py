#!/usr/bin/env python3
"""Real ncnn CPU inference and model-driven file-tool acceptance, no fake fallback."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import uuid
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_agent.agent import Agent, Audit
from local_agent.backends import NcnnBridgeBackend
from local_agent.paths import Workspace
from local_agent.tools import Registry, make_registry
from scripts.qwen05_export import MODEL_ID, REVISION, WEIGHT_SHA256, sha256


def verify_model(directory: Path) -> dict:
    meta = json.loads((directory / 'EXPORT.json').read_text(encoding='utf-8'))
    if (meta['model_id'], meta['revision'], meta['source_weight_sha256']) != (MODEL_ID, REVISION, WEIGHT_SHA256):
        raise ValueError('Wrong model identity')
    for name, data in meta['files'].items():
        if Path(name).name != name or sha256(directory / name) != data['sha256']:
            raise ValueError('Exported model file hash mismatch')
    return meta


def agent_checks(result: dict, events: list, ws: Workspace, name: str, content: str) -> dict:
    calls = [e for e in events if e['event'] == 'tool_result']
    written = any(e['tool'] == 'files.write' and e['arguments'].get('path') == name and e['result']['ok'] for e in calls)
    reads = [e for e in calls if e['tool'] == 'files.read' and e['arguments'].get('path') == name and e['result']['ok']]
    verified = any(e['result'].get('result', {}).get('content') == content for e in reads)
    try: correct = ws.read(name) == content
    except (ValueError, OSError): correct = False
    return {'agent_finished': result.get('ok') is True, 'model_requested_write': written,
            'model_requested_read': verified, 'actual_file_content': correct,
            'no_failed_tools': all(e['result']['ok'] for e in calls)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--binary', type=Path, required=True)
    ap.add_argument('--model', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    report = {'kind': 'REAL_QWEN05_NCNN_CPU', 'commit': os.getenv('GITHUB_SHA'), 'run_id': os.getenv('GITHUB_RUN_ID'),
              'platform': platform.platform(), 'model_id': MODEL_ID, 'revision': REVISION,
              'device': 'CPU', 'threads': 4, 'image': 'not_run', 'ok': False, 'text_cases': []}
    started = time.monotonic()
    backend = None
    try:
        report['model_manifest'] = verify_model(a.model)
        binary = a.binary.resolve(strict=True)
        report['binary_sha256'] = sha256(binary)
        config = {'command': [str(binary)], 'model': str(a.model.resolve()), 'vulkan': False,
                  'threads': 4, 'timeout': 180, 'max_new_tokens': 64}
        backend = NcnnBridgeBackend(config, ROOT)
        for name, question, regex in [
            ('arithmetic', 'What is 2 + 2? Reply with only the number.', r'\s*4[.!]?\s*'),
            ('english_fact', 'What is the capital of France? Reply with one word.', r'\s*Paris[.!]?\s*'),
            ('chinese_fact', '中国的首都是哪里？只回答城市名称。', r'\s*北京[。！!]?\s*')]:
            t = time.monotonic()
            text = backend.complete([{'role': 'system', 'content': 'You are a helpful assistant.'}, {'role': 'user', 'content': question}])
            passed = re.fullmatch(regex, text, re.IGNORECASE) is not None
            report['text_cases'].append({'name': name, 'prompt': question, 'output': text, 'passed': passed,
                                          'seconds': round(time.monotonic() - t, 3)})
            print(json.dumps(report['text_cases'][-1], ensure_ascii=False), flush=True)
        rpc = backend.rpc
        backend.release()
        report['runtime_stderr'] = rpc.stderr.decode('utf-8', errors='replace') if rpc else ''
        report['cpu_confirmed'] = 'Vulkan disabled, using CPU only' in report['runtime_stderr']
        # Narrow file tools only; the LLM still chooses and emits every action itself.
        ws = Workspace(a.output / ('workspace-' + uuid.uuid4().hex[:10]))
        all_tools = make_registry(ws)
        registry = Registry()
        for tool in ('files.write', 'files.read'): registry.add(all_tools.tools[tool])
        config['max_new_tokens'] = 192
        backend = NcnnBridgeBackend(config, ROOT)
        audit = Audit(a.output / 'agent-audit.jsonl')
        name, content = 'cpu-proof.txt', 'NCNN_CPU_' + uuid.uuid4().hex[:8]
        # Provide an explicit few-shot format example for this small-model smoke.
        # These are prompt text, not precomputed actions: the real model must
        # choose every response and substitute the fresh path/content.
        # No action parser or policy is relaxed.
        task = (
            'Example for a DIFFERENT task (do not execute this example):\n'
            'Task: write memo.txt containing hello, then read it.\n'
            'First assistant response: {"tool":"files.write","arguments":{"path":"memo.txt","content":"hello"}}\n'
            'After successful write tool result, assistant response: {"tool":"files.read","arguments":{"path":"memo.txt"}}\n'
            'After successful read tool result, assistant response: {"final":"Done"}\n'
            'End of example.\n\n'
            f'YOUR ACTUAL TASK: Write {name} with content exactly {content}, then read {name}.\n'
            f'The path argument must be exactly "{name}". Never use /tmp or an absolute path.\n'
            'Return only ONE JSON object per turn. For a tool call include only tool and arguments; '
            'do not include final. Wait for the tool result before the next action. '
            'After reading the correct file, return only a final object. Start with files.write.'
        )
        result = Agent(backend, registry, max_steps=8, audit=audit).run(task)
        checks = agent_checks(result, audit.events, ws, name, content)
        report['agent'] = {'result': result, 'checks': checks, 'passed': all(checks.values()),
                           'prompt_profile': 'few-shot file workflow with explicit relative-path instruction',
                           'scope': 'guided small-model task; every action is generated by the model, not a general agent reliability claim'}
        report['ok'] = report['cpu_confirmed'] and all(t['passed'] for t in report['text_cases']) and report['agent']['passed']
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if backend is not None:
            rpc = backend.rpc
            backend.release()
            if rpc: report['final_runtime_stderr'] = rpc.stderr.decode('utf-8', errors='replace')
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (a.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__': raise SystemExit(main())
