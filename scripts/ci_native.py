#!/usr/bin/env python3
"""CI-only native builds. Real sources/binaries; no weights, fake model or GPU test.

Dependencies are checked out by GitHub Actions, NOT downloaded by this script.
Local configure/build uses the same commands when .ci-src/ contains those checkouts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = {
    'ncnn_llm': 'futz12/ncnn_llm', 'qwenimage': 'nihui/qwenimage-ncnn-vulkan',
    'ncnn': 'Tencent/ncnn', 'json': 'nlohmann/json',
}
TARGETS = {'bridge': 'ncnn_agent_bridge', 'image': 'qwenimage-ncnn-vulkan'}
SCOPE = 'Real C++ compilation/linking and model-free startup checks; NO model inference, image generation, GPU execution or Docker isolation test.'


def read_pins(root: Path = ROOT) -> dict:
    pins = json.loads((root / 'ci/dependencies.json').read_text(encoding='utf-8'))
    if set(pins) != set(REPOSITORIES):
        raise ValueError('Dependency lock keys do not match the configured repositories')
    for name, repo in REPOSITORIES.items():
        if pins[name]['repository'] != repo or not re.fullmatch(r'[0-9a-f]{40}', pins[name]['commit']):
            raise ValueError(f'Invalid pinned dependency: {name}')
    original = json.loads((root / 'upstream-lock.json').read_text(encoding='utf-8'))
    for name in ('ncnn_llm', 'qwenimage'):
        match = next(r for r in original['repositories'] if r['name'] == REPOSITORIES[name])
        if match['commit'] != pins[name]['commit']:
            raise ValueError(f'{name} differs from upstream-lock.json; update both deliberately')
    return pins


def configure_command(component: str, system: str, root: Path = ROOT) -> list[str]:
    deps = root / '.ci-src'
    source = root / 'native' if component == 'bridge' else deps / 'qwenimage/src'
    cmd = ['cmake', '-S', str(source), '-B', str(root / f'build/ci-{component}'),
           '-DCMAKE_BUILD_TYPE=Release', '-DNCNN_VULKAN=ON', '-DNCNN_SIMPLEVK=ON',
           '-DNCNN_OPENMP=OFF', '-DNCNN_SHARED_LIB=OFF', '-DBUILD_SHARED_LIBS=OFF',
           '-DCMAKE_POLICY_VERSION_MINIMUM=3.5']
    if system == 'Windows':
        # Initialize additional flags without replacing CMake's /EHsc defaults.
        cmd += ['-A', 'x64', '-DCMAKE_CXX_FLAGS_INIT=/utf-8 /MP2',
                '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded$<$<CONFIG:Debug>:Debug>']
    if component == 'bridge':
        cmd += [f'-DNCNN_LLM_SOURCE_DIR={deps / "ncnn_llm"}',
                f'-DAGENT_NCNN_SOURCE_DIR={deps / "ncnn"}',
                f'-DAGENT_JSON_SOURCE_DIR={deps / "json"}', '-DBUILD_TESTING=ON']
    elif component != 'image':
        raise ValueError('Unknown native component')
    return cmd


def find_binary(component: str, root: Path = ROOT, system: str | None = None) -> Path:
    system = system or platform.system()
    name = TARGETS[component] + ('.exe' if system == 'Windows' else '')
    build = root / f'build/ci-{component}'
    # Support both CMake single-config and Visual Studio multi-config generators.
    found = [p for p in (build / name, build / 'Release' / name) if p.is_file() and p.stat().st_size > 0]
    if len(found) != 1:
        raise RuntimeError(f'Expected exactly one real Release binary, found {found}')
    return found[0].resolve()


def run_logged(argv: list[str], log: Path, *, codes: tuple[int, ...] = (0,),
               contains: str | None = None, timeout: int = 2400) -> dict:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    print('ARGV:', json.dumps(argv), flush=True)
    with log.open('wb') as stream:
        stream.write(('ARGV: ' + json.dumps(argv) + '\n').encode('utf-8'))
        stream.flush()
        proc = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                              stdout=stream, stderr=subprocess.STDOUT,
                              timeout=timeout, check=False, shell=False)
    output = log.read_text(encoding='utf-8', errors='replace')
    # Keep Actions console readable; the artifact contains the complete log.
    print(output[-24000:], flush=True)
    if proc.returncode not in codes:
        raise RuntimeError(f'{log.name}: unexpected exit code {proc.returncode}; expected {codes}')
    # Only program output, not the ARGV header, can satisfy the smoke assertion.
    program_output = output.partition('\n')[2]
    if contains and contains not in program_output:
        raise RuntimeError(f'{log.name}: expected diagnostic was not emitted: {contains}')
    return {'argv': argv, 'returncode': proc.returncode, 'elapsed_seconds': round(time.monotonic() - started, 3)}


def validate_submodules(output: str) -> list[str]:
    """A successful git command does not imply initialized, pinned submodules."""
    lines = [line for line in output.splitlines() if line.strip()]
    for line in lines:
        if not re.fullmatch(r' [0-9a-f]{40} .+', line):
            raise RuntimeError(f'Uninitialized, mismatched, conflicted or malformed submodule: {line}')
    return lines


def load_state(path: Path, component: str, stage: str, env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    identity = {'component': component, 'commit': env.get('GITHUB_SHA', 'local-checkout'),
                'run_id': env.get('GITHUB_RUN_ID', 'local'),
                'run_attempt': env.get('GITHUB_RUN_ATTEMPT', 'local')}
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if stage != 'refs' and previous and all(previous.get(k) == v for k, v in identity.items()):
        if not isinstance(previous.get('stages'), dict):
            raise RuntimeError('Malformed CI stage state')
        return previous
    return {**identity, 'platform': platform.platform(), 'machine': platform.machine(),
            'scope': SCOPE, 'model_inference': 'not_run', 'gpu_execution': 'not_run',
            'stages': {}, 'old_evidence_discarded': bool(previous)}


def begin_stage(state: dict, stage: str) -> None:
    order = ('refs', 'configure', 'build', 'smoke', 'package')
    index = order.index(stage)
    for later in order[index + 1:]:
        state['stages'].pop(later, None)
    if stage in ('configure', 'build'):
        state.pop('built_binary_sha256', None)
    if stage in ('configure', 'build', 'smoke'):
        state.pop('smoke_binary_sha256', None)
        state.pop('startup_checks', None)
    for required in {'build': ('configure',), 'smoke': ('configure', 'build'),
                     'package': ('configure', 'build', 'smoke')}.get(stage, ()):
        if state['stages'].get(required, {}).get('status') != 'passed':
            raise RuntimeError(f'Cannot run {stage} without a successful {required} stage in this run')
    state['stages'][stage] = {'status': 'running'}


def checked_digest(binary: Path, state: dict, *, require_smoke: bool = False) -> str:
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != state.get('built_binary_sha256'):
        raise RuntimeError('Binary differs from the executable built in this run')
    if require_smoke and digest != state.get('smoke_binary_sha256'):
        raise RuntimeError('Binary differs from the executable tested in this run')
    return digest


def verify_sources(component: str, pins: dict, evidence: Path) -> dict:
    names = ('ncnn_llm', 'ncnn', 'json') if component == 'bridge' else ('qwenimage',)
    result = {}
    for name in names:
        directory = ROOT / '.ci-src' / name
        got = subprocess.check_output(['git', '-C', str(directory), 'rev-parse', 'HEAD'],
                                      text=True, timeout=30).strip()
        if got != pins[name]['commit']:
            raise RuntimeError(f'Dependency SHA mismatch for {name}: {got}')
        result[name] = {'repository': REPOSITORIES[name], 'commit': got}
        run_logged(['git', '-C', str(directory), 'submodule', 'status', '--recursive'],
                   evidence / f'submodules-{name}.log', timeout=60)
        status_text = (evidence / f'submodules-{name}.log').read_text(encoding='utf-8').partition('\n')[2]
        result[name]['submodules_verified'] = len(validate_submodules(status_text))
        dirty = subprocess.check_output(['git', '-C', str(directory), 'status', '--porcelain',
                                         '--untracked-files=no', '--ignore-submodules=none'],
                                        text=True, timeout=60).strip()
        if dirty:
            raise RuntimeError(f'Modified dependency checkout: {name}: {dirty}')
    if component == 'image':
        got = subprocess.check_output(['git', '-C', str(ROOT / '.ci-src/qwenimage/src/ncnn'),
                                       'rev-parse', 'HEAD'], text=True, timeout=30).strip()
        if got != pins['ncnn']['commit']:
            raise RuntimeError('Qwen Image embedded ncnn does not match CI dependency lock')
        result['ncnn'] = {'repository': REPOSITORIES['ncnn'], 'commit': got}
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['refs', 'configure', 'build', 'smoke', 'package', 'summary'])
    ap.add_argument('--component', choices=list(TARGETS), required=True)
    args = ap.parse_args()
    component, stage = args.component, args.stage
    evidence = ROOT / 'reports/ci' / component
    evidence.mkdir(parents=True, exist_ok=True)
    state_file = evidence / 'status.json'
    state = load_state(state_file, component, stage)
    build = ROOT / f'build/ci-{component}'
    if stage == 'summary':
        lines = [f'## Native {component}: {state["platform"]}', '', SCOPE, '']
        lines += [f'- {key}: {value["status"]}' for key, value in state['stages'].items()]
        if not state['stages']:
            lines.append('No native stage produced results; inspect checkout/setup logs.')
        text = '\n'.join(lines) + '\n'
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
                f.write(text)
        print(text)
        return 0
    try:
        begin_stage(state, stage)
        state_file.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        pins = read_pins()
        if stage == 'refs':
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
                    for name, info in pins.items():
                        f.write(f'{name}={info["commit"]}\n')
            print(json.dumps(pins, indent=2))
        elif stage == 'configure':
            state['dependencies'] = verify_sources(component, pins, evidence)
            run_logged(['cmake', '--version'], evidence / 'cmake-version.log', timeout=30)
            run_logged(configure_command(component, platform.system()), evidence / 'configure.log', timeout=600)
        elif stage == 'build':
            run_logged(['cmake', '--build', str(build), '--config', 'Release', '--parallel', '2',
                        '--target'] + (['bridge_cli_tests', 'bridge_exception_tests'] if component == 'bridge' else [])
                       + [TARGETS[component]],
                       evidence / 'build.log')
            state['binary'] = str(find_binary(component))
            state['built_binary_sha256'] = hashlib.sha256(Path(state['binary']).read_bytes()).hexdigest()
        elif stage == 'smoke':
            binary = str(find_binary(component))
            checked_digest(Path(binary), state)
            if component == 'bridge':
                run_logged(['ctest', '--test-dir', str(build), '-C', 'Release', '--output-on-failure',
                            '--no-tests=error'], evidence / 'ctest.log', timeout=120)
                missing = ROOT / 'build/ci-missing-model'
                if missing.exists():
                    raise RuntimeError('Missing-model test directory must not exist')
                run_logged([binary, '--model', str(missing)], evidence / 'missing-model.log',
                           codes=(2,), contains='--model must point to a converted ncnn model directory', timeout=30)
            else:
                run_logged([binary, '-h'], evidence / 'help.log',
                           contains='Usage: qwenimage-ncnn-vulkan', timeout=30)
            run_logged([sys.executable, str(ROOT / 'scripts/native_regression.py'),
                        '--component', component, '--binary', binary,
                        '--output', str(evidence / 'native-test-results.json')],
                       evidence / 'native-regression.log', timeout=300)
            state['smoke_binary_sha256'] = checked_digest(Path(binary), state)
            state['startup_checks'] = 'passed; no model was loaded'
        elif stage == 'package':
            for required in ('configure', 'build', 'smoke'):
                if state['stages'].get(required, {}).get('status') != 'passed':
                    raise RuntimeError(f'Cannot package without a successful {required} stage')
            binary = find_binary(component)
            state['binary_sha256'] = checked_digest(binary, state, require_smoke=True)
            out = ROOT / 'dist/ci'
            out.mkdir(parents=True, exist_ok=True)
            # A fresh staging directory prevents stale files entering a new artifact.
            package_dir = Path(tempfile.mkdtemp(prefix=component + '-payload-', dir=out))
            shutil.copy2(binary, package_dir / binary.name)
            metadata = json.loads(json.dumps(state))
            metadata['stages'].pop('package', None)
            metadata['package_note'] = 'Validated payload snapshot; final archive outcome is in the CI status.json.'
            (package_dir / 'BUILD_INFO.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
            (package_dir / 'README.txt').write_text(SCOPE + '\nCI binary; no weights bundled. OpenMP disabled.\n'
                'Not a portable release or a performance benchmark; system/GPU drivers may still be required.\n', encoding='utf-8')
            shutil.copy2(ROOT / 'THIRD_PARTY.md', package_dir / 'THIRD_PARTY.md')
            shutil.copy2(ROOT / 'LICENSE', package_dir / 'LICENSE')
            # Preserve upstream/submodule license notices with distributed static binaries.
            for source in (ROOT / '.ci-src').rglob('*'):
                if '.git' in source.parts or not source.is_file():
                    continue
                if source.name.lower().startswith(('license', 'copying', 'notice', 'copyright')):
                    target = package_dir / 'licenses' / source.relative_to(ROOT / '.ci-src')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            archive = out / f'{component}-{platform.system().lower()}-{platform.machine().lower()}.tar.gz'
            temporary_archive = archive.with_name(archive.name + '.tmp')
            with tarfile.open(temporary_archive, 'w:gz') as tar:
                tar.add(package_dir, arcname=component)
            # Retest the archived bytes, not the build-directory executable.
            with tempfile.TemporaryDirectory(prefix='archive-smoke-') as tmp:
                restored = Path(tmp) / binary.name
                with tarfile.open(temporary_archive, 'r:gz') as tar:
                    member = tar.getmember(f'{component}/{binary.name}')
                    if not member.isfile():
                        raise RuntimeError('Archive executable is not a regular file')
                    with tar.extractfile(member) as src:
                        restored.write_bytes(src.read())
                    restored.chmod(member.mode & 0o755)
                checked_digest(restored, state, require_smoke=True)
                run_logged([sys.executable, str(ROOT / 'scripts/native_regression.py'),
                            '--component', component, '--binary', str(restored),
                            '--output', str(evidence / 'packaged-native-test-results.json')],
                           evidence / 'packaged-regression.log', timeout=300)
            os.replace(temporary_archive, archive)
            shutil.rmtree(package_dir)
            state['archive'] = archive.name
            state['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        state['stages'][stage] = {'status': 'passed'}
        returncode = 0
    except Exception as exc:
        state['stages'][stage] = {'status': 'failed', 'error': str(exc)}
        print(f'ERROR: {exc}', file=sys.stderr, flush=True)
        returncode = 1
    state_file.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    return returncode


if __name__ == '__main__':
    raise SystemExit(main())
