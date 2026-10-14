"""Opt-in image question validation. Protocol plumbing, not model validation."""
import json
import struct
from pathlib import Path
from .paths import PolicyError, Workspace


def is_minicpm(config):
    try:
        path=Path(config['model'])/'model.json'
        if path.stat().st_size>1024**2: return False
        model=json.loads(path.read_text(encoding='utf-8-sig'))
        return model.get('setting',{}).get('vision',{}).get('type')=='minicpm_v4_7'
    except (OSError,ValueError,KeyError,TypeError,AttributeError): return False


def enabled(config):
    return config.get('experimental_vision') is True and config.get('backend')=='ncnn_bridge' and is_minicpm(config)


def image_path(workspace: Workspace, relative: str) -> Path:
    p = workspace.path(relative)
    if not p.is_file() or not 0 < p.stat().st_size <= 8 * 1024 * 1024:
        raise PolicyError('Vision image must be a regular file of 1 byte to 8 MiB')
    with p.open("rb") as f:
        data = f.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise PolicyError("Vision image exceeds 8 MiB")
    width = height = 0
    if p.suffix.lower() == '.png' and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24 and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
    elif p.suffix.lower() in ('.jpg', '.jpeg') and data.startswith(b'\xff\xd8'):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]; offset += 1
            if marker in (0xD9, 0xDA):
                break
            if marker in (0x01, *range(0xD0, 0xD9)):
                continue
            if offset + 2 > len(data):
                break
            size = int.from_bytes(data[offset:offset+2], 'big')
            if size < 2 or offset + size > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2) and size >= 8:
                height, width = struct.unpack('>HH', data[offset+3:offset+7]); break
            offset += size
    if not width or not height or width * height > 16_000_000 or max(width, height) > 16384:
        raise PolicyError('Vision accepts bounded PNG/JPEG images only (at most 16 megapixels)')
    return p
