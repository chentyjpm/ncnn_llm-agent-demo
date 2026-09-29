#!/usr/bin/env python3
"""Export the exact official Qwen2.5-0.5B-Instruct snapshot to ncnn.

CI-only conversion utility. No pickle, remote Python, torch or GPU is needed.
Numpy is imported only for conversion; the Agent keeps its stdlib-only runtime.
Graph uses ncnn Gemm/RMSNorm/RotaryEmbed/SDPA with the pinned runtime KV ABI.
This is a model-specific exporter, NOT support for arbitrary Qwen architectures.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
import mmap
from pathlib import Path
import struct
import time
import urllib.request

MODEL_ID = 'Qwen/Qwen2.5-0.5B-Instruct'
REVISION = '7ae557604adf67be50417f59c2c2f167def9a775'
WEIGHT_SHA256 = 'fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe'
FILES = ('config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'LICENSE')
EXPECTED_CONFIG = {'model_type': 'qwen2', 'hidden_size': 896, 'intermediate_size': 4864,
                   'num_hidden_layers': 24, 'num_attention_heads': 14, 'num_key_value_heads': 2,
                   'vocab_size': 151936, 'tie_word_embeddings': True, 'use_sliding_window': False,
                   'rms_norm_eps': 1e-6, 'rope_theta': 1000000.0}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validate_config(config: dict) -> None:
    for key, value in EXPECTED_CONFIG.items():
        if config.get(key) != value:
            raise ValueError(f'Not the supported Qwen2.5-0.5B geometry: {key}')


def download_snapshot(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    records = {}
    for name in FILES:
        url = f'https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}?download=true'
        target = output / name
        for attempt in range(3):
            partial = output / (name + '.part')
            try:
                print('Download', name, 'from pinned official snapshot', flush=True)
                request = urllib.request.Request(url, headers={'User-Agent': 'ncnn-agent-cpu-ci/0.1'})
                limit = 1100 * 1024 * 1024 if name == 'model.safetensors' else 32 * 1024 * 1024
                size = 0
                with urllib.request.urlopen(request, timeout=120) as response, partial.open('wb') as f:
                    while block := response.read(4 * 1024 * 1024):
                        size += len(block)
                        if size > limit:
                            raise ValueError('Download exceeded expected size limit')
                        f.write(block)
                digest = sha256(partial)
                if name == 'model.safetensors' and digest != WEIGHT_SHA256:
                    raise ValueError('Official weight SHA-256 mismatch')
                partial.replace(target)
                records[name] = {'bytes': size, 'sha256': digest}
                break
            except Exception:
                partial.unlink(missing_ok=True)
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
    validate_config(json.loads((output / 'config.json').read_text(encoding='utf-8')))
    records = {'model_id': MODEL_ID, 'revision': REVISION, 'files': records}
    (output / 'SOURCE.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    return records


class SafeWeights:
    """Bounded safetensors reader for BF16/F32 tensors; no code deserialization."""
    def __init__(self, path: Path):
        self.file = path.open('rb')
        try:
            self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
            if len(self.data) < 8:
                raise ValueError('Truncated safetensors file')
            count = struct.unpack('<Q', self.data[:8])[0]
            if not 1 <= count <= 16 * 1024 * 1024 or 8 + count > len(self.data):
                raise ValueError('Invalid safetensors header length')
            self.header = json.loads(self.data[8:8 + count])
            self.start = 8 + count
        except Exception:
            self.close()
            raise

    def read(self, name: str, shape: tuple[int, ...]):
        import numpy as np
        item = self.header[name]
        if item['shape'] != list(shape):
            raise ValueError(f'Unexpected tensor shape: {name}')
        dtype = item['dtype']
        size = {'BF16': 2, 'F32': 4}.get(dtype)
        a, b = item['data_offsets']
        if size is None or not (0 <= a <= b <= len(self.data) - self.start) or b - a != math.prod(shape) * size:
            raise ValueError(f'Invalid tensor offsets/dtype: {name}')
        if dtype == 'BF16':
            raw = np.frombuffer(self.data, dtype='<u2', count=math.prod(shape), offset=self.start + a)
            return (raw.astype('<u4') << 16).view('<f4').reshape(shape)
        return np.frombuffer(self.data, dtype='<f4', count=math.prod(shape), offset=self.start + a).copy().reshape(shape)

    def close(self):
        if hasattr(self, 'data'):
            self.data.close()
        self.file.close()


class Graph:
    def __init__(self):
        self.nodes = []
        self.produced = set()

    def add(self, kind: str, name: str, inputs: list[str], outputs: list[str], params: str = ''):
        if any(x not in self.produced for x in inputs):
            raise ValueError('Graph consumes undefined blob')
        if len(set(outputs)) != len(outputs) or any(x in self.produced for x in outputs):
            raise ValueError('Graph produces duplicate blob')
        self.nodes.append((kind, name, inputs, outputs, params))
        self.produced.update(outputs)

    def text(self) -> str:
        uses = Counter(x for _, _, inputs, _, _ in self.nodes for x in inputs)
        used = Counter()
        lines = []
        blob_count = 0
        for kind, name, inputs, outputs, params in self.nodes:
            expanded = []
            for x in inputs:
                expanded.append(f'{x}_split_{used[x]}' if uses[x] > 1 else x)
                used[x] += 1
            lines.append(' '.join([kind, name, str(len(inputs)), str(len(outputs)), *expanded, *outputs, params]).rstrip())
            blob_count += len(outputs)
            # Explicit fanout is needed for ncnn light-mode/in-place operators.
            for x in outputs:
                if uses[x] > 1:
                    names = [f'{x}_split_{i}' for i in range(uses[x])]
                    lines.append(' '.join(['Split', f'split_{x}', '1', str(len(names)), x, *names]))
                    blob_count += len(names)
        return f'7767517\n{len(lines)} {blob_count}\n' + '\n'.join(lines) + '\n'


def export_model(source: Path, output: Path) -> dict:
    config = json.loads((source / 'config.json').read_text(encoding='utf-8'))
    validate_config(config)
    if sha256(source / 'model.safetensors') != WEIGHT_SHA256:
        raise ValueError('Refusing non-pinned source weights')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Export requires a new empty directory')
    output.mkdir(parents=True, exist_ok=True)
    w = SafeWeights(source / 'model.safetensors')
    try:
        # Embedding and tied projection share the same binary bytes.
        embedding = w.read('model.embed_tokens.weight', (151936, 896))
        with (output / 'embed.bin').open('wb') as f:
            f.write(struct.pack('<I', 0)); embedding.tofile(f)
        del embedding
        g = Graph(); g.add('Input', 'input', [], ['in0'])
        g.add('Embed', 'embed', ['in0'], ['out0'], f'0=896 1=151936 2=0 3={151936 * 896}')
        (output / 'embed.param').write_text(g.text(), encoding='utf-8')
        g = Graph(); g.add('Input', 'input', [], ['in0'])
        g.add('Gemm', 'lm_head', ['in0'], ['out0'], '3=1 5=1 6=1 8=151936 9=896 10=-1')
        (output / 'head.param').write_text(g.text(), encoding='utf-8')
        g = Graph()
        for x in ['in0', 'in1', 'in2', 'in3'] + [f'cache_{t}{i}' for i in range(24) for t in ('k', 'v')]:
            g.add('Input', 'input_' + x, [], [x])
        with (output / 'decoder.bin').open('wb') as f:
            def tensor(key, shape, tagged=False):
                array = w.read(key, shape)
                if tagged:
                    f.write(struct.pack('<I', 0))
                array.tofile(f)
            def linear(name, src, n, k, key, bias=False):
                g.add('Gemm', name, [src], [name], f'3=1 5=1 6=1 8={n} 9={k} 10={4 if bias else -1}')
                tensor(key + '.weight', (n, k), True)
                if bias: tensor(key + '.bias', (n,), True)
                return name
            def norm(name, src, key):
                g.add('RMSNorm', name, [src], [name], '0=896 1=1e-6 2=1')
                tensor(key, (896,))
                return name
            x = 'in0'
            for i in range(24):
                pre = f'model.layers.{i}.'
                residual = x
                x = norm(f'l{i}_ln1', x, pre + 'input_layernorm.weight')
                projections = []
                for t, heads in [('q', 14), ('k', 2), ('v', 2)]:
                    p = f'l{i}_{t}'
                    linear(p, x, heads * 64, 896, pre + f'self_attn.{t}_proj', True)
                    g.add('Reshape', p + '_shape', [p], [p + '_shape'], f'0=64 1={heads} 2=-1')
                    g.add('Permute', p + '_heads', [p + '_shape'], [p + '_heads'], '0=2')
                    p += '_heads'
                    if t != 'v':
                        g.add('RotaryEmbed', p + '_rope', [p, 'in2', 'in3'], [p + '_rope'], '0=0')
                        p += '_rope'
                    projections.append(p)
                attn = f'l{i}_attn'
                g.add('SDPA', attn, [*projections, 'in1', f'cache_k{i}', f'cache_v{i}'],
                      [attn, f'out_cache_k{i}', f'out_cache_v{i}'], '5=1 6=0.125 7=1')
                g.add('Permute', attn + '_perm', [attn], [attn + '_perm'], '0=2')
                g.add('Reshape', attn + '_flat', [attn + '_perm'], [attn + '_flat'], '0=896 1=-1')
                x = linear(f'l{i}_o', attn + '_flat', 896, 896, pre + 'self_attn.o_proj')
                g.add('BinaryOp', f'l{i}_res1', [residual, x], [f'l{i}_res1'], '0=0')
                residual = f'l{i}_res1'
                x = norm(f'l{i}_ln2', residual, pre + 'post_attention_layernorm.weight')
                gate = linear(f'l{i}_gate', x, 4864, 896, pre + 'mlp.gate_proj')
                up = linear(f'l{i}_up', x, 4864, 896, pre + 'mlp.up_proj')
                g.add('Swish', gate + '_act', [gate], [gate + '_act'])
                g.add('BinaryOp', f'l{i}_mul', [gate + '_act', up], [f'l{i}_mul'], '0=2')
                down = linear(f'l{i}_down', f'l{i}_mul', 896, 4864, pre + 'mlp.down_proj')
                x = f'l{i}_res2'
                g.add('BinaryOp', x, [residual, down], [x], '0=0')
            g.add('RMSNorm', 'final_norm', [x], ['out0'], '0=896 1=1e-6 2=1')
            tensor('model.norm.weight', (896,))
        (output / 'decoder.param').write_text(g.text(), encoding='utf-8')
        tok = json.loads((source / 'tokenizer.json').read_text(encoding='utf-8'))
        vocab = sorted(tok['model']['vocab'].items(), key=lambda kv: kv[1])
        if [v for _, v in vocab] != list(range(len(vocab))):
            raise ValueError('Tokenizer vocabulary is not contiguous')
        (output / 'vocab.txt').write_text(''.join(t + '\n' for t, _ in vocab), encoding='utf-8')
        merges = [' '.join(m) if isinstance(m, list) else m for m in tok['model']['merges']]
        (output / 'merges.txt').write_text('\n'.join(merges) + '\n', encoding='utf-8')
        added = sorted(tok['added_tokens'], key=lambda t: t['id'])
        if [t['id'] for t in added] != list(range(len(vocab), len(vocab) + len(added))):
            raise ValueError('Unexpected special token IDs')
        model = {'type': 'qwen2', 'source_model': MODEL_ID, 'source_revision': REVISION,
                 'params': {'decoder_param': 'decoder.param', 'decoder_bin': 'decoder.bin',
                            'embed_token_param': 'embed.param', 'embed_token_bin': 'embed.bin',
                            'proj_out_param': 'head.param', 'proj_out_bin': 'embed.bin'},
                 'setting': {'attn_cnt': 24, 'rope': {'type': 'RoPE', 'rope_head_dim': 64, 'rope_theta': 1000000.0},
                             'vision': {'type': 'close'}},
                 'tokenizer': {'type': 'bbpe', 'vocab_file': 'vocab.txt', 'merges_file': 'merges.txt',
                               'bos': '', 'eos': '<|im_end|>', 'additional_special_tokens': [t['content'] for t in added]}}
        (output / 'model.json').write_text(json.dumps(model, indent=2) + '\n', encoding='utf-8')
        (output / 'LICENSE').write_bytes((source / 'LICENSE').read_bytes())
        result = {'model_id': MODEL_ID, 'revision': REVISION, 'source_weight_sha256': WEIGHT_SHA256,
                  'format': 'ncnn FP32 weights, tied head reuses embedding file',
                  'files': {p.name: {'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in sorted(output.iterdir()) if p.is_file()}}
        (output / 'EXPORT.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2), flush=True)
        return result
    finally:
        w.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['download', 'export'])
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.stage == 'download': download_snapshot(a.source)
    elif a.output is None: p.error('--output required for export')
    else: export_model(a.source, a.output)


if __name__ == '__main__': main()
