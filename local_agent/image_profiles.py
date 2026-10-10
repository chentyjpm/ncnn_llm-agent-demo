"""Qwen Image variants: separate checkpoints and fixed Turbo scheduler."""
from pathlib import Path
from .paths import PolicyError

BASE_IMAGE = 'qwenimage21'
TURBO_IMAGE = 'qwenimage21-turbo'
TURBO_FILES = frozenset(f'transformer/{part}.ncnn.{ext}'
                       for part in ('input', 'blocks', 'output') for ext in ('param', 'bin'))


def image_profile(config: dict | None = None) -> dict:
    config = config or {}
    model = str(config.get('model') or '')
    name = Path(model).expanduser().resolve().name if model else ''
    turbo = name.lower().endswith('-turbo')
    return {'variant': 'turbo' if turbo else 'base', 'default_steps': 8 if turbo else 40,
            'fixed_steps': 8 if turbo else None,
            'scheduler': 'turbo-fixed-8' if turbo else 'base-dynamic',
            'requires_base': BASE_IMAGE if turbo else None}


def image_steps(config: dict | None, value=None) -> int:
    p = image_profile(config)
    steps = p['default_steps'] if value is None else value
    if type(steps) is not int or not 1 <= steps <= 100:
        raise PolicyError('steps must be in 1..100; four steps alone is not a LoRA accelerator')
    if p['fixed_steps'] is not None and steps != p['fixed_steps']:
        raise PolicyError('Qwen Image 2.1 Turbo 固定使用 8 步；不能使用 2、4 或 40 步，请刷新模型设置')
    return steps


def shared_components(base: Path, *, verify_hashes=False, cancelled=None) -> dict:
    """Check reviewed shared components; never copy or silently replace them.

    Quick runtime checks use file sizes. Installation/activation also hash every
    shared file. The application's existing single-writer requirement applies.
    """
    from .hf_download_pins import PINNED_HF
    from .model_sources import safe_name, digest
    base = Path(base).expanduser().resolve()
    pin = PINNED_HF[BASE_IMAGE]
    shared = [f for f in pin['files'] if not f['name'].startswith('transformer/')]
    if not shared:
        raise PolicyError('缺少基础模型共享组件的可信清单')
    for item in shared:
        if cancelled and cancelled():
            raise InterruptedError('已取消共享组件校验')
        p = base / safe_name(item['name'])
        current = base
        for part in p.relative_to(base).parts:
            current = current / part
            if current.is_symlink():
                raise PolicyError('共享组件不能包含符号链接')
        if not p.is_file() or p.stat().st_size != item['bytes']:
            raise PolicyError('请先完整安装 Qwen Image 2.1 基础模型，共享组件缺失或大小不符：' + item['name'])
        if verify_hashes and digest(p, item['algorithm'], cancelled) != item['digest']:
            raise PolicyError('基础模型共享组件校验失败：' + item['name'])
    return {'id': BASE_IMAGE, 'revision': pin['revision'], 'files': len(shared),
            'shared_bytes': sum(f['bytes'] for f in shared), 'copied_bytes': 0,
            'hashes_verified': verify_hashes}
