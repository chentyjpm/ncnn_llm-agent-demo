#!/usr/bin/env python3
"""Maintainer-only upstream model inventory; streams real files into SHA256.
No model code, archives, credentials or user data. Output needs review before
becoming the installed application's immutable download manifest.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import urlsplit
import urllib.request

ROOT = 'https://mirrors.sdu.edu.cn/ncnn_modelzoo/'
MODELS = {
    'qwen3_0.6b': ('Qwen3 0.6B', 'qwen3', 'fp32'),
    'qwen3_0.6b_int8': ('Qwen3 0.6B INT8', 'qwen3', 'int8'),
    'minicpm4_0.5b': ('MiniCPM4 0.5B', 'minicpm4', 'fp32'),
    'minicpm4_0.5b_int8': ('MiniCPM4 0.5B INT8', 'minicpm4', 'int8'),
    'youtu_llm': ('Youtu LLM', 'youtu_llm', 'fp32'),
    'youtu_llm_int8': ('Youtu LLM INT8', 'youtu_llm', 'int8'),
    'qwen3.5_0.8b': ('Qwen3.5 0.8B · text only', 'qwen3.5', 'fp32'),
    'qwen3.5_0.8b_int8': ('Qwen3.5 0.8B INT8 · text only', 'qwen3.5', 'int8'),
    'qwen2.5_vl_3b': ('Qwen2.5 VL 3B · text only', 'qwen2.5_vl', 'fp32'),
    'qwen2.5_vl_3b_int8': ('Qwen2.5 VL 3B INT8 · text only', 'qwen2.5_vl', 'int8'),
}


def name(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise ValueError('Invalid model filename')
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or p.as_posix() != value or p.suffix not in ('.json','.bin','.param','.txt','.f32'):
        raise ValueError('Invalid model file path/type')
    return value


def request(url):
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith(ROOT): raise ValueError('Mirror redirect escaped allowed directory')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(Redirect()).open(
        urllib.request.Request(url, headers={'User-Agent':'LocalAgent-model-inventory/1'}), timeout=60)


def referenced_files(config):
    result = {'model.json'}
    def walk(value):
        if isinstance(value, dict):
            for key, val in value.items():
                if isinstance(val, str) and (key.endswith(('_bin','_param','_file')) or val.endswith(('.ncnn.bin','.ncnn.param','.f32'))):
                    result.add(name(val))
                elif isinstance(val, (dict,list)): walk(val)
        elif isinstance(value, list):
            for val in value: walk(val)
    walk(config)
    if len(result) > 50: raise ValueError('Unexpected number of referenced files')
    return sorted(result)


def inventory(model_id, output):
    begin=time.monotonic(); meta={'model_id':model_id,'ok':False,'files':[],'utc':datetime.now(timezone.utc).isoformat()}
    try:
        if model_id not in MODELS: raise ValueError('Unknown upstream model')
        with request(ROOT+model_id+'/model.json') as r: raw=r.read(1024*1024+1)
        if len(raw)>1024*1024: raise ValueError('Oversized model config')
        config=json.loads(raw); meta['config']=config
        for file in referenced_files(config):
            sha=hashlib.sha256(); size=0
            if file=='model.json': sha.update(raw);size=len(raw)
            else:
                print('Hash actual bytes:',model_id,file,flush=True)
                with request(ROOT+model_id+'/'+file) as r:
                    while block:=r.read(4*1024*1024):
                        size+=len(block)
                        if size>16*1024**3 or time.monotonic()-begin>1800: raise ValueError('Inventory byte/time budget exceeded')
                        sha.update(block)
            meta['files'].append({'name':file,'remote':model_id+'/'+file,'bytes':size,'digest':sha.hexdigest(),'algorithm':'sha256'})
        meta.update(ok=True,name=MODELS[model_id][0],expected_type=config.get('type'),precision=MODELS[model_id][2],
                    download_bytes=sum(x['bytes'] for x in meta['files']),provider='sdu',repository=model_id)
        meta['revision']=hashlib.sha256(json.dumps(meta['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    except Exception as e: meta['error']=f'{type(e).__name__}: {e}'
    meta['elapsed_seconds']=round(time.monotonic()-begin,3)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(meta,ensure_ascii=False,indent=2),flush=True)
    return 0 if meta['ok'] else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',choices=list(MODELS),required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();raise SystemExit(inventory(a.model,a.output))
