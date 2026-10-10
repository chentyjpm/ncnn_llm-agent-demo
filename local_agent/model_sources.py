"""Strict adapters for curated public Hugging Face and ModelScope repositories.
Only selected-provider metadata is read. No hidden HF dependency for ModelScope.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import ssl
from urllib.parse import quote, urlencode, urlsplit
import urllib.request
from .model_catalog import CATALOG, PROFILES, PROVIDERS
from .hf_download_pins import PINNED_HF

QWEN_FILES = {'config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'LICENSE'}
IMAGE_REQUIRED = {'processor/vocab.txt', 'processor/merges.txt', 'text_encoder/text_encoder.ncnn.param',
    'text_encoder/text_encoder.ncnn.bin', 'transformer/input.ncnn.param', 'transformer/input.ncnn.bin',
    'transformer/blocks.ncnn.param', 'transformer/blocks.ncnn.bin', 'transformer/output.ncnn.param',
    'transformer/output.ncnn.bin', 'vae/decoder.ncnn.param', 'vae/decoder.ncnn.bin'}


HF_DOWNLOAD_ROUTES = {
    'direct': {'name': 'Hugging Face 原站', 'endpoint': 'https://huggingface.co', 'third_party': False},
    'hf_mirror': {'name': 'HF-Mirror 镜像（第三方）', 'endpoint': 'https://hf-mirror.com', 'third_party': True},
}


def download_route(provider, route='direct'):
    """A reviewed enum, never a user URL, proxy setting, token, or HF_ENDPOINT."""
    if not isinstance(route, str) or route not in HF_DOWNLOAD_ROUTES:
        raise ValueError('下载线路仅允许 direct / hf_mirror，不接受自定义地址')
    if provider != 'huggingface' and route != 'direct':
        raise ValueError('HF-Mirror 仅适用于 Hugging Face 来源，不支持魔搭或 SDU 模型')
    return dict(HF_DOWNLOAD_ROUTES[route])


def response_host(response, requested_url):
    """Report only the hostname, never signed CDN query parameters."""
    url = response.geturl() if hasattr(response, 'geturl') else getattr(response, 'url', None)
    return _https_host(url or requested_url)


def verify_mirror_manifest(model_id, repository, revision, files):
    pin = PINNED_HF.get(model_id)
    if not pin or (repository, revision) != (pin['repository'], pin['revision']):
        raise ValueError('镜像模型版本不在已审核清单中；请更新应用或手动选择原站')
    expected = {f['name']: f for f in pin['files']}
    actual = {f['name']: f for f in files}
    if len(actual) != len(files) or actual != expected:
        raise ValueError('镜像文件清单与独立核实的 HF 原站大小／哈希不一致，已停止')


def safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Invalid model filename')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or name != p.as_posix():
        raise ValueError('Invalid model path')
    return name


def digest(path: Path, algorithm='sha256', cancelled=None) -> str:
    if algorithm not in ('sha256', 'git'):
        raise ValueError('Unsupported checksum algorithm')
    h = hashlib.new('sha1' if algorithm == 'git' else 'sha256')
    if algorithm == 'git': h.update(f'blob {path.stat().st_size}\0'.encode())
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            if cancelled and cancelled(): raise InterruptedError('已取消校验')
            h.update(block)
    return h.hexdigest()


def source_for(model_id, provider):
    if not isinstance(model_id, str) or model_id not in CATALOG:
        raise ValueError('Unknown model')
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError('请选择 ModelScope、Hugging Face 或 ncnn 上游镜像')
    item = CATALOG[model_id]
    if not item.get('installable', True): raise ValueError('该模型需要专用适配，尚不能安装为聊天模型')
    if provider not in item['sources']:
        raise ValueError(item.get('unavailable_sources', {}).get(provider, '此模型暂无该来源'))
    source = dict(item['sources'][provider])
    if provider == 'huggingface' and model_id == 'qwenimage21':
        # Same already-tested image snapshot for direct and mirror routes.
        source['revision'] = PINNED_HF[model_id]['revision']
    return source


def _https_host(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Only public HTTPS model sources are allowed')
    return p.hostname or ''


def open_https(url, timeout=30):
    host = _https_host(url)
    if host not in ('huggingface.co', 'hf-mirror.com', 'modelscope.cn', 'mirrors.sdu.edu.cn'):
        raise ValueError('Only reviewed model providers are supported')
    if host == 'mirrors.sdu.edu.cn' and not urlsplit(url).path.startswith('/ncnn_modelzoo/'):
        raise ValueError('Unexpected mirror directory')
    suffixes = {
        'huggingface.co': ('huggingface.co', 'hf.co', 'xethub.hf.co'),
        # The mirror can redirect to HF origin/CDN. This is reported to the UI;
        # it is not an application retry/fallback and does not guarantee speed.
        'hf-mirror.com': ('hf-mirror.com', 'huggingface.co', 'hf.co', 'xethub.hf.co'),
        'mirrors.sdu.edu.cn': ('mirrors.sdu.edu.cn',),
        'modelscope.cn': ('modelscope.cn', 'modelscope.ai', 'aliyuncs.com'),
    }[host]
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            dest = _https_host(newurl)
            if host == 'mirrors.sdu.edu.cn' and (dest != host or not urlsplit(newurl).path.startswith('/ncnn_modelzoo/')):
                raise ValueError('Mirror redirect escaped allowed directory')
            if not any(dest == suffix or dest.endswith('.' + suffix) for suffix in suffixes):
                raise ValueError('下载源跳转至未授权域名，已拒绝；没有自动更换下载源')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(Redirect(), urllib.request.HTTPSHandler(context=ctx))
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'LocalAgent-Desktop/0.4'}), timeout=timeout)


def read_metadata(url, opener, transport=None):
    with opener(url, timeout=20) as response:
        if transport is not None:
            transport.update(metadata_requested_host=_https_host(url), metadata_final_host=response_host(response, url))
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024: raise ValueError('Model metadata exceeded limit')
    value = json.loads(data)
    if not isinstance(value, dict): raise ValueError('Invalid provider response')
    return value


def resolve_manifest(model_id: str, provider='huggingface', *, download_route_id='direct', opener=open_https) -> dict:
    source = source_for(model_id, provider)
    route = download_route(provider, download_route_id)
    transport = {}
    if provider == 'sdu':
        from .native_models import manifest
        return manifest(model_id)
    repo, revision = source['repository'], source['revision']
    if provider == 'huggingface':
        meta = read_metadata(f'{route["endpoint"]}/api/models/{repo}/revision/{revision}?blobs=true', opener, transport)
        resolved = meta.get('sha')
        if not isinstance(resolved, str) or not re.fullmatch('[a-f0-9]{40}', resolved):
            raise ValueError('Missing immutable Hugging Face snapshot')
        if revision != 'main' and resolved != revision: raise ValueError('Unexpected model revision')
        revision = resolved
        members = meta.get('siblings')
    else:
        meta = read_metadata(f'https://modelscope.cn/api/v1/models/{repo}/repo/files?' + urlencode({'Revision':revision,'Recursive':'true'}), opener)
        if meta.get('Code') != 200 or meta.get('Success') is False: raise ValueError('ModelScope: ' + str(meta.get('Message', 'request failed')))
        members = meta.get('Data', {}).get('Files')
    if not isinstance(members, list) or len(members) > 5000: raise ValueError('Invalid model file list')
    files = []
    for entry in members:
        remote = entry.get('rfilename') if provider == 'huggingface' else entry.get('Path')
        if model_id in PROFILES:
            if remote not in QWEN_FILES: continue
            name = remote
        else:
            if not isinstance(remote, str) or not remote.startswith('qwenimage21/'): continue
            name = remote[len('qwenimage21/'):]
            if not name.endswith(('.bin', '.param', '.txt', '.f32')): continue
        safe_name(name)
        if provider == 'huggingface':
            lfs = entry.get('lfs') or {}
            size = entry.get('size', lfs.get('size'))
            checksum = lfs.get('sha256') or lfs.get('oid') or entry.get('blobId')
            algorithm = 'sha256' if lfs else 'git'
        else:
            if entry.get('Type') != 'blob': raise ValueError('Selected file is not a blob')
            size, checksum, algorithm = entry.get('Size'), entry.get('Sha256'), 'sha256'
        if type(size) is not int or not 0 < size < 100 * 1024**3: raise ValueError('Invalid model file size')
        if not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}' if algorithm == 'sha256' else '[a-f0-9]{40}', checksum):
            raise ValueError('Missing checksum: ' + name)
        if name == 'model.safetensors' and checksum != PROFILES[model_id]['weight_sha256']:
            raise ValueError('权重版本与已适配模型不匹配，已停止；不会下载其他模型替代')
        files.append({'name':name, 'remote':safe_name(remote), 'bytes':size, 'digest':checksum, 'algorithm':algorithm})
    names = {f['name'] for f in files}
    required = QWEN_FILES if model_id in PROFILES else IMAGE_REQUIRED
    if not required.issubset(names) or len(names) != len(files) or len(files) > 128:
        raise ValueError('Required model files missing or duplicated')
    if download_route_id == 'hf_mirror':
        verify_mirror_manifest(model_id, repo, revision, files)
    total = sum(f['bytes'] for f in files)
    return {'id':model_id, 'provider':provider, 'provider_name':PROVIDERS[provider], 'repository':repo, 'revision':revision,
        'download_route':download_route_id, 'download_route_name':route['name'] if provider == 'huggingface' else PROVIDERS[provider],
        'third_party_mirror':route['third_party'], **transport,
        'files':files, 'download_bytes':total, 'disk_required_bytes':total * (3 if model_id in PROFILES else 1) + 1024**3,
        'installed_estimate_bytes':total * (2 if model_id in PROFILES else 1)}


def file_url(manifest, item):
    # The caller uses an internal issued manifest, never raw client-supplied URLs.
    provider = manifest.get('provider', 'huggingface')
    source = source_for(manifest['id'], provider)
    route_id = manifest.get('download_route', 'direct')
    route = download_route(provider, route_id)
    if route_id == 'hf_mirror':
        verify_mirror_manifest(manifest['id'], manifest['repository'], manifest['revision'], manifest['files'])
        if item not in manifest['files']: raise ValueError('文件不属于已确认的镜像清单')
    if provider == 'sdu':
        from .native_models import download_url
        return download_url(manifest, item)
    if source['repository'] != manifest['repository']: raise ValueError('Repository differs from catalog')
    repo, revision = source['repository'], manifest['revision']
    if not re.fullmatch('[a-f0-9]{40}', revision): raise ValueError('Immutable revision required')
    remote = safe_name(item['remote'])
    if provider == 'modelscope':
        return f'https://modelscope.cn/api/v1/models/{repo}/repo?' + urlencode({'Revision':revision, 'FilePath':remote})
    return f'{route["endpoint"]}/{repo}/resolve/{revision}/{quote(remote, safe="/")}?download=true'
