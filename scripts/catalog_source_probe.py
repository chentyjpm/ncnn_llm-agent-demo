#!/usr/bin/env python3
"""Read official provider metadata/config only; never download large weights.
Evidence for provider adapters. Network failures are reported, not mocked.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
from urllib.parse import urlencode

REPOS = ['Qwen/Qwen2.5-0.5B-Instruct', 'Qwen/Qwen2.5-Coder-0.5B-Instruct', 'Qwen/Qwen2.5-1.5B-Instruct']


def read(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'LocalAgent-provider-check/1'}), timeout=30) as r:
        data = r.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Metadata exceeded size limit')
    return json.loads(data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('reports/provider-catalog'))
    a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=True)
    results = []
    for provider in ('huggingface', 'modelscope'):
        for repo in REPOS:
            record = {'provider': provider, 'repository': repo, 'ok': False}
            try:
                if provider == 'huggingface':
                    meta = read(f'https://huggingface.co/api/models/{repo}?blobs=true')
                    record['revision'] = meta['sha']; record['files'] = meta['siblings']
                    record['config'] = read(f'https://huggingface.co/{repo}/resolve/{meta["sha"]}/config.json')
                else:
                    branches = read(f'https://modelscope.cn/api/v1/models/{repo}/revisions')
                    record['revisions'] = branches
                    meta = read(f'https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Recursive=true')
                    record['files_response'] = meta
                    record['config'] = read(f'https://modelscope.cn/api/v1/models/{repo}/repo?' + urlencode({'Revision':'master','FilePath':'config.json'}))
                record['ok'] = True
            except Exception as e:
                record['error'] = f'{type(e).__name__}: {e}'
            results.append(record)
            print(provider, repo, record['ok'], record.get('error', ''))
    (a.output / 'metadata.json').write_text(json.dumps({'scope':'Live metadata/config only; not full-model inference', 'results':results}, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(r['ok'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
