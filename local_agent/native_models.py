"""Validate reviewed native bundles. No remote code or unreviewed URL discovery."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re
from .native_catalog import NATIVE_MODELS

MIRROR = 'https://mirrors.sdu.edu.cn/ncnn_modelzoo/'


def manifest(model_id: str) -> dict:
    from .model_sources import safe_name
    item = NATIVE_MODELS[model_id]
    files = copy.deepcopy(item['files'])
    names=set()
    for f in files:
        name=safe_name(f['name'])
        if name in names or name.startswith('.') or not name.endswith(('.bin','.param','.json','.txt','.f32')):
            raise ValueError('Unexpected native model file')
        names.add(name)
        if f['remote'] != item['repository']+'/'+name or f['algorithm']!='sha256' or not re.fullmatch('[a-f0-9]{64}',f['digest']):
            raise ValueError('Native model manifest identity/checksum mismatch')
        if type(f['bytes']) is not int or not 0<f['bytes']<=16*1024**3:
            raise ValueError('Invalid native model file size')
    revision=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if revision != item['revision'] or 'model.json' not in names or sum(f['bytes'] for f in files)!=item['download_bytes']:
        raise ValueError('Native manifest changed without review')
    return {'id':model_id,'provider':'sdu','provider_name':'ncnn 上游镜像（SDU）','repository':item['repository'],
            'revision':revision,'files':files,'download_bytes':item['download_bytes'],
            'installed_estimate_bytes':item['download_bytes'],'disk_required_bytes':item['download_bytes']+1024**3}


def download_url(m: dict, f: dict) -> str:
    expected=manifest(m['id'])
    if m.get('provider')!='sdu' or any(m.get(k)!=expected[k] for k in ('repository','revision')) or f not in expected['files']:
        raise ValueError('Native download differs from reviewed manifest')
    return MIRROR+f['remote']


def validate_install(root: Path, model_id: str) -> None:
    """Called after all source hashes verified and before publishing READY.json."""
    from .model_sources import safe_name,digest
    item=NATIVE_MODELS[model_id]
    root=root.resolve(); m=manifest(model_id)
    allowed={f['name'] for f in m['files']}
    p=root/'model.json'
    expected=next(f for f in m['files'] if f['name']=='model.json')
    if p.stat().st_size!=expected['bytes'] or digest(p)!=expected['digest']:
        raise ValueError('Native model config differs from reviewed bytes')
    config=json.loads(p.read_text(encoding='utf-8'))
    if config.get('type')!=item['expected_type'] or config!=item['config']:
        raise ValueError('Unexpected native architecture/config')
    def walk(value):
        if isinstance(value,dict):
            for key,val in value.items():
                if isinstance(val,str) and (key.endswith(('_param','_bin','_file')) or val.endswith(('.ncnn.bin','.ncnn.param','.f32')):
                    name=safe_name(val)
                    target=root/name
                    if name not in allowed or not target.resolve().is_relative_to(root) or target.is_symlink() or not target.is_file():
                        raise ValueError('Native config references an unreviewed/missing file')
                elif isinstance(val,(dict,list)):walk(val)
        elif isinstance(value,list):
            for val in value:walk(val)
    walk(config)
    if config.get('tokenizer',{}).get('type') not in ('bpe','bbpe'):
        raise ValueError('Tokenizer not supported by this text bridge')
