"""Stage a complete macOS Vulkan runtime; build helper, not user setup.

MoltenVK is the ICD, not a replacement filename for Vulkan-Loader. Native
artifacts can import @rpath/libvulkan.dylib even when their help path passed
on an SDK-equipped build runner. Copy both components and an app-local ICD.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def load_names(file: Path) -> list[str]:
    lines = subprocess.check_output(['otool', '-L', str(file)], text=True).splitlines()
    return [line.strip().split(' (', 1)[0] for line in lines[1:] if line.strip()]


def check_runtime_library(file: Path) -> None:
    # The first entry of otool -L for a dylib is its own install ID.
    for dependency in load_names(file)[1:]:
        if not dependency.startswith(('/usr/lib/', '/System/Library/')):
            raise RuntimeError('Unbundled Vulkan runtime dependency: ' + dependency)


def local_icd(source: Path) -> dict:
    if source.stat().st_size > 16384:
        raise ValueError('Oversized MoltenVK ICD manifest')
    original = json.loads(source.read_text(encoding='utf-8'))
    api = original.get('ICD', {}).get('api_version')
    if not isinstance(api, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', api):
        raise ValueError('Invalid MoltenVK ICD API version')
    return {'file_format_version': '1.0.1',
            'ICD': {'library_path': './libMoltenVK.dylib', 'api_version': api,
                    'is_portability_driver': True}}


def prepare_runtime(staging: Path) -> dict:
    runtime = staging / 'engines/vulkan'
    runtime.mkdir(parents=True, exist_ok=True)
    prefixes = {name: Path(subprocess.check_output(['brew', '--prefix', name], text=True).strip())
                for name in ('molten-vk', 'vulkan-loader')}
    sources = {'libMoltenVK.dylib': prefixes['molten-vk'] / 'lib/libMoltenVK.dylib',
               'libvulkan.dylib': prefixes['vulkan-loader'] / 'lib/libvulkan.dylib'}
    result = {'libraries': {}, 'versions': {}, 'native_relocations': [],
              'scope': 'Bundled loader + MoltenVK ICD. No system driver installation.'}
    for name, source in sources.items():
        if not source.is_file():
            raise RuntimeError('Build runner is missing runtime library: ' + str(source))
        target = runtime / name
        shutil.copy2(source.resolve(), target)
        check_runtime_library(target)
    # Support the standard loader soname for both linked and dlopen consumers.
    shutil.copy2(runtime / 'libvulkan.dylib', runtime / 'libvulkan.1.dylib')
    icd_source = prefixes['molten-vk'] / 'etc/vulkan/icd.d/MoltenVK_icd.json'
    if not icd_source.is_file():
        raise RuntimeError('Build runner is missing the MoltenVK ICD manifest')
    (runtime / 'MoltenVK_icd.json').write_text(json.dumps(local_icd(icd_source), indent=2) + '\n', encoding='utf-8')
    # Give PyInstaller an existing absolute dependency to discover and relocate.
    # Only staging copies change; the verified native archives are untouched.
    loader = str((runtime / 'libvulkan.dylib').resolve())
    for component, name in (('bridge', 'ncnn_agent_bridge'), ('image', 'qwenimage-ncnn-vulkan')):
        for filename in (name, 'ncnn_device_probe'):
            binary = staging / 'engines' / component / filename
            for old in load_names(binary):
                if re.fullmatch(r'libvulkan(?:\.[0-9]+)*\.dylib', Path(old).name):
                    subprocess.run(['install_name_tool', '-change', old, loader, str(binary)], check=True)
                    result['native_relocations'].append({'file': str(binary.relative_to(staging)), 'from': old,
                                                          'to': 'bundled engines/vulkan/libvulkan.dylib'})
    for target in runtime.glob('*.dylib'):
        result['libraries'][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    for name, prefix in prefixes.items():
        result['versions'][name] = subprocess.check_output(['brew', 'list', '--versions', name], text=True).strip()
        for source in prefix.rglob('*'):
            if source.is_file() and source.name.lower().startswith(('license', 'notice', 'copying')):
                target = staging / 'licenses' / name / source.relative_to(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    return result


def verify_frozen_dependencies(resources: Path) -> dict:
    """Reject SDK/Homebrew paths that would work only on a developer's Mac."""
    records = {}
    for component, name in (('bridge', 'ncnn_agent_bridge'), ('image', 'qwenimage-ncnn-vulkan')):
        for filename in (name, 'ncnn_device_probe'):
            file = resources / 'engines' / component / filename
            names = load_names(file)
            for dependency in names:
                if not dependency.startswith(('/usr/lib/', '/System/Library/', '@rpath/', '@loader_path/', '@executable_path/')):
                    raise RuntimeError('Nonportable frozen native dependency: ' + dependency)
            records[str(file.relative_to(resources))] = names
    for name in ('libvulkan.dylib', 'libvulkan.1.dylib', 'libMoltenVK.dylib', 'MoltenVK_icd.json'):
        if not (resources / 'engines/vulkan' / name).is_file():
            raise RuntimeError('Frozen bundle omitted Vulkan runtime: ' + name)
    return records
