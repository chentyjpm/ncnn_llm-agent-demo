#!/usr/bin/env python3
"""Freeze the app from same-run verified native packages on each target OS.

Packages both engines/probes, Office libraries, Python, H5 and the full macOS
Vulkan loader + MoltenVK runtime. Model weights require explicit consent.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.macos_runtime import prepare_runtime, verify_frozen_dependencies
NAMES = {'bridge': 'ncnn_agent_bridge', 'image': 'qwenimage-ncnn-vulkan'}


def run(argv):
    print('RUN', argv, flush=True)
    subprocess.run([str(a) for a in argv], check=True, cwd=ROOT, timeout=1800)


def unpack(archive: Path, dest: Path):
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        if sum(m.size for m in members) > 500 * 1024 * 1024:
            raise ValueError('Native package exceeds expected size')
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts or '\\' in m.name or ':' in m.name or not (m.isdir() or m.isfile()):
                raise ValueError('Unsafe native package member')
            target = dest.joinpath(*p.parts)
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as source, target.open('xb') as out:
                    shutil.copyfileobj(source, out)
                target.chmod(m.mode & 0o755)


def prepare(archives: Path, staging: Path):
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    info = {'commit': os.environ.get('GITHUB_SHA', 'local'), 'run_id': os.environ.get('GITHUB_RUN_ID', 'local'),
            'os': platform.system(), 'arch': platform.machine(), 'engines': {}, 'packages': [], 'signed': False,
            'scope': 'App, Python, Office and both engines/probes included; no model weights or vendor GPU drivers.'}
    for component, name in NAMES.items():
        matches = list(archives.rglob(component + '-*.tar.gz'))
        if len(matches) != 1: raise ValueError('Expected exactly one native archive for ' + component)
        unpack(matches[0], staging / 'engines')
        folder = staging / 'engines' / component
        source = json.loads((folder / 'BUILD_INFO.json').read_text(encoding='utf-8'))
        if source['commit'] != info['commit'] or source['run_id'] != info['run_id']:
            raise ValueError('Engine must be built in this exact workflow run/commit')
        if any(source['stages'][stage]['status'] != 'passed' for stage in ('configure', 'build', 'smoke')):
            raise ValueError('Engine has not passed native tests')
        file = folder / (name + ('.exe' if os.name == 'nt' else ''))
        sha = hashlib.sha256(file.read_bytes()).hexdigest()
        if sha != source['binary_sha256'] or sha != source['smoke_binary_sha256']:
            raise ValueError('Native executable checksum mismatch')
        probe = folder / ('ncnn_device_probe.exe' if os.name == 'nt' else 'ncnn_device_probe')
        probe_sha = hashlib.sha256(probe.read_bytes()).hexdigest()
        if probe_sha != source['probe_sha256']: raise ValueError('Vulkan probe checksum mismatch')
        info['engines']['llm' if component == 'bridge' else 'image'] = {'sha256': sha, 'probe_sha256': probe_sha, 'source': source}
    if platform.system() == 'Darwin':
        info['vulkan_runtime'] = prepare_runtime(staging)
    for package in ('pyinstaller', 'python-docx', 'openpyxl', 'python-pptx', 'numpy', 'lxml', 'Pillow',
                    'XlsxWriter', 'defusedxml', 'certifi', 'typing_extensions', 'et_xmlfile', 'psutil'):
        dist = importlib.metadata.distribution(package)
        info['packages'].append({'name': package, 'version': dist.version, 'license': dist.metadata.get('License', '')})
        for item in dist.files or []:
            lower = str(item).lower()
            if any(p in lower for p in ('license', 'copying', 'notice')) and str(item).endswith(('.txt','.md','.rst','LICENSE','COPYING','NOTICE','.MIT')):
                file = dist.locate_file(item)
                if file.is_file():
                    target = staging / 'licenses' / package / str(item).replace('/', '_')
                    target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(file, target)
    info['packages'].append({'name': 'CPython', 'version': platform.python_version(), 'license': 'PSF License'})
    (staging / 'licenses').mkdir(exist_ok=True)
    license_text = getattr(__import__('builtins'), 'license')
    license_text._Printer__setup()
    (staging / 'licenses/CPython-LICENSE.txt').write_text('\n'.join(license_text._Printer__lines), encoding='utf-8')
    for p in (Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt'):
        if p.is_file():
            shutil.copy2(p, staging / 'licenses/CPython-LICENSE.txt'); break
    for name in ('LICENSE', 'THIRD_PARTY.md', 'README.md'): shutil.copy2(ROOT / name, staging / name)
    (staging / 'BUNDLE.json').write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding='utf-8')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', type=Path, required=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    staging = ROOT / 'build/desktop-payload'
    reports = ROOT / 'reports/desktop'; reports.mkdir(parents=True, exist_ok=True)
    prepare(args.archives.resolve(), staging)
    name = 'LocalAgent'
    options = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', name,
               '--paths', str(ROOT), '--distpath', str(ROOT / 'dist/desktop'), '--workpath', str(ROOT / 'build/pyinstaller'),
               '--specpath', str(ROOT / 'build'), '--collect-data', 'docx', '--collect-data', 'pptx',
               '--collect-data', 'certifi', '--hidden-import', 'scripts.qwen05_export', '--exclude-module', 'torch',
               '--exclude-module', 'transformers', '--exclude-module', 'matplotlib', '--exclude-module', 'pandas', '--hidden-import', 'tkinter', '--hidden-import', 'psutil']
    for source, dest in [(ROOT / 'local_agent/webui', 'local_agent/webui'), (staging / 'engines', 'engines'),
                         (staging / 'licenses', 'licenses'), (staging / 'BUNDLE.json', '.'), (staging / 'LICENSE', '.'),
                         (staging / 'THIRD_PARTY.md', '.'), (ROOT / 'configs', 'configs')]:
        options += ['--add-data', str(source) + os.pathsep + dest]
    for component, binary_name in NAMES.items():
        engine = staging / 'engines' / component / (binary_name + ('.exe' if os.name == 'nt' else ''))
        options += ['--add-binary', str(engine) + os.pathsep + 'engines/' + component]
        probe = engine.with_name('ncnn_device_probe.exe' if os.name == 'nt' else 'ncnn_device_probe')
        options += ['--add-binary', str(probe) + os.pathsep + 'engines/' + component]
    if platform.system() == 'Darwin':
        for library in sorted((staging / 'engines/vulkan').glob('*.dylib')):
            options += ['--add-binary', str(library) + os.pathsep + 'engines/vulkan']
    if platform.system() in ('Windows', 'Darwin'): options += ['--windowed']
    if platform.system() == 'Darwin': options += ['--osx-bundle-identifier', 'io.localagent.workbench']
    run(options + [ROOT / 'packaging/launch.py'])
    bundle = ROOT / 'dist/desktop' / (name + '.app' if platform.system() == 'Darwin' else name)
    exe = bundle / ('Contents/MacOS/LocalAgent' if platform.system() == 'Darwin' else name + ('.exe' if os.name == 'nt' else ''))
    manifests = {p.resolve() for p in bundle.rglob('BUNDLE.json')}
    if len(manifests) != 1: raise RuntimeError('Expected one frozen bundle manifest')
    manifest = manifests.pop()
    frozen_info = json.loads(manifest.read_text(encoding='utf-8'))
    for component, binary_name in NAMES.items():
        kind = 'llm' if component == 'bridge' else 'image'
        executable = manifest.parent / 'engines' / component / (binary_name + ('.exe' if os.name == 'nt' else ''))
        probe = executable.with_name('ncnn_device_probe.exe' if os.name == 'nt' else 'ncnn_device_probe')
        record = frozen_info['engines'][kind]
        record['pre_freeze_sha256'] = record['sha256']; record['pre_freeze_probe_sha256'] = record['probe_sha256']
        record['sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
        record['probe_sha256'] = hashlib.sha256(probe.read_bytes()).hexdigest()
    if platform.system() == 'Darwin':
        dependencies = verify_frozen_dependencies(manifest.parent)
        (reports / 'macos-dependencies.json').write_text(json.dumps(dependencies, indent=2), encoding='utf-8')
        runtime = manifest.parent / 'engines/vulkan'
        frozen_info['vulkan_runtime']['frozen_hashes'] = {
            file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in runtime.iterdir() if file.is_file()}
        frozen_info['signing'] = 'ad-hoc resource seal only; not Developer ID signed or notarized'
    frozen_info['hash_note'] = 'Source hashes checked before staging/PyInstaller; final hashes include relocation/ad-hoc signatures.'
    manifest.write_text(json.dumps(frozen_info, indent=2, ensure_ascii=False), encoding='utf-8')
    if platform.system() == 'Darwin':
        # Updating BUNDLE.json invalidates the outer resource seal. Reseal only
        # the app (NOT --deep signing) so measured nested binaries remain intact.
        run(['codesign', '--force', '--sign', '-', '--timestamp=none', bundle])
        run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', bundle])
    run([exe, '--monitor-test', reports / 'native-monitor.json'])
    if not json.loads((reports / 'native-monitor.json').read_text(encoding='utf-8'))['ok']:
        raise RuntimeError('Frozen native dashboard acceptance failed')
    run([exe, '--self-test', reports / 'bundle-self-test.json'])
    if not json.loads((reports / 'bundle-self-test.json').read_text())['ok']:
        raise RuntimeError('Frozen app self-test failed')
    run([sys.executable, ROOT / 'scripts/test_installed_app.py', '--binary', exe, '--output', reports / 'installed-http.json'] +
        (['--with-model'] if platform.system() == 'Linux' else []))
    out = ROOT / 'dist/installers'; out.mkdir(parents=True, exist_ok=True)
    suffix = platform.system().lower() + '-' + platform.machine().lower()
    if os.name == 'nt':
        compiler = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6/ISCC.exe'
        if not compiler.is_file(): raise RuntimeError('Build runner needs Inno Setup 6; end users do not')
        run([compiler, '/DBundleDir=' + str(bundle), '/DOutputDir=' + str(out), ROOT / 'packaging/windows.iss'])
        with tempfile.TemporaryDirectory(prefix='local-agent-installed-') as tmp:
            target = Path(tmp) / 'Application'
            run([out / 'LocalAgent-windows-x64-setup.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR=' + str(target)])
            run([sys.executable, ROOT / 'scripts/test_installed_app.py', '--binary', target / 'LocalAgent.exe', '--output', reports / 'windows-installed-http.json'])
            run([target / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])
        shutil.make_archive(str(out / ('LocalAgent-' + suffix + '-portable')), 'zip', bundle.parent, bundle.name)
    elif platform.system() == 'Darwin':
        run(['hdiutil', 'create', '-volname', 'Local Agent', '-srcfolder', bundle, '-ov', '-format', 'UDZO', out / ('LocalAgent-' + suffix + '.dmg')])
    else:
        shutil.copy2(ROOT / 'packaging/install-linux.sh', bundle / 'install.sh'); (bundle / 'install.sh').chmod(0o755)
        with tarfile.open(out / ('LocalAgent-' + suffix + '.tar.gz'), 'w:gz') as tar: tar.add(bundle, arcname=bundle.name)
    sums = [hashlib.sha256(f.read_bytes()).hexdigest() + '  ' + f.name for f in sorted(out.iterdir()) if f.is_file()]
    (out / 'SHA256SUMS.txt').write_text('\n'.join(sums) + '\n', encoding='utf-8')
    (reports / 'build.json').write_text(json.dumps({'commit': os.getenv('GITHUB_SHA'), 'platform': suffix,
        'installers': [p.name for p in out.iterdir()], 'signed': False, 'models_bundled': False}, indent=2))
    print('INSTALLERS', out, flush=True)


if __name__ == '__main__': main()
